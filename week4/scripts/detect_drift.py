"""
Drift detection skeleton.

Write code to detect 4+ distinct drift patterns between baseline and new data.
Use statistical tests (KS, PSI, chi-square) to quantify drift.
"""

import os
import pandas as pd
import numpy as np
from pathlib import Path
from scipy.stats import ks_2samp

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PSI_SIGNIFICANT = 0.25  # PSI above this is a significant shift (BASELINE_METRICS.md)


def _psi(baseline: pd.Series, new: pd.Series, bins: int = 10) -> float:
    """Population Stability Index using quantile bins from the baseline."""
    baseline, new = baseline.dropna(), new.dropna()
    edges = np.unique(np.quantile(baseline, np.linspace(0, 1, bins + 1)))
    if edges.size < 2:
        return 0.0
    edges[0], edges[-1] = -np.inf, np.inf
    base_counts = np.histogram(baseline, bins=edges)[0].astype(float)
    new_counts = np.histogram(new, bins=edges)[0].astype(float)
    eps = 1e-6
    base_pct = base_counts / base_counts.sum() + eps
    new_pct = new_counts / new_counts.sum() + eps
    return float(np.sum((new_pct - base_pct) * np.log(new_pct / base_pct)))


def detect_feature_drift(baseline_df: pd.DataFrame, new_df: pd.DataFrame, feature: str) -> dict:
    """
    Detect drift in a single feature using the KS test and PSI.

    KS gives statistical significance, PSI gives magnitude. On large samples the
    KS p-value over-fires, so the drift verdict is magnitude-based (PSI).
    """
    base = baseline_df[feature].dropna()
    new = new_df[feature].dropna()
    ks_stat, ks_p = ks_2samp(base, new)
    psi = _psi(base, new)
    base_mean, new_mean = float(base.mean()), float(new.mean())
    pct_change = ((new_mean - base_mean) / base_mean * 100) if base_mean else float("nan")
    return {
        "feature": feature,
        "ks_statistic": float(ks_stat),
        "ks_p_value": float(ks_p),
        "psi": psi,
        "baseline_mean": round(base_mean, 2),
        "new_mean": round(new_mean, 2),
        "pct_change": round(pct_change, 1),
        "drifted": bool(psi > PSI_SIGNIFICANT),
    }


def detect_concept_drift_by_segment(baseline_df: pd.DataFrame, new_df: pd.DataFrame) -> dict:
    """
    Detect segment-level shift in mean demand, by zone and by hour.

    Compares mean trip_count per segment (baseline vs new) and surfaces the
    segments that moved most. This is demand shift per segment; accuracy-based
    concept drift would additionally require model predictions.
    """
    def _by(col, top=5):
        base = baseline_df.groupby(col)["trip_count"].mean()
        new = new_df.groupby(col)["trip_count"].mean()
        pct = ((new - base) / base * 100).replace([np.inf, -np.inf], np.nan).dropna()
        worst = pct.reindex(pct.abs().sort_values(ascending=False).index).head(top)
        return [
            {
                "segment": int(k),
                "baseline_mean": round(float(base[k]), 2),
                "new_mean": round(float(new[k]), 2),
                "pct_change": round(float(v), 1),
            }
            for k, v in worst.items()
        ]

    return {"by_zone": _by("PULocationID"), "by_hour": _by("hour")}


def make_plots(baseline_df: pd.DataFrame, new_df: pd.DataFrame, results: list, out_path: Path):
    """
    Render the drift patterns to a single 2x2 PNG. No-op (with a note) if
    matplotlib is not installed, so the CI drift check never breaks on it.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")  # headless backend; no display needed
        import matplotlib.pyplot as plt
    except ImportError:
        print("(matplotlib not installed - skipping plots)")
        return None

    base_c, feb_c = "#2980b9", "#e67e22"
    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle("Drift: Feb 2-28, 2026 vs Jan 1-15 baseline", fontsize=14, weight="bold")

    # (1) Feature drift by PSI
    ax = axes[0, 0]
    feats = [r["feature"] for r in results]
    psis = [r["psi"] for r in results]
    ax.barh(feats, psis, color=["#c0392b" if p > PSI_SIGNIFICANT else "#7f8c8d" for p in psis])
    ax.axvline(PSI_SIGNIFICANT, ls="--", color="#c0392b", lw=1)
    ax.invert_yaxis()
    ax.set_title("Feature drift (PSI)")
    ax.set_xlabel("PSI  (dashed = 0.25 significance)")

    # (2) trip_count distribution overlay (clipped for readability)
    ax = axes[0, 1]
    clip = 60
    ax.hist(baseline_df["trip_count"].clip(upper=clip), bins=40, density=True, alpha=0.5, label="baseline", color=base_c)
    ax.hist(new_df["trip_count"].clip(upper=clip), bins=40, density=True, alpha=0.5, label="Feb", color=feb_c)
    ax.set_title("trip_count distribution")
    ax.set_xlabel(f"trips / zone / 15min (clipped at {clip})")
    ax.legend()

    # (3) Mean demand by hour of day
    ax = axes[1, 0]
    bh = baseline_df.groupby("hour")["trip_count"].mean()
    nh = new_df.groupby("hour")["trip_count"].mean()
    ax.plot(bh.index, bh.values, marker="o", label="baseline", color=base_c)
    ax.plot(nh.index, nh.values, marker="o", label="Feb", color=feb_c)
    ax.set_title("Mean demand by hour of day")
    ax.set_xlabel("hour")
    ax.set_ylabel("mean trip_count")
    ax.legend()

    # (4) Top zones by absolute mean change
    ax = axes[1, 1]
    bz = baseline_df.groupby("PULocationID")["trip_count"].mean()
    nz = new_df.groupby("PULocationID")["trip_count"].mean()
    delta = (nz - bz).dropna()
    top = delta.reindex(delta.abs().sort_values(ascending=False).index).head(8).sort_values()
    y = np.arange(len(top))
    ax.barh(y - 0.2, bz[top.index].values, height=0.4, label="baseline", color=base_c)
    ax.barh(y + 0.2, nz[top.index].values, height=0.4, label="Feb", color=feb_c)
    ax.set_yticks(y)
    ax.set_yticklabels([f"zone {z}" for z in top.index])
    ax.set_title("Top zones by |mean change|")
    ax.set_xlabel("mean trip_count")
    ax.legend()

    fig.tight_layout(rect=[0, 0, 1, 0.97])
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    print(f"saved plots -> {out_path}")
    return out_path


def main():
    """Main drift detection analysis."""
    print("=" * 70)
    print("DRIFT DETECTION  (Feb 2-28, 2026  vs  Jan 1-15 baseline)")
    print("=" * 70)

    baseline_df = pd.read_parquet(DATA_DIR / "demand_enriched_baseline.parquet")
    w4 = pd.read_parquet(DATA_DIR / "demand_enriched_week4.parquet")
    w4["time_bucket"] = pd.to_datetime(w4["time_bucket"])
    new_df = w4[(w4["time_bucket"] >= "2026-02-02") & (w4["time_bucket"] < "2026-03-01")]
    print(f"baseline rows: {len(baseline_df):,} | new rows: {len(new_df):,}\n")

    # Feature-level drift (patterns 1-2)
    features = ["roll_mean_1day", "lag_1day", "lag_1week", "trip_count", "roll_mean_1h", "lag_1h"]
    results = sorted(
        (detect_feature_drift(baseline_df, new_df, f) for f in features),
        key=lambda r: -r["psi"],
    )
    print("FEATURE DRIFT (sorted by PSI):")
    print(f"  {'feature':<16}{'PSI':>8}{'KS_stat':>9}{'KS_p':>10}{'base':>8}{'new':>8}{'drift':>7}")
    for r in results:
        print(
            f"  {r['feature']:<16}{r['psi']:>8.3f}{r['ks_statistic']:>9.3f}"
            f"{r['ks_p_value']:>10.1e}{r['baseline_mean']:>8.2f}{r['new_mean']:>8.2f}"
            f"{str(r['drifted']):>7}"
        )
    drifted = [r["feature"] for r in results if r["drifted"]]

    # Segment-level drift (patterns 3-4)
    seg = detect_concept_drift_by_segment(baseline_df, new_df)
    print("\nSEGMENT DRIFT - top zones by |mean change|:")
    for z in seg["by_zone"]:
        print(f"  zone {z['segment']:>3}: {z['baseline_mean']:>6.2f} -> {z['new_mean']:>6.2f} ({z['pct_change']:+.1f}%)")
    print("SEGMENT DRIFT - top hours by |mean change|:")
    for h in seg["by_hour"]:
        print(f"  hour {h['segment']:>2}: {h['baseline_mean']:>6.2f} -> {h['new_mean']:>6.2f} ({h['pct_change']:+.1f}%)")

    # Summary of distinct patterns (derived from the results above)
    tc = next(r for r in results if r["feature"] == "trip_count")
    worst_zone, worst_hour = seg["by_zone"][0], seg["by_hour"][0]
    print("\n" + "=" * 70)
    print("SUMMARY - distinct drift patterns:")
    print(f"  1. Input-feature drift: {drifted} significant (PSI>{PSI_SIGNIFICANT}) - model inputs shifting")
    print(f"  2. Mild global target drift: trip_count {tc['baseline_mean']} -> {tc['new_mean']} (PSI {tc['psi']:.3f})")
    print(f"  3. Zone-level drift: worst zone {worst_zone['segment']} ({worst_zone['pct_change']:+.1f}%)")
    print(f"  4. Hour-of-day drift: worst hour {worst_hour['segment']} ({worst_hour['pct_change']:+.1f}%)")
    print("=" * 70)

    make_plots(baseline_df, new_df, results, DATA_DIR.parent / "drift_patterns.png")

    # Emit the output the monitoring workflow gates the drift-alert issue on.
    drift_detected = len(drifted) > 0
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a") as f:
            f.write(f"drift={'true' if drift_detected else 'false'}\n")


if __name__ == "__main__":
    main()
