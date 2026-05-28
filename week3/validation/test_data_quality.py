"""
Week 3 — Data Quality Validator Tests

Pytest suite for week3/validation/check_data_quality.py.

Covers Part 6 of week3/README.md:
  - Baseline data passes validation (TestBaselineData)
  - Corrupted data fails, each of 4 issues detected separately (TestDataQualityIssues)
  - Validator doesn't crash on weird inputs (TestGracefulDegradation)
  - End-to-end integration: validate_data, fail-on variations, class shim parity (TestIntegration)

Design notes:
  - Real data for fixtures (per user choice): one parquet load per session via
    session-scoped fixtures, cached for all tests. Run time ~3-5 sec.
  - clean_slice is a 6-day window (2026-01-01 to 2026-01-06): truly clean -
    before the Jan 7 holiday-flag corruption, includes New Year's Day correctly
    flagged. Used in TestBaselineData because the literal pre-cutoff baseline
    contains the same Jan 7-15 corruption bleed documented in NOTES.md.
  - Robustness tests use small synthetic DataFrames because they verify the
    crash-resistance property under malformed inputs, not data correctness.

Run from repo root:
  PYTHONPATH=week3 .venv/bin/python -m pytest week3/validation/test_data_quality.py -v

Expected: 13 passed.
"""

import pytest
import pandas as pd
import numpy as np
from pathlib import Path

from validation.check_data_quality import (
    detect_trip_count_outliers,
    detect_duplicates,
    detect_stuck_feature,
    detect_holiday_overflag,
    validate_data,
    DataQualityValidator,
)


# Configuration ---------------------------------------------------------------

PATH = Path(__file__).parent.parent / "data" / "demand_enriched_corrupted.parquet"
CUTOFF = pd.Timestamp("2026-01-16")


# Fixtures (session-scoped -> one parquet load per session) ------------------

@pytest.fixture(scope="session")
def corrupted_parquet():
    """The full parquet, loaded once per session."""
    return pd.read_parquet(PATH)


@pytest.fixture(scope="session")
def baseline_df(corrupted_parquet):
    """Pre-cutoff slice (< 2026-01-16). Contains Jan 7-15 corruption bleed
    (see NOTES.md late finding); used as baseline reference for detector
    comparison strings, not as 'clean' data."""
    return corrupted_parquet[corrupted_parquet["time_bucket"] < CUTOFF].copy()


@pytest.fixture(scope="session")
def corrupt_df(corrupted_parquet):
    """Post-cutoff slice (>= 2026-01-16). The 'under validation' window."""
    return corrupted_parquet[corrupted_parquet["time_bucket"] >= CUTOFF].copy()


@pytest.fixture(scope="session")
def clean_slice(corrupted_parquet):
    """Truly-clean 6-day window: 2026-01-01 through 2026-01-06.
    Sits before the Jan 7 holiday corruption bleed. Includes
    New Year's Day (Jan 1), which IS in US_FEDERAL_2026 so the holiday
    detector won't false-positive."""
    df = corrupted_parquet
    start = pd.Timestamp("2026-01-01")
    end   = pd.Timestamp("2026-01-07")  # exclusive
    return df[(df["time_bucket"] >= start) & (df["time_bucket"] < end)].copy()


@pytest.fixture(scope="session")
def validator(baseline_df):
    """DataQualityValidator pre-configured with baseline reference."""
    return DataQualityValidator(baseline_df=baseline_df)


# TestBaselineData ------------------------------------------------------------

class TestBaselineData:
    """Clean baseline data should pass validation with no issues."""

    def test_clean_baseline_passes(self, clean_slice):
        """validate_data on the truly-clean 6-day window returns is_valid=True."""
        result = validate_data(clean_slice, clean_slice, fail_on="any")
        assert result["is_valid"] is True, (
            f"expected pass on clean slice, got {result['total_issues']} issues: "
            f"{[i['type'] for i in result['issues']]}"
        )
        assert result["total_issues"] == 0
        assert result["issues_by_severity"]["critical"] == 0
        assert result["issues_by_severity"]["high"] == 0


# TestDataQualityIssues -------------------------------------------------------

class TestDataQualityIssues:
    """Each of the 4 detectors fires on the corrupt slice with expected counts."""

    def test_detects_trip_count_outliers(self, corrupt_df, baseline_df):
        """353 negative + 147 sentinel(99999) + 164 extreme(>500) = 664 rows."""
        issues = detect_trip_count_outliers(corrupt_df, baseline_df)
        assert len(issues) == 1
        issue = issues[0]
        assert issue["type"] == "trip_count_outlier"
        assert issue["severity"] == "critical"
        assert issue["column"] == "trip_count"
        assert issue["rows_affected"] == 664
        assert issue["negative_count"] == 353
        assert issue["sentinel_count"] == 147
        assert issue["extreme_count"] == 164

    def test_detects_natural_key_duplicates(self, corrupt_df, baseline_df):
        """10,085 duplicate (time_bucket, PULocationID) keys in the corrupt window."""
        issues = detect_duplicates(corrupt_df, baseline_df)
        assert len(issues) == 1
        issue = issues[0]
        assert issue["type"] == "duplicate_natural_key"
        assert issue["severity"] == "high"
        assert issue["rows_affected"] == 10_085
        assert "time_bucket" in issue["key_cols"]
        assert "PULocationID" in issue["key_cols"]

    def test_detects_stuck_feature(self, corrupt_df, baseline_df):
        """cbd_pricing_active stuck at 1 for all 250,853 corrupt rows."""
        issues = detect_stuck_feature(corrupt_df, baseline_df)
        assert len(issues) == 1
        issue = issues[0]
        assert issue["type"] == "stuck_feature"
        assert issue["severity"] == "high"
        assert issue["column"] == "cbd_pricing_active"
        assert issue["rows_affected"] == 250_853
        assert issue["stuck_value"] == 1

    def test_detects_holiday_over_flag(self, corrupt_df, baseline_df):
        """5 non-holiday dates flagged + 2 real US federal holidays correctly recognized."""
        issues = detect_holiday_overflag(corrupt_df, baseline_df)
        assert len(issues) == 1
        issue = issues[0]
        assert issue["type"] == "holiday_over_flag"
        assert issue["severity"] == "high"
        assert issue["bad_dates"] == [
            "2026-01-16", "2026-01-17", "2026-01-18", "2026-01-20", "2026-01-21",
        ]
        assert issue["good_dates"] == ["2026-01-19", "2026-02-16"]


# TestGracefulDegradation -----------------------------------------------------

class TestGracefulDegradation:
    """validate_data should never raise on weird/malformed inputs."""

    def test_empty_dataframe_does_not_crash(self):
        result = validate_data(pd.DataFrame())
        assert isinstance(result, dict)
        assert result["total_issues"] == 0
        assert result["is_valid"] is True

    def test_dataframe_missing_time_bucket_does_not_crash(self):
        df = pd.DataFrame({
            "PULocationID": [1, 2, 3],
            "trip_count": [10, 20, 30],
        })
        result = validate_data(df)
        assert isinstance(result, dict)
        # detectors needing time_bucket return []; trip_count detector also returns []
        # (values are positive, no sentinels, not extreme)
        assert "issues" in result
        assert result["total_issues"] == 0

    def test_dataframe_missing_trip_count_does_not_crash(self):
        df = pd.DataFrame({
            "time_bucket": pd.to_datetime(["2026-01-20", "2026-01-21"]),
            "PULocationID": [1, 2],
        })
        result = validate_data(df)
        assert isinstance(result, dict)
        # trip_count detector returns []; duplicates checks 2 distinct rows, returns []
        assert result["total_issues"] == 0

    def test_all_nan_cbd_does_not_crash(self):
        df = pd.DataFrame({
            "time_bucket": pd.to_datetime(["2026-01-20", "2026-01-21"]),
            "PULocationID": [1, 2],
            "cbd_pricing_active": [np.nan, np.nan],
        })
        result = validate_data(df)
        assert isinstance(result, dict)
        # detect_stuck_feature sees only NaN, drops to empty, returns []
        assert result["total_issues"] == 0


# TestIntegration -------------------------------------------------------------

class TestIntegration:
    """End-to-end on real data - what the CLI and data.py startup hook see."""

    def test_validate_data_finds_4_issues_with_correct_severities(
        self, corrupt_df, baseline_df
    ):
        result = validate_data(corrupt_df, baseline_df)
        assert result["total_issues"] == 4
        assert result["is_valid"] is False
        assert result["issues_by_severity"]["critical"] == 1
        assert result["issues_by_severity"]["high"] == 3
        types = sorted(i["type"] for i in result["issues"])
        assert types == [
            "duplicate_natural_key",
            "holiday_over_flag",
            "stuck_feature",
            "trip_count_outlier",
        ]

    def test_fail_on_critical_marks_invalid(self, corrupt_df, baseline_df):
        result = validate_data(corrupt_df, baseline_df, fail_on="critical")
        assert result["is_valid"] is False

    def test_fail_on_any_marks_invalid(self, corrupt_df, baseline_df):
        result = validate_data(corrupt_df, baseline_df, fail_on="any")
        assert result["is_valid"] is False

    def test_class_shim_matches_direct_call(self, corrupt_df, baseline_df, validator):
        direct = validate_data(corrupt_df, baseline_df, fail_on="critical")
        shim   = validator.validate(corrupt_df)
        # Drop timing fields that legitimately vary between runs
        for k in ("checked_at", "duration_seconds"):
            direct.pop(k, None)
            shim.pop(k, None)
        assert direct == shim, "class shim output disagrees with validate_data"
