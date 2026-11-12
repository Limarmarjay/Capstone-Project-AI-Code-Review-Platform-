"""Typed data structures for the LLM review section.

These mirror the agreed repo schemas (schemas/analysis_input.json and
schemas/review_output.json). Keeping them as dataclasses gives your code
validation and autocomplete instead of passing raw dicts around.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


# ---- Controlled vocabularies (agree these with #2 and #4) --------------

class Category(str, Enum):
    BUG = "bug"
    SECURITY = "security"
    CODE_SMELL = "code_smell"
    PERFORMANCE = "performance"
    MISSING_TESTS = "missing_tests"
    API_MISUSE = "api_misuse"
    MISSING_DOCS = "missing_docs"


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def level(self) -> int:
        return {"critical": 4, "high": 3, "medium": 2, "low": 1}[self.value]


class Source(str, Enum):
    STATIC = "static"   # only static analysis flagged it
    LLM = "llm"         # only the LLM flagged it
    COMBINED = "combined"  # both flagged the same line


class LLMStatus(str, Enum):
    SUCCESS = "success"
    INVALID_RESPONSE = "invalid_response"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"
    PARTIAL = "partial"  # review()'s time budget ran out before every
    # file got an LLM pass, but at least one file did succeed; the rest
    # are static-only. Distinct from TIMEOUT (a specific call was
    # attempted and didn't come back in time) and SKIPPED (redaction
    # wasn't confirmed, so the LLM was never called at all). If no file
    # ever succeeds, review() reports TIMEOUT instead of this -- a
    # budget that ran out without a single success isn't meaningfully
    # "partial," it's just the LLM being unavailable.


# ---- Input side (from #2) ----------------------------------------------

@dataclass
class StaticFinding:
    finding_id: str
    tool: str
    rule_id: str
    category: str
    message: str
    file_path: str
    line: int
    severity: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StaticFinding":
        return cls(
            finding_id=d["finding_id"],
            tool=d["tool"],
            rule_id=d.get("rule_id", ""),
            category=d.get("category", Category.CODE_SMELL.value),
            message=d.get("message", ""),
            file_path=d["file_path"],
            line=int(d.get("line", 0)),
            severity=d.get("severity"),
        )

@dataclass
class Hunk:
    hunk_id: str
    start_line: int
    end_line: int
    diff: str
    added_lines: list[int] = field(default_factory=list)
    removed_lines: list[int] = field(default_factory=list)
    context_lines: list[int] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Hunk":
        return cls(
            hunk_id=d.get("hunk_id", ""),
            start_line=int(d.get("start_line", 0)),
            end_line=int(d.get("end_line", 0)),
            diff=d.get("diff", ""),
            added_lines=d.get("added_lines", []),
            removed_lines=d.get("removed_lines", []),
            context_lines=d.get("context_lines", []),
        )


@dataclass
class ReviewFile:
    file_path: str
    language: str
    hunks: list[Hunk]
    static_findings: list[StaticFinding]

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ReviewFile":
        return cls(
            file_path=d["file_path"],
            language=d.get("language", "python"),
            hunks=[Hunk.from_dict(h) for h in d.get("hunks", [])],
            static_findings=[
                StaticFinding.from_dict(f) for f in d.get("static_findings", [])
            ],
        )


@dataclass
class AnalysisInput:
    submission_id: str
    source_type: str
    repository: str
    files: list[ReviewFile]
    redaction_applied: bool = False

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "AnalysisInput":
        return cls(
            submission_id=d["submission_id"],
            source_type=d.get("source_type", "unknown"),
            repository=d.get("repository", ""),
            files=[ReviewFile.from_dict(f) for f in d.get("files", [])],
            redaction_applied=bool(d.get("redaction_applied", False)),
        )


# ---- Output side (to #4) -----------------------------------------------

@dataclass
class Finding:
    finding_id: str
    source: Source
    category: Category
    severity: Severity
    file_path: str
    line: int
    title: str
    explanation: str
    suggested_fix: str
    static_finding_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "source": self.source.value,
            "category": self.category.value,
            "severity": self.severity.value,
            "severity_level": self.severity.level,
            "file_path": self.file_path,
            "line": self.line,
            "title": self.title,
            "explanation": self.explanation,
            "suggested_fix": self.suggested_fix,
            "static_finding_ids": self.static_finding_ids,
        }


@dataclass
class ReviewOutput:
    submission_id: str
    review_status: str
    llm_status: LLMStatus
    overall_score: int
    summary: str
    findings: list[Finding]

    def to_dict(self) -> dict[str, Any]:
        return {
            "submission_id": self.submission_id,
            "review_status": self.review_status,
            "llm_status": self.llm_status.value,
            "overall_score": self.overall_score,
            "summary": self.summary,
            "findings": [f.to_dict() for f in self.findings],
        }
