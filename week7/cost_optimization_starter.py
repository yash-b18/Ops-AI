"""
Week 7: Cost Optimization & Feedback Loop

Three systems for operating the agent in production:
1. CostAnalyzer         - track per-query cost and flag expensive outliers
2. OptimizationStrategy - cut cost via caching, retrieval/model selection, compression
3. FeedbackLoop         - collect, validate, and measure user corrections
"""

import logging
import re
import statistics
from collections import Counter
from typing import Dict, List, Any
from datetime import datetime

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ============================================================================
# TASK 1: Implement CostAnalyzer
# ============================================================================


class CostAnalyzer:
    """Analyze and track query costs by component."""

    # The per-component cost fields every recorded query is expected to carry.
    # Anything missing is filled with 0.0 so downstream math never KeyErrors on
    # a partially-populated record.
    COST_FIELDS = ("retrieval_cost", "llm_cost", "tool_cost", "error_cost")

    # A query whose total cost exceeds (mean + SPIKE_SIGMA * stdev) of the
    # recorded history is flagged as a spike. Two sigma is roughly the top 2.5%
    # of a normal distribution -- "clearly more expensive than the usual query".
    SPIKE_SIGMA = 2.0

    def __init__(self):
        """Initialize the cost analyzer with an empty query history."""
        self.query_history: List[Dict[str, Any]] = []

    def record_query(self, query: Dict[str, Any]):
        """Record a query and its cost breakdown.

        The caller passes a dict describing one query. Missing component costs
        default to 0.0, ``total_cost`` is derived from the components when not
        supplied, and ``timestamp`` is stamped (UTC ISO-8601) when absent -- so
        a caller can record a partial dict and still get a consistent record.

        Expected fields: query_text, retrieval_cost, llm_cost, tool_cost,
        error_cost, total_cost, timestamp.
        """
        record = dict(query)  # copy so we never mutate the caller's dict
        for field in self.COST_FIELDS:
            record[field] = float(record.get(field, 0.0))

        # Derive total from the components when the caller didn't give one.
        if query.get("total_cost") is None:
            record["total_cost"] = sum(record[f] for f in self.COST_FIELDS)
        else:
            record["total_cost"] = float(record["total_cost"])

        record.setdefault("query_text", "")
        record.setdefault("timestamp", datetime.utcnow().isoformat())
        self.query_history.append(record)

    def get_cost_breakdown(self) -> Dict[str, Any]:
        """Get total cost broken down by component across all recorded queries.

        ``total_daily`` is the sum of the four component totals (matching the
        worked example in READING.md), and ``query_count`` is the number of
        queries the totals were computed over.
        """
        breakdown = {
            "retrieval_total": sum(q["retrieval_cost"] for q in self.query_history),
            "llm_total": sum(q["llm_cost"] for q in self.query_history),
            "tool_total": sum(q["tool_cost"] for q in self.query_history),
            "error_total": sum(q["error_cost"] for q in self.query_history),
            "query_count": len(self.query_history),
        }
        breakdown["total_daily"] = (
            breakdown["retrieval_total"]
            + breakdown["llm_total"]
            + breakdown["tool_total"]
            + breakdown["error_total"]
        )
        return breakdown

    def identify_cost_spikes(self) -> List[Dict]:
        """Identify unusually expensive queries (statistical outliers).

        Flags any query whose ``total_cost`` exceeds mean + SPIKE_SIGMA * stdev
        of all recorded query costs. A standard deviation needs at least two
        queries, so fewer than that (or an all-equal history, stdev 0) yields no
        spikes. Each spike carries its cost, the threshold it beat, and how many
        standard deviations above the mean it sits (z-score) for quick triage.
        """
        if len(self.query_history) < 2:
            return []

        costs = [q["total_cost"] for q in self.query_history]
        mean = statistics.mean(costs)
        stdev = statistics.stdev(costs)  # sample stdev; requires n >= 2

        # All-equal costs => stdev 0 => no meaningful outliers to report.
        if stdev == 0:
            return []

        threshold = mean + self.SPIKE_SIGMA * stdev
        spikes = [
            {
                "query_text": q.get("query_text", ""),
                "total_cost": q["total_cost"],
                "threshold": threshold,
                "z_score": (q["total_cost"] - mean) / stdev,
                "timestamp": q.get("timestamp"),
            }
            for q in self.query_history
            if q["total_cost"] > threshold
        ]
        # Most expensive first so the worst offender is easiest to spot.
        spikes.sort(key=lambda s: s["total_cost"], reverse=True)
        if spikes:
            logger.info(
                "Identified %d cost spike(s) above $%.4f", len(spikes), threshold
            )
        return spikes


# ============================================================================
# TASK 2: Implement OptimizationStrategy
# ============================================================================


class OptimizationStrategy:
    """Optimize agent costs through multiple strategies."""

    # Model names returned by select_model_by_complexity(). Aligned with the
    # agent's actual stack (app_starter.py runs flash-lite): simple questions go
    # to the cheapest model, complex reasoning to the stronger flash model.
    SIMPLE_MODEL = "gemini-2.5-flash-lite"
    COMPLEX_MODEL = "gemini-2.5-flash"

    # Words that signal a query needs real reasoning rather than a fact lookup.
    COMPLEXITY_KEYWORDS = (
        "analyze", "analyse", "explain", "compare", "design", "evaluate",
        "summarize", "summarise", "assess", "recommend",
    )
    # A query longer than this many words is treated as complex even without a
    # keyword (long questions tend to bundle several asks together).
    COMPLEXITY_WORD_THRESHOLD = 15

    # Default number of documents to keep when capping retrieval.
    DEFAULT_TOP_K = 3

    # Flash-Lite costs roughly a quarter of the stronger flash model per token,
    # so routing a query to it saves ~75% on that call. Used to weight the
    # model-selection contribution in get_optimization_impact().
    CHEAP_MODEL_DISCOUNT = 0.75

    def __init__(self, top_k: int = DEFAULT_TOP_K):
        """Initialize the cache, retrieval top-k, and usage counters.

        The counters feed get_optimization_impact() so reported savings reflect
        what actually happened this run rather than fixed guesses.
        """
        self.cache: Dict[str, str] = {}  # normalized_query -> response
        self.top_k = top_k
        self.strategies_applied: List[str] = []

        # Usage counters for data-driven impact analysis.
        self._cache_hits = 0
        self._cache_misses = 0
        self._retrieval_before = 0  # total docs requested
        self._retrieval_after = 0   # total docs after capping
        self._model_simple = 0      # times the cheap model was chosen
        self._model_complex = 0     # times the strong model was chosen
        self._compress_before = 0   # total chars before compression
        self._compress_after = 0    # total chars after compression

    @staticmethod
    def _normalize(query: str) -> str:
        """Cache key: collapse whitespace and lowercase.

        So "What is policy?" and "  what is   POLICY? " resolve to one entry.
        """
        return " ".join(query.split()).lower()

    def _mark(self, strategy: str):
        """Record that a strategy was exercised at least once."""
        if strategy not in self.strategies_applied:
            self.strategies_applied.append(strategy)

    def apply_caching(self, query: str, response: str) -> tuple:
        """Cache query responses.

        The first call for a query stores and returns its response as a miss;
        any later call with an equivalent query returns the cached response as a
        hit (the new ``response`` argument is ignored on a hit, so a repeat
        query is answered without another LLM call).

        Returns:
            (is_cached_hit, response)
        """
        self._mark("caching")
        key = self._normalize(query)
        if key in self.cache:
            self._cache_hits += 1
            return (True, self.cache[key])
        self._cache_misses += 1
        self.cache[key] = response
        return (False, response)

    def optimize_retrieval_count(self, num_docs: int) -> int:
        """Cap the number of retrieved documents at top-k.

        Returns min(num_docs, top_k), so an over-eager retrieval of 15 docs is
        trimmed to 3 -- cutting the tokens spent stuffing documents into the
        prompt. Never increases the count; non-positive inputs pass through.
        """
        if num_docs <= 0:
            return num_docs
        optimized = min(num_docs, self.top_k)
        self._mark("retrieval_reduction")
        self._retrieval_before += num_docs
        self._retrieval_after += optimized
        return optimized

    def select_model_by_complexity(self, query: str) -> str:
        """Choose the cheapest capable model for the query.

        Simple lookups ("What is the travel policy?") go to the cheap model;
        queries containing a reasoning keyword (analyze/compare/design/...) or
        that are unusually long are routed to the stronger model.
        """
        self._mark("model_selection")
        lowered = query.lower()
        is_complex = any(
            re.search(r"\b" + kw + r"\b", lowered) for kw in self.COMPLEXITY_KEYWORDS
        ) or len(query.split()) > self.COMPLEXITY_WORD_THRESHOLD

        if is_complex:
            self._model_complex += 1
            return self.COMPLEX_MODEL
        self._model_simple += 1
        return self.SIMPLE_MODEL

    def enable_response_compression(self, response: str, max_sentences: int = 3) -> str:
        """Trim a long response to its first ``max_sentences`` sentences.

        Keeps the leading (usually most essential) sentences and drops the rest,
        reducing output tokens. A response already within the limit is returned
        effectively unchanged.
        """
        self._mark("compression")
        # Split into sentences, keeping each sentence's terminal punctuation.
        sentences = [s.strip() for s in re.findall(r"[^.!?]+[.!?]?", response) if s.strip()]
        compressed = " ".join(sentences[:max_sentences]) if sentences else response

        self._compress_before += len(response)
        self._compress_after += len(compressed)
        return compressed

    def get_optimization_impact(self) -> Dict[str, Any]:
        """Estimate cost savings from the strategies actually exercised.

        Each strategy's contribution is derived from observed usage, so the
        numbers reflect this run rather than fixed guesses:
        - caching: fraction of lookups served from cache (each hit ~ one avoided query)
        - retrieval_reduction: fraction of requested document volume trimmed away
        - model_selection: cheap-model share, scaled by the cheap model's discount
        - compression: average reduction in response length

        The independent per-axis savings are combined multiplicatively (each
        acts on what the previous left behind) so ``total_savings_pct`` never
        exceeds 100. All values are percentages.
        """
        breakdown: Dict[str, float] = {}

        # Caching: hit rate over all lookups.
        lookups = self._cache_hits + self._cache_misses
        if lookups:
            breakdown["caching"] = 100.0 * self._cache_hits / lookups

        # Retrieval: how much of the requested doc volume we trimmed away.
        if self._retrieval_before:
            saved = self._retrieval_before - self._retrieval_after
            breakdown["retrieval_reduction"] = 100.0 * saved / self._retrieval_before

        # Model selection: cheap-model share scaled by its per-call discount.
        model_calls = self._model_simple + self._model_complex
        if model_calls:
            cheap_share = self._model_simple / model_calls
            breakdown["model_selection"] = 100.0 * cheap_share * self.CHEAP_MODEL_DISCOUNT

        # Compression: average characters removed from responses.
        if self._compress_before:
            saved = self._compress_before - self._compress_after
            breakdown["compression"] = 100.0 * saved / self._compress_before

        remaining = 1.0
        for pct in breakdown.values():
            remaining *= 1.0 - min(max(pct, 0.0), 100.0) / 100.0
        total_savings_pct = 100.0 * (1.0 - remaining)

        return {
            "total_savings_pct": round(total_savings_pct, 2),
            "strategies_applied": self.strategies_applied,
            "breakdown": {k: round(v, 2) for k, v in breakdown.items()},
        }


# ============================================================================
# TASK 3: Implement FeedbackLoop
# ============================================================================


class FeedbackLoop:
    """Collect and validate user corrections for continuous improvement."""

    # Minimum authority level required for a correction to be accepted. In the
    # hierarchy below, manager and above grade at >= 3.
    MIN_AUTHORITY = 3

    # Words ignored when mining "what gets corrected most" so the patterns
    # surface real topics (salary, travel, expense) rather than filler.
    _STOPWORDS = {
        "the", "is", "a", "an", "of", "to", "for", "and", "what", "does",
        "do", "in", "on", "my", "our", "are", "how", "can", "you", "with",
        "this", "that", "it", "be", "or", "by", "we", "was", "were",
    }

    def __init__(self):
        """Initialize the corrections store and the role authority hierarchy."""
        self.corrections: List[Dict[str, Any]] = []
        # Authority hierarchy for role-based validation; only manager+ (>= 3)
        # may have a correction accepted.
        self.authority = {
            "engineer": 1,
            "hr": 2,
            "finance": 2,
            "manager": 3,
            "executive": 4,
        }

    def submit_correction(
        self,
        original_query: str,
        original_answer: str,
        corrected_answer: str,
        user_role: str,
    ) -> Dict[str, Any]:
        """Submit a correction to the agent's answer.

        Every submission is stored (so feedback metrics can measure how many
        submissions are actually valid). A correction is accepted only when it
        passes validation: the submitting role has manager+ authority AND the
        corrected answer is more detailed (longer) than the original.

        Returns:
            {"accepted": bool, "reason": str}
        """
        self.corrections.append(
            {
                "original_query": original_query,
                "original_answer": original_answer,
                "corrected_answer": corrected_answer,
                "user_role": user_role,
                "timestamp": datetime.utcnow().isoformat(),
                "accepted": False,  # provisional; set just below
            }
        )
        index = len(self.corrections) - 1
        accepted, reason = self._evaluate(self.corrections[index])
        self.corrections[index]["accepted"] = accepted
        self.corrections[index]["reason"] = reason

        logger.info(
            "Correction from role=%s %s (%s)",
            user_role,
            "ACCEPTED" if accepted else "REJECTED",
            reason,
        )
        return {"accepted": accepted, "reason": reason}

    def _evaluate(self, correction: Dict[str, Any]) -> tuple:
        """Validate one correction, returning (is_valid, human-readable reason).

        Single source of truth for the acceptance rules, shared by
        submit_correction() and validate_correction().
        """
        role = correction.get("user_role", "")
        if self.authority.get(role, 0) < self.MIN_AUTHORITY:
            return (False, f"role '{role}' lacks authority (needs manager or above)")
        if len(correction.get("corrected_answer", "")) <= len(
            correction.get("original_answer", "")
        ):
            return (False, "corrected answer must be more detailed than the original")
        return (True, "accepted")

    def validate_correction(self, index: int) -> bool:
        """Validate a stored correction is accurate.

        Returns True only when the correction at ``index`` has manager+ authority
        and is more detailed than the original answer. Out-of-range indices
        return False rather than raising.
        """
        if not 0 <= index < len(self.corrections):
            return False
        is_valid, _ = self._evaluate(self.corrections[index])
        return is_valid

    def get_feedback_metrics(self) -> Dict[str, Any]:
        """Compute metrics on feedback quality.

        Returns:
            total_corrections: number of submissions received
            validation_rate: fraction (0-1) of submissions that are valid
            avg_correction_length: mean character length of corrected answers
            top_error_patterns: most frequent topic words across the corrected
                queries -- signals for where the agent most needs improvement,
                as (word, count) pairs
        """
        total = len(self.corrections)
        if total == 0:
            return {
                "total_corrections": 0,
                "validation_rate": 0.0,
                "avg_correction_length": 0.0,
                "top_error_patterns": [],
            }

        valid = sum(1 for c in self.corrections if c.get("accepted"))
        avg_len = sum(len(c["corrected_answer"]) for c in self.corrections) / total

        # Mine the most-corrected topics from the original queries.
        words = [
            w
            for c in self.corrections
            for w in re.findall(r"[a-z0-9]+", c["original_query"].lower())
            if w not in self._STOPWORDS and len(w) > 2
        ]
        top_error_patterns = Counter(words).most_common(5)

        return {
            "total_corrections": total,
            "validation_rate": valid / total,
            "avg_correction_length": avg_len,
            "top_error_patterns": top_error_patterns,
        }


if __name__ == "__main__":
    """Run with: python3 cost_optimization_starter.py"""

    # ----- CostAnalyzer -----
    print("Testing CostAnalyzer...")
    analyzer = CostAnalyzer()
    analyzer.record_query(
        {
            "query_text": "What is the travel policy?",
            "retrieval_cost": 0.002,
            "llm_cost": 0.005,
            "tool_cost": 0.001,
            "error_cost": 0.0,
            "total_cost": 0.008,
        }
    )
    analyzer.record_query(
        {
            "query_text": "Who is the CFO?",
            "retrieval_cost": 0.001,
            "llm_cost": 0.003,
            "tool_cost": 0.0,
            "error_cost": 0.0,
            # total_cost omitted on purpose -> derived as 0.004
        }
    )
    breakdown = analyzer.get_cost_breakdown()
    assert breakdown["query_count"] == 2, breakdown
    assert abs(breakdown["retrieval_total"] - 0.003) < 1e-9, breakdown
    assert abs(breakdown["llm_total"] - 0.008) < 1e-9, breakdown
    assert abs(breakdown["tool_total"] - 0.001) < 1e-9, breakdown
    assert abs(breakdown["total_daily"] - 0.012) < 1e-9, breakdown
    assert abs(analyzer.query_history[1]["total_cost"] - 0.004) < 1e-9, "derived total"
    print(f"  get_cost_breakdown: PASSED  ({breakdown})")

    # Spike detection: 10 routine queries establish a baseline, 1 runaway query
    # should stand out as an outlier above mean + 2*stdev.
    spike_analyzer = CostAnalyzer()
    for i in range(10):
        spike_analyzer.record_query(
            {
                "query_text": f"routine query {i}",
                "retrieval_cost": 0.001,
                "llm_cost": 0.005,
                "tool_cost": 0.0,
                "error_cost": 0.0,
                "total_cost": 0.006,
            }
        )
    spike_analyzer.record_query(
        {
            "query_text": "runaway retrieval query",
            "retrieval_cost": 0.5,
            "llm_cost": 0.4,
            "tool_cost": 0.1,
            "error_cost": 0.0,
            "total_cost": 1.0,
        }
    )
    spikes = spike_analyzer.identify_cost_spikes()
    assert len(spikes) == 1, spikes
    assert spikes[0]["query_text"] == "runaway retrieval query", spikes
    assert CostAnalyzer().identify_cost_spikes() == [], "no spikes without a baseline"
    print(f"  identify_cost_spikes: PASSED  (z={spikes[0]['z_score']:.2f})")

    # ----- OptimizationStrategy -----
    print("\nTesting OptimizationStrategy...")
    optimizer = OptimizationStrategy()

    hit1, resp1 = optimizer.apply_caching("What is policy?", "Answer A")
    hit2, resp2 = optimizer.apply_caching("  what is   POLICY? ", "Answer B")
    assert hit1 is False, "first call is a miss"
    assert hit2 is True, "second (normalized-equal) call is a hit"
    assert resp1 == resp2 == "Answer A", "a hit returns the originally cached response"
    print("  apply_caching: PASSED")

    assert optimizer.optimize_retrieval_count(15) == 3, "15 docs capped to top-3"
    assert optimizer.optimize_retrieval_count(2) == 2, "fewer than k passes through"
    print("  optimize_retrieval_count: PASSED")

    assert (
        optimizer.select_model_by_complexity("What is the travel policy?")
        == "gemini-2.5-flash-lite"
    ), "simple query -> cheap model"
    assert (
        optimizer.select_model_by_complexity("Analyze and compare the travel policies")
        == "gemini-2.5-flash"
    ), "reasoning query -> strong model"
    print("  select_model_by_complexity: PASSED")

    long_resp = (
        "First sentence. Second sentence. Third sentence. "
        "Fourth sentence. Fifth sentence."
    )
    compressed = optimizer.enable_response_compression(long_resp, max_sentences=2)
    assert compressed == "First sentence. Second sentence.", compressed
    assert len(compressed) < len(long_resp), "compression shortens the response"
    print("  enable_response_compression: PASSED")

    impact = optimizer.get_optimization_impact()
    assert impact["total_savings_pct"] > 0, impact
    assert {
        "caching",
        "retrieval_reduction",
        "model_selection",
        "compression",
    }.issubset(set(impact["strategies_applied"])), impact
    print(f"  get_optimization_impact: PASSED  ({impact['total_savings_pct']}% est.)")

    # ----- FeedbackLoop -----
    print("\nTesting FeedbackLoop...")
    feedback = FeedbackLoop()

    r1 = feedback.submit_correction(
        "What is the travel policy for flights over 8 hours?",
        "There is no specific policy.",
        "Employees can book business class for flights over 8 hours with manager approval.",
        "manager",
    )
    assert r1["accepted"] is True, r1
    assert len(feedback.corrections) == 1, feedback.corrections

    r2 = feedback.submit_correction(
        "What is the expense limit?",
        "Unknown.",
        "The expense limit for engineers is $5,000 per quarter with receipts.",
        "engineer",
    )
    assert r2["accepted"] is False, r2  # engineer lacks authority
    assert len(feedback.corrections) == 2, "rejected corrections are still recorded"

    r3 = feedback.submit_correction(
        "Who approves PTO?",
        "Your direct manager approves PTO requests in the HR portal.",
        "Your manager.",
        "manager",
    )
    assert r3["accepted"] is False, r3  # not more detailed than the original
    print("  submit_correction: PASSED")

    assert feedback.validate_correction(0) is True, "manager + detailed -> valid"
    assert feedback.validate_correction(1) is False, "engineer -> invalid"
    assert feedback.validate_correction(99) is False, "out-of-range index -> False"
    print("  validate_correction: PASSED")

    metrics = feedback.get_feedback_metrics()
    assert metrics["total_corrections"] == 3, metrics
    assert abs(metrics["validation_rate"] - (1 / 3)) < 1e-9, metrics
    assert metrics["avg_correction_length"] > 0, metrics
    print(f"  get_feedback_metrics: PASSED  ({metrics})")

    print("\nAll tests passed!")
