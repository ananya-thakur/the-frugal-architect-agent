"""
scan_iac — the primary Strands tool for exhaustive IaC/config analysis.

Implements the scan_iac workflow from business-logic-model-iac-scanner:
  1. Guard: empty inventory → return ([], empty_summary)
  2. Validate rubric has exactly 7 laws
  3. Partition inventory.iac_files by format
  4. Budget-aware dispatch to per-format sub-scanners
  5. Deduplicate on (file_path, resource_id, law_id)
  6. Sort deterministically
  7. Assemble and return (findings, summary)

Rules enforced: BR-01 through BR-11 (business-rules-iac-scanner).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from agent.iac_scanner.models import (
    Confidence,
    File,
    FirmFinding,
    IaCFormat,
    ParsedResource,
    PrioritizedInventory,
    ScanContext,
    ScanSummary,
)
from agent.iac_scanner.parsers import parse_file
from agent.iac_scanner.predicates import PredicateError, evaluate, resolve_attribute
from agent.rubric_core.models import IaCSignal, LawDefinition, Rubric

log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

def _scan_token_budget() -> int:
    """Read SCAN_TOKEN_BUDGET env var; default 100 000."""
    try:
        return int(os.environ.get("SCAN_TOKEN_BUDGET", "100000"))
    except ValueError:
        return 100_000


def _estimate_tokens(content: str) -> int:
    """Rough token estimate: 1 token ≈ 4 characters."""
    return max(1, len(content) // 4)


# ── Signal matching ───────────────────────────────────────────────────────────

def _signal_matches(signal: IaCSignal, resource: ParsedResource) -> bool:
    """
    Evaluate whether a resource triggers this signal.

    1. Check format filter (signal.formats must include the resource's format name).
    2. Check resource_type_filter (optional prefix match on resource.resource_type).
    3. Resolve the attribute and evaluate the predicate.
    """
    if resource.format.value not in signal.formats:
        return False

    if signal.resource_type_filter:
        if not resource.resource_type.startswith(signal.resource_type_filter):
            return False

    value = resolve_attribute(resource.attributes, signal.attribute_path)

    try:
        return evaluate(signal.predicate, value)
    except PredicateError as exc:
        log.warning("iac-scanner: skipping unparseable predicate %r — %s", signal.predicate, exc)
        return False


# ── Evidence rendering ────────────────────────────────────────────────────────

_REDACT_PATTERNS = (
    "password", "secret", "token", "key", "credential", "passwd", "auth",
)


def _looks_like_credential(value: str) -> bool:
    lower = value.lower()
    return any(p in lower for p in _REDACT_PATTERNS)


def _render_evidence(template: str, resource: ParsedResource, signal: IaCSignal) -> str:
    """Interpolate evidence_template; redact values that look like credentials."""
    raw_value = resolve_attribute(resource.attributes, signal.attribute_path)
    if isinstance(raw_value, str) and _looks_like_credential(raw_value):
        display_value = "[REDACTED]"
    else:
        display_value = str(raw_value) if raw_value is not None else "null"
    return (
        template
        .replace("{resource_id}", resource.id)
        .replace("{file_path}", resource.file_path)
        .replace("{attribute_value}", display_value)
        .replace("{resource_type}", resource.resource_type)
    )


# ── Per-format sub-scanner ────────────────────────────────────────────────────

def _process_files(
    files: list[File],
    rubric: Rubric,
    context: ScanContext,
    budget: int,
) -> None:
    """
    Scan ``files`` against ``rubric`` signals, accumulating into ``context``.

    Budget-aware: stops adding new files when token spend reaches 90% of budget (BR-08).
    Per-file parse errors are caught and recorded without aborting siblings (BR-06).
    """
    for file in files:
        if context.token_spent >= budget * 0.9:
            context.skipped_paths.append(file.path)
            continue

        context.token_spent += _estimate_tokens(file.content)
        context.formats_seen.add(file.format.value)

        try:
            resources: list[ParsedResource] = parse_file(file)
        except Exception as exc:  # noqa: BLE001 — BR-06 isolation
            context.parse_errors.append((file.path, str(exc)))
            log.debug("iac-scanner: skipping %s — %s", file.path, exc)
            continue

        context.processed_count += 1

        for resource in resources:
            for law in rubric.laws:
                for signal in law.iac_signals:
                    if _signal_matches(signal, resource):
                        evidence = _render_evidence(signal.evidence_template, resource, signal)
                        finding = FirmFinding(
                            law_id=f"law-{law.number}",
                            law_title=law.title,
                            file_path=file.path,
                            resource_id=resource.id,
                            resource_type=resource.resource_type,
                            line=resource.line_number,
                            gap=signal.gap_description,
                            evidence=evidence,
                            signal_id=signal.id,
                            confidence=Confidence.FIRM,
                        )
                        context.findings.append(finding)


# ── Deduplication ─────────────────────────────────────────────────────────────

def _deduplicate(findings: list[FirmFinding]) -> list[FirmFinding]:
    """
    Remove duplicate findings on (file_path, resource_id, law_id).

    Preference: non-None line > lower line number > first encountered (BR-07).
    """
    seen: dict[tuple[str, str, str], FirmFinding] = {}
    for finding in findings:
        key = (finding.file_path, finding.resource_id, finding.law_id)
        if key not in seen:
            seen[key] = finding
        else:
            existing = seen[key]
            if existing.line is None and finding.line is not None:
                seen[key] = finding
            elif (
                existing.line is not None
                and finding.line is not None
                and finding.line < existing.line
            ):
                seen[key] = finding
    return list(seen.values())


# ── Sort ──────────────────────────────────────────────────────────────────────

def _sort_findings(findings: list[FirmFinding], rubric: Rubric) -> list[FirmFinding]:
    """
    Sort findings deterministically (BLM sort-order section):
    1. law_id ascending (law-1 first)
    2. severity_rank ascending (rank 1 = highest first)
    3. file_path ascending
    """
    signal_rank: dict[str, int] = {}
    for law in rubric.laws:
        for sig in law.iac_signals:
            signal_rank[sig.id] = sig.severity_rank

    def sort_key(f: FirmFinding) -> tuple[str, int, str]:
        return (f.law_id, signal_rank.get(f.signal_id, 99), f.file_path)

    return sorted(findings, key=sort_key)


# ── Public Strands tool ───────────────────────────────────────────────────────

def scan_iac(
    inventory: PrioritizedInventory,
    rubric: Rubric,
) -> tuple[list[FirmFinding], ScanSummary]:
    """
    Exhaustively scan IaC and config files for frugality gaps (FIRM confidence).

    This is the primary Strands tool for the CODEBASE REVIEW pipeline. It
    dispatches the IaC files from ``inventory`` through format-specific parsers,
    evaluates each parsed resource against every IaCSignal in ``rubric``, and
    returns deduplicated, sorted FIRM findings alongside a scope-transparency
    summary.

    The tool is READ-ONLY and ADVISORY: it never modifies files, never calls
    AWS account APIs, and never emits findings it cannot ground in static
    evidence from the IaC files (ADR-003, req-static-analysis-honesty).

    Attribution: findings cite the 7 Laws of Frugal Architecture by
    Werner Vogels / The Frugal Architect (https://thefrugalarchitect.com/laws/).

    Args:
        inventory: Pre-sorted file inventory from repo-connector (ADR-007).
                   iac_files are already ordered by frugality signal density.
        rubric:    Validated Rubric from rubric-core. All signal criteria are
                   sourced exclusively from this object (ADR-002, BR-11).

    Returns:
        (findings, summary) where findings is a deduplicated, sorted list of
        FirmFinding objects and summary carries scope-transparency metadata.

    Raises:
        ValueError: if rubric does not contain exactly 7 laws (BR validation).
    """
    # BR-01: empty inventory → no findings
    if not inventory.iac_files:
        return [], ScanSummary(
            files_processed=0,
            files_skipped_budget=0,
            skipped_paths=[],
            parse_errors=[],
            formats_scanned=[],
            findings_count=0,
        )

    # Rubric integrity check
    if len(rubric.laws) != 7:
        raise ValueError(
            f"scan_iac requires a rubric with exactly 7 laws, got {len(rubric.laws)}"
        )

    budget = _scan_token_budget()
    context = ScanContext()

    # Process all IaC files (already priority-sorted by repo-connector per ADR-007)
    _process_files(list(inventory.iac_files), rubric, context, budget)

    # Deduplicate (BR-07)
    deduped = _deduplicate(context.findings)

    # Sort deterministically
    sorted_findings = _sort_findings(deduped, rubric)

    summary = ScanSummary(
        files_processed=context.processed_count,
        files_skipped_budget=len(context.skipped_paths),
        skipped_paths=context.skipped_paths,
        parse_errors=context.parse_errors,
        formats_scanned=sorted(context.formats_seen),
        findings_count=len(sorted_findings),
    )

    return sorted_findings, summary
