"""Tests for the Week 4 monitoring metrics and drift detection.

Unit tests run on small synthetic data so they are fast and deterministic;
one integration test runs against the real parquet and is skipped if absent.

Run:  python -m pytest week4/scripts/test_monitoring.py
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from metric_template import MetricComputer
import detect_drift

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")


# --- helpers ----------------------------------------------------------------

def make_df(trip_count, n_zones=4, ts_end=None):
    """Build a minimal monitoring frame with the columns the metrics use."""
    trip_count = np.asarray(trip_count, dtype=float)
    n = len(trip_count)
    ts_end = ts_end if ts_end is not None else pd.Timestamp.now()
    times = pd.date_range(end=ts_end, periods=n, freq="15min")
    return pd.DataFrame({
        "trip_count": trip_count,
        "PULocationID": (np.arange(n) % n_zones) + 1,
        "time_bucket": times,
        "hour": times.hour,
        "lag_1day": trip_count,
    })


@pytest.fixture
def baseline():
    return make_df(np.random.default_rng(0).poisson(14, 2000))


@pytest.fixture
def shifted():
    # clearly different distribution (mean 6 vs 14) -> should register as drift
    return make_df(np.random.default_rng(1).poisson(6, 2000))


# --- data quality -----------------------------------------------------------

def test_null_rates_clean(baseline):
    rates = MetricComputer(baseline).metric_3_null_rates(baseline)
    assert all(r == 0.0 for r in rates.values())


def test_null_rates_detects(baseline):
    df = baseline.copy()
    df.loc[df.index[:200], "trip_count"] = np.nan  # 10% null
    rates = MetricComputer(baseline).metric_3_null_rates(df)
    assert rates["trip_count"] == pytest.approx(0.10, abs=1e-9)


def test_duplicate_rate_clean(baseline):
    res = MetricComputer(baseline).metric_8_duplicate_rate(baseline)
    assert res["count"] == 0 and res["rate"] == 0.0


def test_duplicate_rate_detects(baseline):
    df = pd.concat([baseline, baseline.iloc[:100]], ignore_index=True)
    res = MetricComputer(baseline).metric_8_duplicate_rate(df)
    assert res["count"] == 100


# --- data drift -------------------------------------------------------------

def test_ks_no_drift(baseline):
    res = MetricComputer(baseline).metric_4_ks_test(baseline)
    assert res["drifted"] is False and res["p_value"] > 0.05


def test_ks_detects_drift(baseline, shifted):
    res = MetricComputer(baseline).metric_4_ks_test(shifted)
    assert res["drifted"] is True and res["p_value"] < 0.05


def test_psi_no_drift(baseline):
    assert MetricComputer(baseline).metric_5_psi(baseline) < 0.10


def test_psi_detects_drift(baseline, shifted):
    assert MetricComputer(baseline).metric_5_psi(shifted) > 0.25


# --- model performance ------------------------------------------------------

def test_accuracy_perfect(baseline):
    acc = MetricComputer(baseline).metric_1_accuracy(
        None, np.array([1.0, 2.0, 3.0]), np.array([1, 2, 3])
    )
    assert acc == 1.0


def test_accuracy_partial(baseline):
    acc = MetricComputer(baseline).metric_1_accuracy(
        None, np.array([1.0, 2.0, 9.0, 9.0]), np.array([1, 2, 3, 4])
    )
    assert acc == 0.5


def test_accuracy_by_zone(baseline):
    df = pd.DataFrame({"PULocationID": [10, 10, 20, 20]})
    res = MetricComputer(baseline).metric_2_accuracy_by_zone(
        df, np.array([5.0, 9.0, 7.0, 7.0]), np.array([5, 5, 7, 7])
    )
    assert res[10] == 0.5 and res[20] == 1.0


# --- model / pipeline health ------------------------------------------------

def test_prediction_distribution_collapsed(baseline):
    res = MetricComputer(baseline).metric_6_prediction_distribution(np.full(100, 7.0))
    assert res["collapsed"] is True and res["std"] == pytest.approx(0.0)


def test_prediction_distribution_varied(baseline):
    res = MetricComputer(baseline).metric_6_prediction_distribution(np.arange(100.0))
    assert res["collapsed"] is False


def test_data_freshness_fresh(baseline):
    df = make_df(np.ones(10), ts_end=pd.Timestamp.now())
    assert MetricComputer(baseline).metric_7_data_freshness(df)["stale"] is False


def test_data_freshness_stale(baseline):
    df = make_df(np.ones(10), ts_end=pd.Timestamp.now() - pd.Timedelta(days=3))
    assert MetricComputer(baseline).metric_7_data_freshness(df)["stale"] is True


# --- orchestration ----------------------------------------------------------

def test_compute_all_metrics_data_only(baseline):
    res = MetricComputer(baseline).compute_all_metrics(baseline)
    assert {"null_rates", "ks_test", "psi", "data_freshness", "duplicate_rate"} <= set(res)
    assert "accuracy" not in res


def test_compute_all_metrics_with_predictions(baseline):
    preds = baseline["trip_count"].to_numpy()
    res = MetricComputer(baseline).compute_all_metrics(baseline, predictions=preds, actuals=preds)
    assert {"accuracy", "accuracy_by_zone", "prediction_distribution"} <= set(res)


# --- drift detection --------------------------------------------------------

def test_detect_feature_drift_stable(baseline):
    assert detect_drift.detect_feature_drift(baseline, baseline, "trip_count")["drifted"] is False


def test_detect_feature_drift_shifted(baseline, shifted):
    res = detect_drift.detect_feature_drift(baseline, shifted, "trip_count")
    assert res["drifted"] is True and res["psi"] > 0.25


def test_detect_concept_drift_by_segment_structure(baseline, shifted):
    res = detect_drift.detect_concept_drift_by_segment(baseline, shifted)
    assert "by_zone" in res and "by_hour" in res
    assert all(
        {"segment", "baseline_mean", "new_mean", "pct_change"} <= set(d)
        for d in res["by_zone"]
    )


# --- integration on the real data (skipped if the parquet is absent) --------

@pytest.mark.skipif(
    not os.path.exists(os.path.join(DATA_DIR, "demand_enriched_week4.parquet")),
    reason="week4 parquet not present",
)
def test_real_data_feature_drift():
    base = pd.read_parquet(
        os.path.join(DATA_DIR, "demand_enriched_baseline.parquet"),
        columns=["trip_count", "roll_mean_1day"],
    )
    w4 = pd.read_parquet(
        os.path.join(DATA_DIR, "demand_enriched_week4.parquet"),
        columns=["time_bucket", "trip_count", "roll_mean_1day"],
    )
    w4["time_bucket"] = pd.to_datetime(w4["time_bucket"])
    feb = w4[(w4["time_bucket"] >= "2026-02-02") & (w4["time_bucket"] < "2026-03-01")]
    res = detect_drift.detect_feature_drift(base, feb, "roll_mean_1day")
    assert res["drifted"] is True  # roll_mean_1day PSI ~0.35 on the real data
