"""Domain entities for the app-scanner unit.

File and PrioritizedInventory mirror the shapes defined in the repo-connector
domain entities doc. They are defined here so app-scanner compiles standalone;
once repo-connector is implemented these become imports from that unit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


# ---------------------------------------------------------------------------
# Consumed from repo-connector (placeholder until that unit is built)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class File:
    """A single file record from the prioritized inventory."""

    path: str        # Repo-relative path (e.g. "src/api/users.py")
    content: str     # Full UTF-8 decoded content
    extension: str   # Lowercased extension including dot (e.g. ".py")
    size_bytes: int


@dataclass
class PrioritizedInventory:
    """Produced by repo-connector after IaC-first prioritisation and cap enforcement."""

    iac_files: list[File]
    app_files: list[File]
    files_scanned_count: int
    files_skipped_count: int
    scan_cap_reached: bool
    repo_root: str
    scope_note: str = ""


# ---------------------------------------------------------------------------
# Owned by app-scanner
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PatternMatch:
    """Ephemeral intermediate produced by match_pattern for one (File, signal) pair.

    Never exposed outside the unit — converted to PossibleFinding before
    scan_app_antipatterns returns.
    """

    signal_id: str
    file_path: str           # Repo-relative (no leading /)
    line_number: int | None  # 1-indexed; None for whole-file heuristics
    matched_text: str        # The matched line or expression
    context_snippet: str     # Surrounding lines (context_lines before + after)


@dataclass(frozen=True)
class PossibleFinding:
    """A single app-code anti-pattern instance with POSSIBLE confidence (BR-APP-001)."""

    finding_id: str                  # Deterministic UUID v5 (BR-APP-009)
    law_id: int                      # Law number 1–7
    law_title: str                   # Verbatim from rubric (BR-APP-004)
    anti_pattern_type: str           # Display name from signal
    file_path: str                   # Repo-relative path
    line_number: int | None          # 1-indexed; None for structural findings
    evidence: str                    # matched_text + context_snippet
    confidence: Literal["POSSIBLE"]  # INVARIANT (BR-APP-001)
    runtime_caveat: str              # Mandatory disclaimer (BR-APP-003)
    cost_impact_explanation: str     # Why this costs money


@dataclass
class ScanSummary:
    """Scope-transparency record always included in AppScannerResult (BR-APP-010)."""

    files_scanned: int            # Files successfully processed
    files_skipped: int            # Files skipped (parse error + cap overflow)
    anti_patterns_searched: int   # Signal types applied (should equal 10)
    cap_reached: bool             # True if inventory cap was hit
    cap_note: str | None          # Human-readable cap message when cap_reached
    systemic_pattern_notes: list[str] = field(default_factory=list)


@dataclass
class AppScannerResult:
    """Return type of scan_app_antipatterns. summary is always present (BR-APP-010)."""

    findings: list[PossibleFinding]
    summary: ScanSummary
