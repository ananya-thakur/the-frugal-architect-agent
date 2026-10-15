"""Domain models for the iac-scanner unit.

File, IaCFormat, and PrioritizedInventory are defined here as the iac-scanner's
direct input contract; repo-connector will import these when it is built.
Confidence is shared with app-scanner (ADR-003).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

# ── Shared confidence enum (ADR-003) ─────────────────────────────────────────

class Confidence(str, Enum):
    """FIRM = static IaC analysis; POSSIBLE = app-code pattern match."""
    FIRM = "FIRM"
    POSSIBLE = "POSSIBLE"


# ── IaC format enum ───────────────────────────────────────────────────────────

class IaCFormat(str, Enum):
    CDK_PYTHON = "cdk_python"
    CDK_TYPESCRIPT = "cdk_typescript"
    CLOUDFORMATION = "cloudformation"
    SAM = "sam"
    TERRAFORM = "terraform"
    SERVERLESS_YML = "serverless"
    KUBERNETES = "kubernetes"
    DOCKERFILE = "dockerfile"
    UNKNOWN = "unknown"


# ── Input entities (owned by repo-connector boundary) ────────────────────────

@dataclass(frozen=True)
class File:
    """A single repository file passed to iac-scanner."""
    path: str
    content: str
    format: IaCFormat
    size_bytes: int = 0


@dataclass(frozen=True)
class PrioritizedInventory:
    """Pre-sorted file inventory produced by repo-connector (ADR-007)."""
    iac_files: tuple[File, ...]
    app_files: tuple[File, ...]
    total_file_count: int
    scan_token_estimate: int
    repo_root: str = ""

    @classmethod
    def empty(cls) -> PrioritizedInventory:
        return cls(
            iac_files=(),
            app_files=(),
            total_file_count=0,
            scan_token_estimate=0,
        )


# ── Internal intermediate entities ───────────────────────────────────────────

@dataclass
class ParsedResource:
    """One addressable IaC resource extracted by a format-specific parser."""
    id: str
    resource_type: str
    attributes: dict[str, Any]
    file_path: str
    format: IaCFormat
    line_number: int | None = None


@dataclass
class ScanContext:
    """Mutable accumulator for one scan_iac invocation (not returned to callers)."""
    findings: list[FirmFinding] = field(default_factory=list)
    skipped_paths: list[str] = field(default_factory=list)
    parse_errors: list[tuple[str, str]] = field(default_factory=list)
    token_spent: int = 0
    processed_count: int = 0
    formats_seen: set[str] = field(default_factory=set)


# ── Output entities ───────────────────────────────────────────────────────────

class FirmFinding(BaseModel):
    """
    One confirmed frugality gap in one IaC resource.

    Always carries confidence=FIRM (BR-03). Consumed by scoring-engine.
    """

    model_config = ConfigDict(frozen=True)

    law_id: str
    law_title: str
    file_path: str
    resource_id: str
    resource_type: str
    line: int | None
    gap: str
    evidence: str
    signal_id: str
    confidence: Confidence = Confidence.FIRM

    @model_validator(mode="after")
    def _validate_invariants(self) -> FirmFinding:
        if not self.file_path:
            raise ValueError("file_path must not be empty (BR-05)")
        if not self.resource_id:
            raise ValueError("resource_id must not be empty (BR-05)")
        if self.confidence is not Confidence.FIRM:
            raise ValueError("iac-scanner findings must always be FIRM (BR-03)")
        return self


class ScanSummary(BaseModel):
    """Metadata returned alongside findings to enable scope-transparency messages."""

    model_config = ConfigDict(frozen=True)

    files_processed: int
    files_skipped_budget: int
    skipped_paths: list[str]
    parse_errors: list[tuple[str, str]]
    formats_scanned: list[str]
    findings_count: int
