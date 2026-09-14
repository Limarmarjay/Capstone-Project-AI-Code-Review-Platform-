"""LLM Review Logic — Capstone AI Code Review Platform (Issue #3).

Owns the middle of the pipeline: consumes parsed diff + static findings
from Static Analysis (#2), enriches with an LLM, assigns severity, and
emits combined findings for the UI/Reporting layer (#4).
"""
from .pipeline import review
from .types import ReviewOutput, Finding, LLMStatus, Severity

__all__ = ["review", "ReviewOutput", "Finding", "LLMStatus", "Severity"]
