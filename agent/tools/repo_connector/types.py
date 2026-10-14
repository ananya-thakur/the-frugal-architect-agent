"""
Domain entities for the repo-connector unit.

All types form the hexagonal boundary contract (ADR-008) between external
SCM systems and the Frugal Architect Agent.  Types are Python dataclasses;
value objects are frozen; entities (transient, internal-only) are mutable
during construction but treated as read-only once returned.
"""

from __future__ import annotations

import dataclasses
from typing import Literal


@dataclasses.dataclass(frozen=True)
class ScanLimits:
    """
    Immutable pair of enforced cap values loaded once per
    clone_or_read_repo invocation (BR-RC-005).
    """

    file_limit: int   # max files to load content for
    token_budget: int  # max tokens from loaded content

    def __post_init__(self) -> None:
        if self.file_limit <= 0:
            raise ValueError(f"file_limit must be > 0, got {self.file_limit}")
        if self.token_budget <= 0:
            raise ValueError(f"token_budget must be > 0, got {self.token_budget}")


@dataclasses.dataclass(frozen=True)
class RepoRef:
    """Resolved reference to a repository, parsed before file enumeration."""

    raw: str
    kind: Literal["url", "path"]
    provider: str | None  # set only when kind == "url"
    subdirectory: str | None  # forward-slash notation regardless of source OS

    def __post_init__(self) -> None:
        if self.kind == "path" and self.provider is not None:
            raise ValueError("provider must be None for kind='path'")


@dataclasses.dataclass(frozen=True)
class FileRecord:
    """
    One file entry within a repository inventory.
    Immutable once constructed.

    Invariants (BR-RC-008, domain-entities spec):
    - content is None iff actual_tokens is None (stub vs loaded)
    - path is always relative (no leading '/')
    - tier is always 'iac' or 'app'; 'unknown' is prohibited
    """

    path: str
    tier: Literal["iac", "app"]
    estimated_tokens: int  # pre-load estimate: characters ÷ 4 (BR-RC-010)
    content: str | None    # None for stub entries (beyond cap)
    actual_tokens: int | None  # None when content is None

    def __post_init__(self) -> None:
        if (self.content is None) != (self.actual_tokens is None):
            raise ValueError(
                "content and actual_tokens must both be None or both be non-None; "
                f"got content={self.content is None!r} actual_tokens={self.actual_tokens is None!r}"
            )
        if self.path.startswith("/"):
            raise ValueError(
                f"FileRecord.path must be relative (no leading '/'), got: {self.path!r}"
            )
        if self.estimated_tokens < 0:
            raise ValueError(
                f"estimated_tokens must be >= 0, got {self.estimated_tokens}"
            )


@dataclasses.dataclass
class RawInventory:
    """
    Intermediate, unordered file collection produced immediately after
    enumeration — before tier classification and prioritization.
    Never exposed outside repo-connector.
    """

    repo_ref: RepoRef
    files: list[FileRecord]
    total_count: int  # total files before subdirectory filter or cap


@dataclasses.dataclass
class PrioritizedInventory:
    """
    Ordered file list after IaC-first sort and file-count cap application.
    Never exposed outside repo-connector.
    """

    files: list[FileRecord]  # IaC-first (alphabetical), then app (alphabetical)
    iac_count: int     # total IaC files discovered before cap
    app_count: int     # total app files discovered before cap
    iac_included: int  # IaC files within the file-count cap
    app_included: int  # app files within the file-count cap
    truncated_count: int  # files excluded by file-count cap; 0 when not capped


@dataclasses.dataclass(frozen=True)
class ScopeNote:
    """
    Scope boundary record embedded in every RepoInventory.
    Always present; never null (BR-RC-006).
    Agent-assembly streams this to the user before dispatching scanners.
    """

    capped: bool
    cap_type: Literal["file_count", "token_budget", "none"]
    files_included: int   # files with loaded content
    files_seen: int       # total files discovered (after subdirectory filter)
    tokens_consumed: int  # actual tokens from loaded content (BR-RC-010)
    layers_covered: Literal["full", "IaC-complete", "IaC-and-app", "IaC-partial"]
    subdirectory: str | None  # filter applied, if any

    def __post_init__(self) -> None:
        if self.capped and self.cap_type == "none":
            raise ValueError("cap_type cannot be 'none' when capped is True")
        if not self.capped and self.cap_type != "none":
            raise ValueError("cap_type must be 'none' when capped is False")
        if self.files_included > self.files_seen:
            raise ValueError(
                f"files_included ({self.files_included}) must be <= "
                f"files_seen ({self.files_seen})"
            )


@dataclasses.dataclass(frozen=True)
class ConnectivityResult:
    """Result of a single, non-retrying probe by check_connectivity (BR-RC-009)."""

    connected: bool
    provider: str | None  # provider name when connected; None otherwise
    hint: str | None      # human-readable onboarding instruction when not connected


@dataclasses.dataclass(frozen=True)
class RepoInventory:
    """
    Aggregate root — the primary output of clone_or_read_repo.
    The only type that crosses the hexagonal boundary outward to the rest
    of the agent.  Downstream components hold a read-only reference.

    Consumers:
    - iac-scanner  : iterates files where tier=='iac' and content is not None
    - app-scanner  : iterates files where tier=='app' and content is not None
    - agent-assembly: reads scope_note for user-facing scope message
    """

    repo_ref: RepoRef
    files: list[FileRecord]  # IaC-first, alphabetical within tier
    scope_note: ScopeNote    # always present (BR-RC-006)
    provider: str            # SCM provider name or 'local'
    file_count: int          # count of FileRecords where content is not None

    def __post_init__(self) -> None:
        if self.scope_note is None:
            raise ValueError("scope_note must never be None (BR-RC-006)")
        loaded = sum(1 for f in self.files if f.content is not None)
        if loaded != self.file_count:
            raise ValueError(
                f"file_count ({self.file_count}) must equal count of loaded "
                f"FileRecords ({loaded})"
            )


class SCMNotConnectedError(Exception):
    """
    Raised by clone_or_read_repo when check_connectivity returns connected=False.
    Caught by type in agent-assembly; never inspected by string content (BR-RC-003).

    hint is always non-empty and suitable for direct display to the user.
    """

    def __init__(self, hint: str, provider_attempted: str | None = None) -> None:
        super().__init__(hint)
        self.hint: str = hint
        self.provider_attempted: str | None = provider_attempted
