"""
Week 3 — Data Quality Validator

Detects 4 data-quality issues in a parquet that's been split into a baseline
window (pre-cutoff) and an evaluation window (post-cutoff):

  1. trip_count_outlier      negative, sentinel 99999, or extreme values
  2. duplicate_natural_key   same (time_bucket, PULocationID) appearing twice+
  3. stuck_feature           a feature that doesn't vary in the eval window
  4. holiday_over_flag       is_holiday=1 on dates that aren't US federal holidays

CLI:
  python -m validation.check_data_quality \\
    --input <parquet> --baseline-cutoff <ISO date> \\
    [--output validation_results.json] [--fail-on critical|high|medium|low|any]

Exit codes:
  0 - passed (no issues at severity >= --fail-on)
  1 - failed (one or more issues at severity >= --fail-on)
  2 - internal error (file missing, malformed parquet, missing required column)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

# Constants ------------------------------------------------------------------

NATURAL_KEY_COLS = ("time_bucket", "PULocationID")
DEFAULT_TRIP_COUNT_SENTINEL = 99999
DEFAULT_TRIP_COUNT_HARD_MAX = 500

# US federal holidays in 2026 (extend as needed for other years)
US_FEDERAL_2026: dict[date, str] = {
    date(2026, 1, 1):  "New Year's Day",
    date(2026, 1, 19): "MLK Day",
    date(2026, 2, 16): "Presidents Day",
    date(2026, 5, 25): "Memorial Day",
    date(2026, 6, 19): "Juneteenth",
    date(2026, 7, 3):  "Independence Day (observed)",
    date(2026, 9, 7):  "Labor Day",
    date(2026, 10, 12): "Columbus Day",
    date(2026, 11, 11): "Veterans Day",
    date(2026, 11, 26): "Thanksgiving",
    date(2026, 12, 25): "Christmas Day",
}

SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3, "critical": 4}


# Detectors ------------------------------------------------------------------

def detect_trip_count_outliers(
    corrupt_df: pd.DataFrame,
    baseline_df: Optional[pd.DataFrame] = None,
    *,
    sentinel: int = DEFAULT_TRIP_COUNT_SENTINEL,
    hard_max: int = DEFAULT_TRIP_COUNT_HARD_MAX,
    allow_negative: bool = False,
) -> list[dict]:
    """Detect trip_count rows that violate domain semantics.

    Three sub-patterns are reported in a single issue:
      - negative_count: trip_count < 0
      - sentinel_count: trip_count == sentinel
      - extreme_count : trip_count > hard_max and != sentinel
    """
    col = "trip_count"
    if col not in corrupt_df.columns:
        return []

    neg = 0 if allow_negative else int((corrupt_df[col] < 0).sum())
    sent = int((corrupt_df[col] == sentinel).sum())
    extreme = int(((corrupt_df[col] > hard_max) & (corrupt_df[col] != sentinel)).sum())
    total = neg + sent + extreme
    if total == 0:
        return []

    parts: list[str] = []
    if neg:
        parts.append(f"{neg:,} negative")
    if sent:
        parts.append(f"{sent:,} sentinel({sentinel})")
    if extreme:
        parts.append(f"{extreme:,} extreme(>{hard_max})")
    desc = " + ".join(parts) + f" out of {len(corrupt_df):,} evaluation rows"

    if baseline_df is not None and col in baseline_df.columns and len(baseline_df) > 0:
        b_max = int(baseline_df[col].max())
        desc += f"; baseline max was {b_max:,}"

    return [{
        "type": "trip_count_outlier",
        "severity": "critical",
        "column": col,
        "rows_affected": total,
        "description": desc,
        "negative_count": neg,
        "sentinel_count": sent,
        "extreme_count": extreme,
    }]


def detect_duplicates(
    corrupt_df: pd.DataFrame,
    baseline_df: Optional[pd.DataFrame] = None,
    *,
    key_cols: tuple[str, ...] = NATURAL_KEY_COLS,
) -> list[dict]:
    """Detect duplicate rows on the natural key (zone x time slot)."""
    keys = list(key_cols)
    if any(c not in corrupt_df.columns for c in keys):
        return []

    extras = int(corrupt_df.duplicated(subset=keys).sum())
    if extras == 0:
        return []

    desc = (
        f"{extras:,} duplicate row(s) on natural key {keys} "
        f"in {len(corrupt_df):,} evaluation rows"
    )
    if baseline_df is not None and all(c in baseline_df.columns for c in keys):
        b_extras = int(baseline_df.duplicated(subset=keys).sum())
        desc += f"; baseline had {b_extras:,}"

    return [{
        "type": "duplicate_natural_key",
        "severity": "high",
        "column": ", ".join(keys),
        "rows_affected": extras,
        "description": desc,
        "key_cols": keys,
    }]


def detect_stuck_feature(
    corrupt_df: pd.DataFrame,
    baseline_df: Optional[pd.DataFrame] = None,
    *,
    feature: str = "cbd_pricing_active",
    min_baseline_unique: int = 2,
) -> list[dict]:
    """Detect a feature stuck at one value in the eval window while varying in
    the baseline (or, with no baseline, any feature stuck across >1 eval rows)."""
    if feature not in corrupt_df.columns or len(corrupt_df) <= 1:
        return []

    unique = corrupt_df[feature].dropna().unique()
    if len(unique) > 1 or len(unique) == 0:
        return []
    stuck_value = unique[0]

    b_unique_str = ""
    if baseline_df is not None and feature in baseline_df.columns:
        b_unique = int(baseline_df[feature].dropna().nunique())
        if b_unique < min_baseline_unique:
            return []  # baseline also lacks variation - feature is constant by design
        b_unique_str = f"; baseline had {b_unique} distinct values"

    return [{
        "type": "stuck_feature",
        "severity": "high",
        "column": feature,
        "rows_affected": int(len(corrupt_df)),
        "description": (
            f"`{feature}` stuck at {stuck_value!r} for all {len(corrupt_df):,} "
            f"evaluation rows{b_unique_str}"
        ),
        "stuck_value": stuck_value.item() if hasattr(stuck_value, "item") else stuck_value,
    }]


def detect_holiday_overflag(
    corrupt_df: pd.DataFrame,
    baseline_df: Optional[pd.DataFrame] = None,
    *,
    us_federal_holidays: dict[date, str] = US_FEDERAL_2026,
    holiday_col: str = "is_holiday",
    time_col: str = "time_bucket",
) -> list[dict]:
    """Detect is_holiday=1 set on dates that are not US federal holidays."""
    if holiday_col not in corrupt_df.columns or time_col not in corrupt_df.columns:
        return []

    flagged = corrupt_df[corrupt_df[holiday_col] == 1]
    if flagged.empty:
        return []

    flagged_dates = sorted(flagged[time_col].dt.date.unique().tolist())
    real = set(us_federal_holidays.keys())
    bad_dates = [d for d in flagged_dates if d not in real]
    if not bad_dates:
        return []

    bad_mask = (corrupt_df[holiday_col] == 1) & (corrupt_df[time_col].dt.date.isin(bad_dates))
    rows_affected = int(bad_mask.sum())
    good_dates = [d for d in flagged_dates if d in real]

    return [{
        "type": "holiday_over_flag",
        "severity": "high",
        "column": holiday_col,
        "rows_affected": rows_affected,
        "description": (
            f"`{holiday_col}=1` on {len(bad_dates)} non-holiday date(s): "
            f"{[d.isoformat() for d in bad_dates]}; "
            f"affecting {rows_affected:,} rows"
        ),
        "bad_dates": [d.isoformat() for d in bad_dates],
        "good_dates": [d.isoformat() for d in good_dates],
    }]


# Orchestrator ---------------------------------------------------------------

def _any_at_or_above(issues: list[dict], fail_on: str) -> bool:
    if not issues:
        return False
    if fail_on == "any":
        return True
    threshold = SEVERITY_RANK.get(fail_on)
    if threshold is None:
        return False
    return any(SEVERITY_RANK.get(i.get("severity"), 0) >= threshold for i in issues)


def validate_data(
    corrupt_df: pd.DataFrame,
    baseline_df: Optional[pd.DataFrame] = None,
    *,
    input_path: Optional[str] = None,
    baseline_cutoff: Optional[str] = None,
    fail_on: str = "critical",
) -> dict:
    """Run all detectors and return a result dict matching the frozen schema."""
    started = time.time()
    issues: list[dict] = []
    issues += detect_trip_count_outliers(corrupt_df, baseline_df)
    issues += detect_duplicates(corrupt_df, baseline_df)
    issues += detect_stuck_feature(corrupt_df, baseline_df)
    issues += detect_holiday_overflag(corrupt_df, baseline_df)

    sev_counts = {s: 0 for s in ("critical", "high", "medium", "low")}
    for i in issues:
        sev = i.get("severity")
        if sev in sev_counts:
            sev_counts[sev] += 1

    is_valid = not _any_at_or_above(issues, fail_on)

    return {
        "schema_version": "1.0",
        "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "input": str(input_path) if input_path is not None else None,
        "baseline_cutoff": str(baseline_cutoff) if baseline_cutoff is not None else None,
        "baseline_rows": int(len(baseline_df)) if baseline_df is not None else 0,
        "evaluation_rows": int(len(corrupt_df)),
        "is_valid": is_valid,
        "fail_on": fail_on,
        "total_issues": len(issues),
        "issues_by_severity": sev_counts,
        "issues": issues,
        "duration_seconds": round(time.time() - started, 3),
    }


# Class shim (preserves README's Part 5 example API) -------------------------

class DataQualityValidator:
    """Thin wrapper around `validate_data` to match the template/README API."""

    def __init__(
        self,
        baseline_df: Optional[pd.DataFrame] = None,
        fail_on: str = "critical",
    ):
        self.baseline = baseline_df
        self.fail_on = fail_on

    def validate(self, df: pd.DataFrame) -> dict:
        return validate_data(df, self.baseline, fail_on=self.fail_on)


# CLI ------------------------------------------------------------------------

def _split_by_cutoff(
    df: pd.DataFrame,
    cutoff_str: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if "time_bucket" not in df.columns:
        raise ValueError("input parquet is missing required column 'time_bucket'")
    cutoff = pd.Timestamp(cutoff_str)
    baseline = df[df["time_bucket"] < cutoff]
    corrupt = df[df["time_bucket"] >= cutoff]
    return baseline, corrupt


def _main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m validation.check_data_quality",
        description="Data-quality validator for week 3 corrupted parquet.",
    )
    p.add_argument("--input", required=True, help="Path to parquet to validate")
    p.add_argument(
        "--baseline-cutoff", required=True,
        help="ISO date; rows < cutoff are baseline, rows >= are under validation",
    )
    p.add_argument(
        "--output", default="validation_results.json",
        help="Where to write the JSON report (default: %(default)s)",
    )
    p.add_argument(
        "--fail-on", default="critical",
        choices=["critical", "high", "medium", "low", "any"],
        help="Min severity that triggers exit non-zero (default: %(default)s)",
    )
    args = p.parse_args(argv)

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: input not found: {args.input}", file=sys.stderr)
        return 2

    try:
        df = pd.read_parquet(input_path)
    except Exception as e:
        print(f"ERROR: failed to read parquet {args.input}: {e}", file=sys.stderr)
        return 2

    try:
        baseline, corrupt = _split_by_cutoff(df, args.baseline_cutoff)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    result = validate_data(
        corrupt,
        baseline,
        input_path=args.input,
        baseline_cutoff=args.baseline_cutoff,
        fail_on=args.fail_on,
    )

    Path(args.output).write_text(json.dumps(result, indent=2, default=str))

    icon = "PASS" if result["is_valid"] else "FAIL"
    sev_str = ", ".join(
        f"{n} {k}" for k, n in result["issues_by_severity"].items() if n
    ) or "none"
    print(f"[{icon}] {result['total_issues']} issues ({sev_str}) - see {args.output}")

    return 0 if result["is_valid"] else 1


if __name__ == "__main__":
    sys.exit(_main())
