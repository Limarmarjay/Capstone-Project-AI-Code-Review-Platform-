"""Shared data models for the repository/diff/review pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ChangeType(str, Enum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class InputType(str, Enum):
    GIT_DIFF = "git_diff"
    PATCH_FILE = "patch_file"
    PULL_REQUEST = "pull_request"


@dataclass
class DiffLine:
    """A single line within a hunk."""
    line_type: str  # "add", "del", "context"
    content: str
    source_line_no: int | None = None
    target_line_no: int | None = None


@dataclass
class Hunk:
    source_start: int
    source_length: int
    target_start: int
    target_length: int
    lines: list[DiffLine] = field(default_factory=list)

    @property
    def added_lines(self) -> list[DiffLine]:
        return [l for l in self.lines if l.line_type == "add"]

    @property
    def removed_lines(self) -> list[DiffLine]:
        return [l for l in self.lines if l.line_type == "del"]


@dataclass
class FileDiff:
    path: str
    change_type: ChangeType
    old_path: str | None = None
    hunks: list[Hunk] = field(default_factory=list)
    is_binary: bool = False

    @property
    def additions(self) -> int:
        return sum(len(h.added_lines) for h in self.hunks)

    @property
    def deletions(self) -> int:
        return sum(len(h.removed_lines) for h in self.hunks)

    @property
    def extension(self) -> str:
        return self.path.rsplit(".", 1)[-1].lower() if "." in self.path else ""


@dataclass
class ParsedDiff:
    files: list[FileDiff] = field(default_factory=list)

    @property
    def total_additions(self) -> int:
        return sum(f.additions for f in self.files)

    @property
    def total_deletions(self) -> int:
        return sum(f.deletions for f in self.files)


@dataclass
class CheckFinding:
    tool: str
    path: str
    line: int | None
    severity: Severity
    message: str
    rule_id: str | None = None


@dataclass
class CheckResult:
    tool: str
    ran: bool
    findings: list[CheckFinding] = field(default_factory=list)
    error: str | None = None
    skipped_reason: str | None = None


@dataclass
class ReviewComment:
    path: str
    line: int | None
    severity: Severity
    comment: str


@dataclass
class AIReviewResult:
    summary: str
    comments: list[ReviewComment] = field(default_factory=list)
    raw_model_output: str | None = None
    error: str | None = None


@dataclass
class SecretFinding:
    path: str
    line: int | None
    rule_id: str
    redacted: bool = True


@dataclass
class CombinedFinding:
    source: str  # e.g. "static:ruff", "static:bandit", "ai"
    path: str
    line: int | None
    severity: Severity
    message: str
    rule_id: str | None = None


@dataclass
class InlineComment:
    path: str
    line: int | None
    severity: Severity
    body: str
    source: str


@dataclass
class SummaryReport:
    text: str
    total_findings: int
    findings_by_severity: dict = field(default_factory=dict)


@dataclass
class SubmissionReport:
    repo_ref: str
    base_ref: str
    head_ref: str
    diff: ParsedDiff
    input_type: InputType | None = None
    secrets_found: list[SecretFinding] = field(default_factory=list)
    check_results: list[CheckResult] = field(default_factory=list)
    ai_review: AIReviewResult | None = None
    combined_findings: list[CombinedFinding] = field(default_factory=list)
    inline_comments: list[InlineComment] = field(default_factory=list)
    summary_report: SummaryReport | None = None
    persisted_id: str | None = None
    blocked: bool = False
    block_reason: str | None = None
