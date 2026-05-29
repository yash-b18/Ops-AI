import pandas as pd
import numpy as np
from pathlib import Path

# Configuration ---------------------------------------------------------------
PATH = Path(__file__).parent / "data" / "demand_enriched_corrupted.parquet"
CUTOFF = pd.Timestamp("2026-01-16")
KEY_COLS = ["time_bucket", "PULocationID"]


def section(title: str) -> None:
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


def summarize_shape(df, baseline, corrupt):
    section("OVERALL SHAPE")
    print(f"Total rows : {len(df):,}")
    print(f"Columns    : {len(df.columns)}")
    print(f"time_bucket: {df['time_bucket'].min()}  ->  {df['time_bucket'].max()}")
    print(f"\nBaseline  (< {CUTOFF.date()}):  {len(baseline):>10,} rows")
    print(f"Corrupted (>= {CUTOFF.date()}): {len(corrupt):>10,} rows")


def null_rates(baseline, corrupt):
    section("NULL RATES (%)")
    print(f"{'column':32s}{'baseline':>10s}{'corrupt':>10s}{'delta':>10s}")
    print("-" * 62)
    for c in baseline.columns:
        bp = baseline[c].isna().mean() * 100
        cp = corrupt[c].isna().mean() * 100
        flag = "  <-- !!!" if abs(cp - bp) > 1 else ""
        print(f"{c!s:32s}{bp:>9.3f}%{cp:>9.3f}%{cp-bp:>+9.3f}%{flag}")


def numeric_ranges(baseline, corrupt):
    section("NUMERIC COLUMN RANGES")
    for c in baseline.select_dtypes(include=[np.number]).columns:
        b = baseline[c].dropna()
        co = corrupt[c].dropna()
        if len(b) == 0 or len(co) == 0:
            continue
        print(f"\n  {c}:")
        print(f"    baseline:  min={b.min():>14g}  max={b.max():>14g}  "
              f"mean={b.mean():>12.4f}  std={b.std():>12.4f}")
        print(f"    corrupted: min={co.min():>14g}  max={co.max():>14g}  "
              f"mean={co.mean():>12.4f}  std={co.std():>12.4f}")


def issue_1_trip_count_outliers(corrupt):
    section("ISSUE 1 - trip_count outliers (target variable)")
    neg = (corrupt["trip_count"] < 0).sum()
    sent = (corrupt["trip_count"] == 99999).sum()
    huge = ((corrupt["trip_count"] > 500) & (corrupt["trip_count"] != 99999)).sum()
    print(f"  trip_count < 0       : {neg:>6,} rows")
    print(f"  trip_count == 99999  : {sent:>6,} rows  (likely sentinel)")
    print(f"  500 < tc != 99999    : {huge:>6,} rows  (extreme outliers)")
    q = corrupt["trip_count"].quantile([0.5, 0.99, 0.999])
    print(f"  quantiles            : p50={q[0.5]:.1f}  p99={q[0.99]:.1f}  p99.9={q[0.999]:.1f}")
    print("\n  sample of negatives:")
    print(corrupt[corrupt["trip_count"] < 0]
          [["time_bucket", "PULocationID", "trip_count"]]
          .head(3).to_string(index=False))


def issue_2_duplicates(baseline, corrupt):
    section("ISSUE 2 - duplicate rows on natural key")
    b_full = baseline.duplicated().sum()
    c_full = corrupt.duplicated().sum()
    b_key = baseline.duplicated(subset=KEY_COLS).sum()
    c_key = corrupt.duplicated(subset=KEY_COLS).sum()
    print(f"  full-row dupes        baseline: {b_full:>6,}   corrupt: {c_full:>6,}")
    print(f"  on {KEY_COLS}: baseline: {b_key:>6,}   corrupt: {c_key:>6,}")
    n_groups = (corrupt[corrupt.duplicated(subset=KEY_COLS, keep=False)]
                .groupby(KEY_COLS).ngroups)
    n_dup_rows = corrupt.duplicated(subset=KEY_COLS, keep=False).sum()
    print(f"  in corrupt: {n_dup_rows:,} rows across {n_groups:,} duplicated keys")


def issue_3_stuck_cbd(baseline, corrupt):
    section("ISSUE 3 - cbd_pricing_active stuck at 1")
    print(f"  baseline value_counts: {dict(baseline['cbd_pricing_active'].value_counts())}")
    print(f"  corrupt  value_counts: {dict(corrupt['cbd_pricing_active'].value_counts())}")
    print(f"  corrupt 'on' rate    : {corrupt['cbd_pricing_active'].mean()*100:.2f}%  "
          f"(baseline: {baseline['cbd_pricing_active'].mean()*100:.2f}%)")


def issue_4_holiday_over_flag(baseline, corrupt):
    section("ISSUE 4 - is_holiday over-flagged on non-holiday dates")
    b_rate = baseline["is_holiday"].mean() * 100
    c_rate = corrupt["is_holiday"].mean() * 100
    print(f"  baseline rate: {b_rate:.2f}%")
    print(f"  corrupt  rate: {c_rate:.2f}%")
    us_federal = {
        pd.Timestamp("2026-01-19").date(): "MLK Day",
        pd.Timestamp("2026-02-16").date(): "Presidents Day",
    }
    print("\n  Dates flagged as holiday (with actual US federal holiday status):")
    hol_dates = sorted(corrupt[corrupt["is_holiday"] == 1]
                       ["time_bucket"].dt.date.unique().tolist())
    for d in hol_dates:
        actual = us_federal.get(d, "NOT A HOLIDAY")
        n = (corrupt["time_bucket"].dt.date == d).sum()
        f = ((corrupt["time_bucket"].dt.date == d) & (corrupt["is_holiday"] == 1)).sum()
        marker = "OK " if actual != "NOT A HOLIDAY" else "BAD"
        print(f"    [{marker}] {d}  flagged {f/n*100:.0f}% of {n:,} rows - {actual}")


def summary(baseline, corrupt):
    section("SUMMARY - 4 ISSUES")
    n_neg = (corrupt['trip_count'] < 0).sum()
    n_sent = (corrupt['trip_count'] == 99999).sum()
    n_huge = ((corrupt['trip_count'] > 500) & (corrupt['trip_count'] != 99999)).sum()
    n_key_dup = corrupt.duplicated(subset=KEY_COLS).sum()
    n_dup_keys = (corrupt[corrupt.duplicated(subset=KEY_COLS, keep=False)]
                  .groupby(KEY_COLS).ngroups)
    items = [
        ("trip_count outliers",
         f"{n_neg} negative + {n_sent} sentinel + {n_huge} other extreme",
         "critical (target variable)"),
        ("Duplicate rows on natural key",
         f"{n_key_dup:,} dupe rows across {n_dup_keys:,} unique keys",
         "high"),
        ("cbd_pricing_active stuck at 1",
         f"100% of {len(corrupt):,} corrupt rows "
         f"(baseline: {baseline['cbd_pricing_active'].mean()*100:.0f}% on)",
         "high (feature)"),
        ("is_holiday over-flagged",
         f"{int(corrupt['is_holiday'].sum()):,} flagged rows; "
         f"only 2 of 7 flagged dates are real holidays",
         "high (feature)"),
    ]
    for i, (name, evidence, sev) in enumerate(items, 1):
        print(f"  {i}. {name}")
        print(f"     evidence: {evidence}")
        print(f"     severity: {sev}")


def main():
    df = pd.read_parquet(PATH)
    baseline = df[df["time_bucket"] < CUTOFF].copy()
    corrupt = df[df["time_bucket"] >= CUTOFF].copy()

    summarize_shape(df, baseline, corrupt)
    null_rates(baseline, corrupt)
    numeric_ranges(baseline, corrupt)
    issue_1_trip_count_outliers(corrupt)
    issue_2_duplicates(baseline, corrupt)
    issue_3_stuck_cbd(baseline, corrupt)
    issue_4_holiday_over_flag(baseline, corrupt)
    summary(baseline, corrupt)


if __name__ == "__main__":
    main()
