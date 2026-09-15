"""Severity assignment (Critical 4 / High 3 / Medium 2 / Low 1).

Rule-based mapping from category → base severity, with escalation hooks.
This is your section's "Assign Severity Levels" step. Keeping it as a table
makes it defensible in the demo: every score traces to a rule, not a vibe.
"""
from __future__ import annotations

from .types import Category, Severity

# Base severity per issue category.
_BASE: dict[Category, Severity] = {
    Category.SECURITY: Severity.CRITICAL,
    Category.BUG: Severity.HIGH,
    Category.API_MISUSE: Severity.HIGH,
    Category.PERFORMANCE: Severity.MEDIUM,
    Category.MISSING_TESTS: Severity.MEDIUM,
    Category.CODE_SMELL: Severity.LOW,
    Category.MISSING_DOCS: Severity.LOW,
}


def assign_severity(category: Category, *, confirmed_by_both: bool = False) -> Severity:
    """Return a severity for a finding.

    confirmed_by_both escalates one level when BOTH static analysis and the
    LLM flagged the same issue — agreement is a stronger signal.
    """
    base = _BASE.get(category, Severity.LOW)
    if confirmed_by_both:
        return _escalate(base)
    return base


def _escalate(sev: Severity) -> Severity:
    order = [Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]
    i = order.index(sev)
    return order[min(i + 1, len(order) - 1)]


def score(findings) -> int:
    """Overall review score 0-100. Starts at 100, subtracts per finding by
    severity. Higher score = healthier change. Clamped to [0, 100]."""
    penalty = {
        Severity.CRITICAL: 40,
        Severity.HIGH: 20,
        Severity.MEDIUM: 8,
        Severity.LOW: 3,
    }
    total = 100 - sum(penalty[f.severity] for f in findings)
    return max(0, min(100, total))
