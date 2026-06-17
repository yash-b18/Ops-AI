"""
Week 6: Access Control, Rate Limiting & Cost Enforcement Starter Template

Implement three guardrails:
1. AccessController - role-based document/field access control
2. RateLimiter - limit queries per minute per user
3. CostEnforcer - enforce budget limits per role
"""

import json
import logging
import re
from typing import Dict, Any, List
from datetime import datetime
from time import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ============================================================================
# TASK 1: Implement AccessController
# ============================================================================


class AccessController:
    """Enforce role-based access control."""

    # Regex fragment matching a single "value" token to redact (money, SSN,
    # number/percentage, or a quoted string). Uses only non-capturing groups.
    _VALUE = (
        r"(?:\$\d+(?:,\d{3})*(?:\.\d{1,2})?"   # money: $145,000 / $1,234.50
        r"|\d{3}-\d{2}-\d{4}"                  # SSN: 123-45-6789
        r"|\d+(?:,\d{3})*(?:\.\d+)?%?"         # plain number or percentage
        r'|"[^"]+")'                            # quoted string
    )

    # Natural-language terms (regex fragments) that may introduce each field.
    _FIELD_SYNONYMS = {
        "salary": [r"salar(?:y|ies)", r"pay"],
        "compensation": [r"compensation", r"bonus", r"stock options?", r"equity"],
        "ssn": [r"ssn", r"social security(?: number)?"],
        "address": [r"(?:home|mailing) address", r"address"],
        "performance_review": [r"performance(?: review| rating)?s?"],
    }

    def __init__(self, access_policy_path: str):
        """Load the access control policy from a JSON file.

        Stores the parsed policy in ``self.policy`` and initializes an
        in-memory ``self.audit_log`` for tracking access attempts.
        """
        with open(access_policy_path, "r", encoding="utf-8") as f:
            self.policy: Dict[str, Any] = json.load(f)
        self.audit_log: List[Dict[str, Any]] = []

    def _field_terms(self, field: str) -> List[str]:
        """Regex fragments that may introduce ``field`` in free text."""
        return self._FIELD_SYNONYMS.get(field, [re.escape(field.replace("_", " "))])

    def can_view_document(self, role: str, document: Dict[str, Any]) -> bool:
        """Check if ``role`` can view a document based on its sensitivity tier.

        Looks up the document's ``sensitivity`` in ``policy["document_access"]``
        and returns whether ``role`` is in the allowed list. Unknown or missing
        tiers fail closed (access denied).
        """
        sensitivity = document.get("sensitivity")
        allowed_roles = self.policy.get("document_access", {}).get(sensitivity, [])
        return role in allowed_roles

    def can_view_field(self, role: str, field_name: str) -> bool:
        """Check if ``role`` can view a (possibly sensitive) field.

        A field not listed under ``policy["sensitive_fields"]`` is treated as
        non-sensitive and is viewable by everyone. A sensitive field is viewable
        only by the roles in its ``visibility`` list.
        """
        sensitive_fields = self.policy.get("sensitive_fields", {})
        if field_name not in sensitive_fields:
            return True
        return role in sensitive_fields[field_name].get("visibility", [])

    def redact_response(self, role: str, response: str) -> str:
        """Redact sensitive fields from a free-text response.

        For every sensitive field this ``role`` may NOT view, the value that
        follows a mention of that field (e.g. "salary is $145,000") is replaced
        with ``[REDACTED]``. SSN-formatted numbers are always redacted when the
        role cannot view ``ssn``. Fields the role can view are left untouched.
        Each redaction that actually fires is recorded in the audit log.
        """
        if not response:
            return response

        redacted = response
        sensitive_fields = self.policy.get("sensitive_fields", {})
        for field, meta in sensitive_fields.items():
            if self.can_view_field(role, field):
                continue

            before = redacted
            for term in self._field_terms(field):
                pattern = re.compile(
                    r"(\b" + term + r"\b\s*(?:is|are|was|of|:|=|->)?\s*)"
                    r"(" + self._VALUE + r")",
                    re.IGNORECASE,
                )
                redacted = pattern.sub(r"\1[REDACTED]", redacted)

            # SSN has an unambiguous format, so redact it wherever it appears.
            if field == "ssn":
                redacted = re.sub(r"\b\d{3}-\d{2}-\d{4}\b", "[REDACTED]", redacted)

            if redacted != before and meta.get("audit_log"):
                self.log_access(role, resource="response", allowed=False, field=field)

        return redacted

    def log_access(self, role: str, resource: str, allowed: bool, field: str = None):
        """Append a structured access-attempt entry to the audit log.

        Records timestamp, role, resource, field (if any), and whether the
        access was allowed, then emits a log line.
        """
        entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "role": role,
            "resource": resource,
            "field": field,
            "allowed": allowed,
        }
        self.audit_log.append(entry)
        logger.info(
            "ACCESS %s role=%s resource=%s field=%s",
            "ALLOW" if allowed else "DENY",
            role,
            resource,
            field,
        )

    def filter_documents(
        self, role: str, documents: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Return only the documents ``role`` is permitted to view.

        For each document, checks ``can_view_document``, records the attempt in
        the audit log, and keeps only the documents the role may view.
        """
        visible: List[Dict[str, Any]] = []
        for document in documents:
            allowed = self.can_view_document(role, document)
            resource = document.get("id") or document.get("title") or "unknown"
            self.log_access(role, resource=resource, allowed=allowed)
            if allowed:
                visible.append(document)
        return visible

    def get_audit_log(self) -> List[Dict[str, Any]]:
        """Return audit log entries."""
        return self.audit_log


# ============================================================================
# TASK 2: Implement RateLimiter
# ============================================================================


class RateLimiter:
    """Rate limit queries per user per minute."""

    def __init__(self, max_queries_per_minute: int = 30):
        """Initialize the rate limiter and per-user query-time tracking."""
        self.max_queries_per_minute = max_queries_per_minute
        self.user_query_times: Dict[str, List[float]] = {}  # {user_id: [timestamps]}

    def _recent_queries(self, user_id: str) -> List[float]:
        """Return this user's query timestamps within the last 60 seconds.

        Also prunes older timestamps from storage so the lists stay bounded.
        """
        cutoff = time() - 60
        recent = [t for t in self.user_query_times.get(user_id, []) if t > cutoff]
        self.user_query_times[user_id] = recent
        return recent

    def is_allowed(self, user_id: str) -> bool:
        """Check if user can make another query.

        Counts the user's queries in the trailing 60-second window. If under the
        limit, records the current query and allows it; otherwise denies it.
        Returns True if allowed, False if the rate limit is exceeded.
        """
        recent = self._recent_queries(user_id)
        if len(recent) < self.max_queries_per_minute:
            recent.append(time())
            return True
        return False

    def get_remaining_queries(self, user_id: str) -> int:
        """Get remaining queries for user in current minute.

        Returns ``max_queries_per_minute`` minus the number of queries in the
        last 60 seconds (never negative).
        """
        return max(0, self.max_queries_per_minute - len(self._recent_queries(user_id)))


# ============================================================================
# TASK 3: Implement CostEnforcer
# ============================================================================


class CostEnforcer:
    """Enforce cost limits per user/role."""

    # Default monthly budget per role (USD).
    DEFAULT_ROLE_BUDGETS = {
        "engineer": 100.0,
        "manager": 500.0,
        "hr": 200.0,
        "finance": 500.0,
        "executive": 1000.0,
    }

    def __init__(self, policy_path: str = None):
        """Initialize role budgets and per-user spending tracking.

        Uses the built-in monthly budgets by default. If ``policy_path`` is
        provided and contains a ``role_budgets`` object, those values override
        the defaults.
        """
        self.role_budgets: Dict[str, float] = dict(self.DEFAULT_ROLE_BUDGETS)
        if policy_path:
            try:
                with open(policy_path, "r", encoding="utf-8") as f:
                    self.role_budgets.update(json.load(f).get("role_budgets", {}))
            except (OSError, json.JSONDecodeError):
                pass  # fall back to the default budgets
        # Unknown users/roles fall back to the most conservative (lowest) budget.
        self.default_budget: float = min(self.role_budgets.values())
        self.user_spending: Dict[str, Dict[str, Any]] = {}  # {user_id: {role, total}}

    def _budget_for(self, user_id: str, role: str = None) -> float:
        """Resolve the budget for a user, preferring their recorded role."""
        if user_id in self.user_spending:
            role = self.user_spending[user_id]["role"]
        if role in self.role_budgets:
            return self.role_budgets[role]
        return self.default_budget

    def add_cost(self, user_id: str, role: str, cost: float):
        """Record cost for user.

        Creates the user's entry on first sight (recording their role), then
        accumulates ``cost`` into their running total.
        """
        if user_id not in self.user_spending:
            self.user_spending[user_id] = {"role": role, "total": 0.0}
        self.user_spending[user_id]["total"] += cost

    def can_afford_query(
        self, user_id: str, estimated_cost: float, role: str = None
    ) -> bool:
        """Check if the user has enough remaining budget for a query.

        ``role`` is optional: if the user has spent before, their recorded role
        is used; otherwise ``role`` (when given) selects the budget, falling
        back to the most conservative default budget when neither is available.
        """
        return estimated_cost <= self.get_budget_remaining(user_id, role)

    def get_budget_remaining(self, user_id: str, role: str = None) -> float:
        """Return the user's remaining budget (budget minus spending, never < 0)."""
        spent = self.user_spending.get(user_id, {}).get("total", 0.0)
        return max(0.0, self._budget_for(user_id, role) - spent)


# ============================================================================
# TASK 4: Integrate with Week 5 Agent
# ============================================================================

# Once you have implemented the three classes above, open your copied
# app_starter.py and update the Agent class to use them:
#
# 1. In Agent.__init__, add:
#       self.access_controller = AccessController("data/access_control.json")
#       self.rate_limiter = RateLimiter(max_queries_per_minute=30)
#       self.cost_enforcer = CostEnforcer()
#
# 2. Update Agent.query() to accept user_id and user_role parameters:
#       def query(self, user_query: str, user_id: str, user_role: str = "engineer")
#
# 3. At the start of query(), add guardrail checks:
#       if not self.rate_limiter.is_allowed(user_id):
#           return {"error": "Rate limit exceeded"}
#       if not self.cost_enforcer.can_afford_query(user_id, estimated_cost=0.01):
#           return {"error": "Budget exceeded"}
#
# 4. After getting the LLM answer, redact sensitive fields:
#       answer = self.access_controller.redact_response(user_role, answer)
#
# 5. After each query, track actual cost:
#       self.cost_enforcer.add_cost(user_id, user_role, actual_cost)


# ============================================================================
# TASK 5: Test Your Implementation
# ============================================================================

# A basic test suite is provided below to help you verify your implementation.
# Run it with: python3 access_control_starter.py
# You are free to modify or extend these tests as you see fit.

if __name__ == "__main__":
    """Quick test of access control functionality."""

    # Test AccessController
    print("Testing AccessController...")
    controller = AccessController("data/access_control.json")

    assert not controller.can_view_field(
        "engineer", "salary"
    ), "Engineer should not see salary"
    assert controller.can_view_field("hr", "salary"), "HR should see salary"
    assert controller.can_view_field("manager", "salary"), "Manager should see salary"
    assert not controller.can_view_field(
        "engineer", "ssn"
    ), "Engineer should not see SSN"
    print("  can_view_field: PASSED")

    docs = [
        {"id": "doc1", "sensitivity": "Public", "content": "Mission statement"},
        {"id": "doc2", "sensitivity": "Confidential", "content": "Salary ranges"},
    ]
    visible = controller.filter_documents("engineer", docs)
    assert (
        len(visible) == 1 and visible[0]["id"] == "doc1"
    ), "Engineer should only see Public doc"
    print("  filter_documents: PASSED")

    # Test RateLimiter
    print("\nTesting RateLimiter...")
    limiter = RateLimiter(max_queries_per_minute=3)
    assert limiter.is_allowed("user1"), "First query should be allowed"
    assert limiter.is_allowed("user1"), "Second query should be allowed"
    assert limiter.is_allowed("user1"), "Third query should be allowed"
    assert not limiter.is_allowed("user1"), "Fourth query should be blocked"
    print("  is_allowed: PASSED")

    # Test CostEnforcer
    print("\nTesting CostEnforcer...")
    enforcer = CostEnforcer()
    assert enforcer.can_afford_query(
        "user1", 50.0
    ), "Should afford $50 within $100 budget"
    enforcer.add_cost("user1", "engineer", 50.0)
    assert enforcer.can_afford_query(
        "user1", 49.0
    ), "Should afford $49 with $50 remaining"
    assert not enforcer.can_afford_query(
        "user1", 51.0
    ), "Should not afford $51 with $50 remaining"
    print("  can_afford_query: PASSED")

    print("\nAll tests passed!")
