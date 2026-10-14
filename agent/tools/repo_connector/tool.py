"""
repo-connector Strands tool — clone_or_read_repo.

This module is the hexagonal adapter (ADR-008) between the Frugal Architect
Agent and external source-control systems.  It is the ONLY component
responsible for:
  - IaC-first file prioritization (ADR-007)
  - Scan cap enforcement (BR-RC-005)
  - SCM connectivity detection and SCMNotConnectedError raising (BR-RC-003)
  - ScopeNote authoring (BR-RC-006)

Downstream scanners (iac-scanner, app-scanner) receive a pre-prioritized
RepoInventory and must never re-sort or modify it.

Strands decorator is optional at import time so unit tests can exercise
the business logic without the full Strands SDK installed.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from agent.tools.repo_connector._patterns import classify_tier
from agent.tools.repo_connector.types import (
    ConnectivityResult,
    FileRecord,
    PrioritizedInventory,
    RawInventory,
    RepoInventory,
    RepoRef,
    SCMNotConnectedError,
    ScanLimits,
    ScopeNote,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Strands SDK import — graceful fallback so unit tests run without the SDK
# ---------------------------------------------------------------------------
try:
    from strands import tool as _strands_tool  # type: ignore[import]

    def _tool_decorator(fn):  # type: ignore[return]
        return _strands_tool(fn)

except ImportError:  # pragma: no cover — only absent in test environments
    def _tool_decorator(fn):  # type: ignore[return]
        return fn


# ---------------------------------------------------------------------------
# OTEL tracer — optional; no-ops when OTEL SDK is not initialised
# ---------------------------------------------------------------------------
try:
    from opentelemetry import trace as _otel_trace  # type: ignore[import]
    _tracer = _otel_trace.get_tracer(__name__)
except ImportError:  # pragma: no cover
    _tracer = None  # type: ignore[assignment]


def _current_span() -> Any:
    """Return the active OTEL span, or a no-op object when OTEL is absent."""
    if _tracer is None:
        return _NoOpSpan()
    try:
        from opentelemetry import trace as _t  # type: ignore[import]
        return _t.get_current_span()
    except Exception:  # pragma: no cover
        return _NoOpSpan()


class _NoOpSpan:
    """Minimal no-op span for environments without OTEL."""

    def set_attribute(self, *_: Any, **__: Any) -> None:  # noqa: D401
        pass


# ---------------------------------------------------------------------------
# DEFAULT SCAN CAP VALUES (BR-RC-005)
# The BLM specifies 200 files / 50 000 tokens as defaults.
# agent/config.py has higher defaults (500 / 100 000) for the overall agent;
# these conservative defaults apply when get_scan_limits() cannot read env vars.
# ---------------------------------------------------------------------------
_DEFAULT_FILE_LIMIT: int = 200
_DEFAULT_TOKEN_BUDGET: int = 50_000


# ---------------------------------------------------------------------------
# Step 1 — get_scan_limits
# ---------------------------------------------------------------------------

def get_scan_limits() -> ScanLimits:
    """
    Load scan cap values from environment variables (BR-RC-005).

    Falls back to safe defaults (200 files, 50 000 tokens) on missing or
    non-integer values and emits a warning span attribute.
    """
    span = _current_span()
    file_limit = _DEFAULT_FILE_LIMIT
    token_budget = _DEFAULT_TOKEN_BUDGET

    raw_file = os.environ.get("SCAN_FILE_LIMIT", "")
    if raw_file:
        try:
            file_limit = int(raw_file)
            if file_limit <= 0:
                raise ValueError("non-positive")
        except ValueError:
            logger.warning(
                "SCAN_FILE_LIMIT=%r is not a positive integer; using default %d",
                raw_file,
                _DEFAULT_FILE_LIMIT,
            )
            span.set_attribute("repo_connector.scan_limit_default_used", "file_limit")
            file_limit = _DEFAULT_FILE_LIMIT
    else:
        span.set_attribute("repo_connector.scan_limit_default_used", "file_limit")

    raw_budget = os.environ.get("SCAN_TOKEN_BUDGET", "")
    if raw_budget:
        try:
            token_budget = int(raw_budget)
            if token_budget <= 0:
                raise ValueError("non-positive")
        except ValueError:
            logger.warning(
                "SCAN_TOKEN_BUDGET=%r is not a positive integer; using default %d",
                raw_budget,
                _DEFAULT_TOKEN_BUDGET,
            )
            span.set_attribute("repo_connector.scan_limit_default_used", "token_budget")
            token_budget = _DEFAULT_TOKEN_BUDGET
    else:
        span.set_attribute("repo_connector.scan_limit_default_used", "token_budget")

    span.set_attribute("repo_connector.file_limit", file_limit)
    span.set_attribute("repo_connector.token_budget", token_budget)
    return ScanLimits(file_limit=file_limit, token_budget=token_budget)


# ---------------------------------------------------------------------------
# Step 3 — resolve_repo_ref  (called before connectivity for path detection)
# ---------------------------------------------------------------------------

_URL_PREFIXES: tuple[str, ...] = ("http://", "https://", "git@")

_PROVIDER_PATTERNS: list[tuple[str, str]] = [
    ("github.com", "github"),
    ("gitlab.com", "gitlab"),
    ("bitbucket.org", "bitbucket"),
    ("codecommit", "codecommit"),
]


def _resolve_repo_ref(raw: str, subdirectory: str | None) -> RepoRef:
    """Parse the raw repo string into a typed RepoRef."""
    lower = raw.lower()
    is_url = any(lower.startswith(p) for p in _URL_PREFIXES)

    if not is_url:
        return RepoRef(raw=raw, kind="path", provider=None, subdirectory=subdirectory)

    provider: str | None = None
    for fragment, name in _PROVIDER_PATTERNS:
        if fragment in lower:
            provider = name
            break

    # Normalise subdirectory separator
    if subdirectory:
        subdirectory = subdirectory.replace("\\", "/").strip("/")

    return RepoRef(raw=raw, kind="url", provider=provider, subdirectory=subdirectory)


# ---------------------------------------------------------------------------
# Step 2 — check_connectivity
# ---------------------------------------------------------------------------

def check_connectivity(repo_ref: RepoRef) -> ConnectivityResult:
    """
    Single-probe, non-retrying connectivity check (BR-RC-009).

    For local paths: connected when the path exists as a directory.
    For URL refs: connected when an SCM token is available in the environment.
    Never retries — retry responsibility belongs to the AgentCore integration layer.
    """
    span = _current_span()

    if repo_ref.kind == "path":
        target = Path(repo_ref.raw)
        if target.is_dir():
            span.set_attribute("repo_connector.connectivity", "local_path_ok")
            return ConnectivityResult(connected=True, provider="local", hint=None)
        else:
            msg = (
                f"Local path '{repo_ref.raw}' does not exist or is not a directory. "
                "Provide the absolute path to a repository you have cloned locally."
            )
            span.set_attribute("repo_connector.connectivity", "local_path_missing")
            return ConnectivityResult(connected=False, provider=None, hint=msg)

    # URL-based: look for a SCM token (AgentCore injects these on connection)
    provider = repo_ref.provider or "unknown"
    token = (
        os.environ.get("AGENTCORE_SCM_TOKEN")
        or os.environ.get("GITHUB_TOKEN")
        or os.environ.get("GITLAB_TOKEN")
        or os.environ.get("BITBUCKET_TOKEN")
    )

    if token:
        span.set_attribute("repo_connector.connectivity", f"scm_token_present:{provider}")
        return ConnectivityResult(connected=True, provider=provider, hint=None)

    span.set_attribute("repo_connector.connectivity", f"scm_token_absent:{provider}")
    onboarding_hint = (
        "Your source-control account is not connected. "
        "To connect GitHub (and other providers): open the AgentCore console → "
        "Integrations → Source Control and follow the one-time OAuth flow. "
        "Once connected, re-run your CODEBASE REVIEW request. "
        "Alternatively, switch to DESIGN REVIEW mode — no repository access needed."
    )
    return ConnectivityResult(connected=False, provider=None, hint=onboarding_hint)


# ---------------------------------------------------------------------------
# Step 4 — subdirectory filter  (applied to a file list before prioritization)
# ---------------------------------------------------------------------------

def _apply_subdirectory_filter(
    files: list[FileRecord], subdirectory: str | None
) -> list[FileRecord]:
    """Filter file list to entries under the subdirectory prefix (BR-RC-007)."""
    if not subdirectory:
        return files
    prefix = subdirectory.rstrip("/") + "/"
    return [f for f in files if f.path.startswith(prefix) or f.path == subdirectory]


# ---------------------------------------------------------------------------
# Step 5 — enumerate files from a local path
# ---------------------------------------------------------------------------

def _enumerate_local_files(root: Path) -> list[FileRecord]:
    """
    Walk a local directory tree and build a FileRecord list with tier
    classification and pre-load token estimates.  Content is NOT loaded here.
    """
    records: list[FileRecord] = []
    root_str = str(root)

    for dirpath, _dirnames, filenames in os.walk(root_str):
        for filename in filenames:
            abs_path = os.path.join(dirpath, filename)
            try:
                rel_path = os.path.relpath(abs_path, root_str).replace("\\", "/")
                size = os.path.getsize(abs_path)
                estimated_tokens = max(0, size // 4)
                tier = classify_tier(rel_path)
                records.append(
                    FileRecord(
                        path=rel_path,
                        tier=tier,
                        estimated_tokens=estimated_tokens,
                        content=None,
                        actual_tokens=None,
                    )
                )
            except OSError:
                # Skip files we can't stat (permissions, broken symlinks)
                logger.debug("Skipping unreadable file: %s", abs_path)

    return records


# ---------------------------------------------------------------------------
# Step 6 — prioritise_files  (ADR-007, BR-RC-004)
# ---------------------------------------------------------------------------

def prioritise_files(
    raw_inventory: RawInventory, limits: ScanLimits
) -> PrioritizedInventory:
    """
    Sort files IaC-first (alphabetical within each tier).
    This is the sole enforcement point for ADR-007 ordering.

    All files are kept in the result (BR-RC-008: stubs must remain in the
    inventory).  The file-count cap is enforced during content loading in
    _load_content, not here.  truncated_count reports how many files are
    beyond the cap so callers can derive the cap state.

    Downstream consumers receive the output and MUST NOT re-sort it.
    """
    iac_files = sorted(
        (f for f in raw_inventory.files if f.tier == "iac"),
        key=lambda f: f.path.lower(),
    )
    app_files = sorted(
        (f for f in raw_inventory.files if f.tier == "app"),
        key=lambda f: f.path.lower(),
    )

    iac_count = len(iac_files)
    app_count = len(app_files)
    ordered = iac_files + app_files

    # Compute what would fit within the file-count cap (for counting only)
    truncated_count = max(0, len(ordered) - limits.file_limit)
    within_cap = ordered[: limits.file_limit]

    iac_included = sum(1 for f in within_cap if f.tier == "iac")
    app_included = sum(1 for f in within_cap if f.tier == "app")

    return PrioritizedInventory(
        files=ordered,  # ALL files kept; _load_content enforces the cap
        iac_count=iac_count,
        app_count=app_count,
        iac_included=iac_included,
        app_included=app_included,
        truncated_count=truncated_count,
    )


# ---------------------------------------------------------------------------
# Helper — layers_covered derivation
# ---------------------------------------------------------------------------

def _compute_layers_covered(
    iac_included: int,
    app_included: int,
    iac_count: int,
    capped: bool,
    cap_type: Literal["file_count", "token_budget", "none"],
) -> Literal["full", "IaC-complete", "IaC-and-app", "IaC-partial"]:
    """Derive the layers_covered value from coverage statistics."""
    if not capped:
        return "full"
    if cap_type == "file_count" and iac_included < iac_count:
        # Cap hit inside the IaC tier — zero app files loaded
        return "IaC-partial"
    if app_included == 0:
        # All IaC loaded, but token cap stopped us before any app code
        return "IaC-complete"
    # Both IaC and some app files loaded (file-count cap mid-app-code)
    return "IaC-and-app"


# ---------------------------------------------------------------------------
# Step 7 — load file content up to caps
# ---------------------------------------------------------------------------

def _load_content(
    files: list[FileRecord], root: Path, limits: ScanLimits
) -> tuple[list[FileRecord], bool, Literal["file_count", "token_budget", "none"]]:
    """
    Iterate through the prioritized file list and load content up to caps.

    Enforces both caps:
    - file-count cap: stop loading after limits.file_limit files have content
    - token-budget cap: stop loading when tokens_consumed would exceed limit

    Files after either cap are retained as path-only stubs (BR-RC-008).

    Returns:
        (result_files, capped, cap_type)
        result_files: FileRecords with content for loaded files; stubs for the rest
        capped: True when either cap was reached
        cap_type: which cap was hit first (or 'none')
    """
    result: list[FileRecord] = []
    tokens_consumed = 0
    loaded_count = 0
    capped = False
    cap_type: Literal["file_count", "token_budget", "none"] = "none"

    for file_rec in files:
        if capped:
            # Retain as stub (BR-RC-008) — must not drop from the list
            result.append(file_rec)
            continue

        # File-count cap check (step 7 of the BLM algorithm)
        if loaded_count >= limits.file_limit:
            capped = True
            cap_type = "file_count"
            result.append(file_rec)
            continue

        abs_path = root / file_rec.path
        try:
            text = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            logger.debug("Could not read %s; retaining as stub", abs_path)
            result.append(file_rec)
            continue

        actual = len(text) // 4
        if tokens_consumed + actual > limits.token_budget:
            # Token budget would be exceeded — mark cap and keep as stub
            capped = True
            cap_type = "token_budget"
            result.append(file_rec)
            continue

        tokens_consumed += actual
        loaded_count += 1
        loaded = FileRecord(
            path=file_rec.path,
            tier=file_rec.tier,
            estimated_tokens=file_rec.estimated_tokens,
            content=text,
            actual_tokens=actual,
        )
        result.append(loaded)

    return result, capped, cap_type


# ---------------------------------------------------------------------------
# Step 8 — build ScopeNote
# ---------------------------------------------------------------------------

def _build_scope_note(
    files: list[FileRecord],
    files_seen: int,
    capped: bool,
    cap_type: Literal["file_count", "token_budget", "none"],
    iac_count: int,
    prioritized: PrioritizedInventory,
    subdirectory: str | None,
) -> ScopeNote:
    files_included = sum(1 for f in files if f.content is not None)
    tokens_consumed = sum(
        f.actual_tokens for f in files if f.actual_tokens is not None
    )
    iac_included = sum(1 for f in files if f.tier == "iac" and f.content is not None)
    app_included = sum(1 for f in files if f.tier == "app" and f.content is not None)

    layers_covered = _compute_layers_covered(
        iac_included=iac_included,
        app_included=app_included,
        iac_count=iac_count,
        capped=capped,
        cap_type=cap_type,
    )

    return ScopeNote(
        capped=capped,
        cap_type=cap_type,
        files_included=files_included,
        files_seen=files_seen,
        tokens_consumed=tokens_consumed,
        layers_covered=layers_covered,
        subdirectory=subdirectory,
    )


# ---------------------------------------------------------------------------
# Internal orchestrator (testable without Strands)
# ---------------------------------------------------------------------------

def _clone_or_read_repo_impl(
    repo_ref_raw: str,
    subdirectory: str | None = None,
) -> RepoInventory:
    """
    Core 9-step algorithm from business-logic-model-repo-connector.
    Raises SCMNotConnectedError when connectivity fails.
    """
    span = _current_span()

    # Step 1 — read scan limits
    limits = get_scan_limits()

    # Step 3 — resolve reference (before connectivity so we know kind)
    repo_ref = _resolve_repo_ref(repo_ref_raw, subdirectory)

    # Step 2 — check connectivity
    connectivity = check_connectivity(repo_ref)
    if not connectivity.connected:
        raise SCMNotConnectedError(
            hint=connectivity.hint or "SCM integration is not connected.",
            provider_attempted=repo_ref.provider,
        )

    provider = connectivity.provider or "local"
    span.set_attribute("repo_connector.provider", provider)

    # Step 4 — apply subdirectory filter is handled during enumeration
    if subdirectory:
        span.set_attribute("repo_connector.subdirectory", subdirectory)

    # Step 5 — build raw inventory
    root = Path(repo_ref.raw)
    all_files = _enumerate_local_files(root)

    # Apply subdirectory filter (BR-RC-007) before cap and prioritization
    if subdirectory:
        all_files = _apply_subdirectory_filter(all_files, subdirectory)

    total_seen = len(all_files)
    span.set_attribute("repo_connector.files_seen", total_seen)

    raw_inventory = RawInventory(
        repo_ref=repo_ref,
        files=all_files,
        total_count=total_seen,
    )

    # Step 6 — prioritize files (ADR-007)
    prioritized = prioritise_files(raw_inventory, limits)

    # Step 7 — load file content up to caps (file-count AND token-budget)
    # _load_content enforces both caps and retains stubs for files beyond (BR-RC-008)
    loaded_files, capped, final_cap_type = _load_content(
        prioritized.files, root, limits
    )

    if capped:
        span.set_attribute("repo_connector.cap_hit", final_cap_type)

    # Step 8 — write ScopeNote
    scope_note = _build_scope_note(
        files=loaded_files,
        files_seen=total_seen,
        capped=capped,
        cap_type=final_cap_type,
        iac_count=prioritized.iac_count,
        prioritized=prioritized,
        subdirectory=subdirectory,
    )

    span.set_attribute("repo_connector.files_included", scope_note.files_included)
    span.set_attribute("repo_connector.tokens_consumed", scope_note.tokens_consumed)
    span.set_attribute("repo_connector.layers_covered", scope_note.layers_covered)

    # Step 9 — assemble and return RepoInventory
    file_count = sum(1 for f in loaded_files if f.content is not None)
    return RepoInventory(
        repo_ref=repo_ref,
        files=loaded_files,
        scope_note=scope_note,
        provider=provider,
        file_count=file_count,
    )


# ---------------------------------------------------------------------------
# Strands tool registration
# ---------------------------------------------------------------------------

@_tool_decorator
def clone_or_read_repo(
    repo_ref: str,
    subdirectory: str | None = None,
) -> dict:
    """
    Read a source-code repository (read-only) and return a prioritized file
    inventory for the Frugal Architecture analysis.

    Accepts a GitHub/GitLab/Bitbucket/CodeCommit URL or a local filesystem
    path.  IaC files are placed first in the inventory (ADR-007).
    Scan caps (SCAN_FILE_LIMIT, SCAN_TOKEN_BUDGET) are enforced here.

    Args:
        repo_ref: Repository URL (https://github.com/org/repo) or local path.
        subdirectory: Optional sub-path within the repository to narrow scope.

    Returns:
        dict with 'scope_note', 'file_count', 'provider', and 'files' summary
        (paths + tiers, no content — content is held in memory for scanner tools).

    Raises:
        SCMNotConnectedError: when the SCM integration is not configured.
    """
    inventory = _clone_or_read_repo_impl(repo_ref, subdirectory)

    # Return a summary dict to the LLM; scanner tools access the full content
    # via the module-level cache populated below.
    _inventory_cache[repo_ref] = inventory

    scope = inventory.scope_note
    return {
        "status": "ok",
        "provider": inventory.provider,
        "file_count": inventory.file_count,
        "files_seen": scope.files_seen,
        "capped": scope.capped,
        "cap_type": scope.cap_type,
        "layers_covered": scope.layers_covered,
        "tokens_consumed": scope.tokens_consumed,
        "subdirectory": scope.subdirectory,
        "files": [
            {"path": f.path, "tier": f.tier, "loaded": f.content is not None}
            for f in inventory.files
        ],
    }


# Module-level inventory cache keyed by repo_ref string.
# Scanner tools (iac-scanner, app-scanner) retrieve the RepoInventory here.
_inventory_cache: dict[str, RepoInventory] = {}


def get_cached_inventory(repo_ref: str) -> RepoInventory | None:
    """Retrieve a previously loaded RepoInventory by repo_ref string."""
    return _inventory_cache.get(repo_ref)
