"""
Load the baseline and current data, run the monitoring metrics, write a
metrics-*.json report, and emit an `alert` output that the monitoring workflow
gates the drift-alert issue on. A critical-severity threshold breach sets
alert=true; otherwise the run stays green and no issue is opened.
"""

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from metric_template import MetricComputer

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Critical thresholds (BASELINE_METRICS.md) - a breach here pages ops.
NULL_RATE_CRITICAL = 0.01        # >1% nulls in any column
DUPLICATE_RATE_CRITICAL = 0.005  # >0.5% duplicate rows
PSI_CRITICAL = 0.25              # significant distribution shift
ACCURACY_CRITICAL = 0.80         # overall / any-zone accuracy floor


def load_data():
    """Baseline (Jan 1-15) and the current window under watch (Feb 2-28, 2026)."""
    baseline = pd.read_parquet(DATA_DIR / "demand_enriched_baseline.parquet")
    w4 = pd.read_parquet(DATA_DIR / "demand_enriched_week4.parquet")
    w4["time_bucket"] = pd.to_datetime(w4["time_bucket"])
    current = w4[(w4["time_bucket"] >= "2026-02-02") & (w4["time_bucket"] < "2026-03-01")]
    return baseline, current


def evaluate_alerts(metrics: dict) -> list:
    """
    Return the list of CRITICAL breaches (empty = healthy).

    Magnitude/quality signals drive the page; the KS p-value and freshness are
    reported in the metrics but kept out of the gate (KS over-fires on large
    samples, and freshness here reflects the static snapshot).
    """
    breaches = []
    for col, rate in metrics.get("null_rates", {}).items():
        if rate > NULL_RATE_CRITICAL:
            breaches.append(f"null rate {col}={rate:.3f} > {NULL_RATE_CRITICAL}")
    dup = metrics.get("duplicate_rate", {}).get("rate", 0.0)
    if dup > DUPLICATE_RATE_CRITICAL:
        breaches.append(f"duplicate rate {dup:.3f} > {DUPLICATE_RATE_CRITICAL}")
    if metrics.get("psi", 0.0) > PSI_CRITICAL:
        breaches.append(f"PSI {metrics['psi']:.3f} > {PSI_CRITICAL}")
    if "accuracy" in metrics and metrics["accuracy"] < ACCURACY_CRITICAL:
        breaches.append(f"accuracy {metrics['accuracy']:.3f} < {ACCURACY_CRITICAL}")
    for zone, acc in metrics.get("accuracy_by_zone", {}).items():
        if acc < ACCURACY_CRITICAL:
            breaches.append(f"zone {zone} accuracy {acc:.3f} < {ACCURACY_CRITICAL}")
    return breaches


def main():
    baseline, current = load_data()
    print(f"baseline rows: {len(baseline):,} | current rows: {len(current):,}")

    # Data-driven metrics. Accuracy metrics (1/2/6) need a predictions source and
    # are skipped here until one is wired in.
    metrics = MetricComputer(baseline).compute_all_metrics(current)
    breaches = evaluate_alerts(metrics)
    alert = len(breaches) > 0

    report = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "baseline_rows": len(baseline),
        "current_rows": len(current),
        "metrics": metrics,
        "alert": alert,
        "breaches": breaches,
    }
    print(json.dumps(report, indent=2, default=str))

    out_path = DATA_DIR.parent / "metrics-latest.json"
    out_path.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nwrote {out_path}  | alert={alert}")

    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a") as f:
            f.write(f"alert={'true' if alert else 'false'}\n")


if __name__ == "__main__":
    main()
