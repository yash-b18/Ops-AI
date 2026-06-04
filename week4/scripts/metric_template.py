"""
Monitoring metrics skeleton.

This file defines 8 metric stubs for monitoring data and model health.
Implement at least 5 of the 8 metrics based on your monitoring framework design.
Each metric should compute a specific health signal about your data/model,
and return a dict (or float) that can be checked against your alert thresholds.
"""

import pandas as pd
import numpy as np
from scipy.stats import ks_2samp


class MetricComputer:
    """Compute monitoring metrics for drift detection."""

    def __init__(self, baseline_df: pd.DataFrame):
        """Initialize with baseline data."""
        self.baseline_df = baseline_df

    def metric_1_accuracy(
        self, new_df: pd.DataFrame, predictions: np.ndarray, actuals: np.ndarray
    ) -> float:
        """
        Metric 1: Overall Accuracy

        Fraction of correct predictions (rounded to whole trips, since trip_count
        is an integer count).
        """
        predictions = np.asarray(predictions, dtype=float)
        actuals = np.asarray(actuals, dtype=float)
        if actuals.size == 0:
            return float("nan")
        correct = np.round(predictions) == np.round(actuals)
        return float(np.mean(correct))

    def metric_2_accuracy_by_zone(
        self, new_df: pd.DataFrame, predictions: np.ndarray, actuals: np.ndarray
    ) -> dict:
        """
        Metric 2: Accuracy by Zone

        Per-zone fraction of correct predictions, keyed by PULocationID.
        """
        predictions = np.asarray(predictions, dtype=float)
        actuals = np.asarray(actuals, dtype=float)
        correct = np.round(predictions) == np.round(actuals)
        zones = new_df["PULocationID"].to_numpy()

        accuracy_by_zone = {}
        for zone in np.unique(zones):
            mask = zones == zone
            if mask.any():
                accuracy_by_zone[int(zone)] = float(np.mean(correct[mask]))
        return accuracy_by_zone

    def metric_3_null_rates(self, new_df: pd.DataFrame) -> dict:
        """
        Metric 3: Null Rates

        Null rate (fraction missing) for every column. The healthy baseline is
        0.0 across all features (per BASELINE_METRICS.md), so any non-zero rate
        is a signal worth surfacing.
        """
        return {col: float(rate) for col, rate in new_df.isna().mean().items()}

    def metric_4_ks_test(self, new_df: pd.DataFrame) -> dict:
        """
        Metric 4: KS Test for Distribution Shift

        Two-sample Kolmogorov-Smirnov test on trip_count, baseline vs new.
        """
        baseline = self.baseline_df["trip_count"].dropna()
        new = new_df["trip_count"].dropna()
        statistic, p_value = ks_2samp(baseline, new)
        return {
            "statistic": float(statistic),
            "p_value": float(p_value),
            "drifted": bool(p_value < 0.05),
        }

    def metric_5_psi(self, new_df: pd.DataFrame, bins: int = 10) -> float:
        """
        Metric 5: Population Stability Index

        PSI of new trip_count vs baseline, using quantile bins from the baseline.
        """
        baseline = self.baseline_df["trip_count"].dropna()
        new = new_df["trip_count"].dropna()

        # Bin edges from baseline quantiles; outer edges widened to catch new extremes.
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

    def metric_6_prediction_distribution(self, predictions: np.ndarray) -> dict:
        """
        Metric 6: Prediction Distribution Shift

        Mean/std of predictions; flags a collapsed model (near-constant output).
        """
        predictions = np.asarray(predictions, dtype=float)
        if predictions.size == 0:
            return {"mean": float("nan"), "std": float("nan"), "collapsed": True}
        std = float(np.std(predictions))
        return {
            "mean": float(np.mean(predictions)),
            "std": std,
            "collapsed": bool(std < 1.0),
        }

    def metric_7_data_freshness(self, new_df: pd.DataFrame) -> dict:
        """
        Metric 7: Data Freshness

        Age of the most recent record relative to now.
        """
        latest = pd.to_datetime(new_df["time_bucket"]).max()
        age = pd.Timestamp.now() - latest
        age_minutes = age.total_seconds() / 60.0
        return {
            "latest_record": str(latest),
            "age_minutes": round(age_minutes, 1),
            "age_hours": round(age_minutes / 60.0, 1),
            "stale": bool(age_minutes > 24 * 60),
        }

    def metric_8_duplicate_rate(self, new_df: pd.DataFrame) -> dict:
        """
        Metric 8: Duplicate Rate

        Fraction of rows that are exact duplicates.
        """
        n = len(new_df)
        count = int(new_df.duplicated().sum())
        return {"rate": (count / n) if n else 0.0, "count": count}

    def compute_all_metrics(
        self,
        new_df: pd.DataFrame,
        predictions: np.ndarray = None,
        actuals: np.ndarray = None,
    ) -> dict:
        """
        Compute all metrics.

        Prediction-dependent metrics (1, 2, 6) are included only when the
        corresponding predictions/actuals are supplied.
        """
        results = {
            "null_rates": self.metric_3_null_rates(new_df),
            "ks_test": self.metric_4_ks_test(new_df),
            "psi": self.metric_5_psi(new_df),
            "data_freshness": self.metric_7_data_freshness(new_df),
            "duplicate_rate": self.metric_8_duplicate_rate(new_df),
        }
        if predictions is not None:
            results["prediction_distribution"] = self.metric_6_prediction_distribution(
                predictions
            )
        if predictions is not None and actuals is not None:
            results["accuracy"] = self.metric_1_accuracy(new_df, predictions, actuals)
            results["accuracy_by_zone"] = self.metric_2_accuracy_by_zone(
                new_df, predictions, actuals
            )
        return results
