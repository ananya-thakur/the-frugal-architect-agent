"""Core scanning logic for the app-scanner unit.

Public entry point: scan_app_antipatterns(inventory, rubric) -> AppScannerResult
"""

from __future__ import annotations

import logging
import os
import uuid
from collections import defaultdict

from agent.app_scanner._patterns import SIGNAL_PATTERNS
from agent.app_scanner.models import (
    AppScannerResult,
    File,
    PatternMatch,
    PossibleFinding,
    PrioritizedInventory,
    ScanSummary,
)
from agent.rubric_core.models import AntiPatternSignal, Rubric

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_DEFAULT_MAX_INSTANCES: int = 3
_MAX_FILE_BYTES: int = 512 * 1024  # 512 KB per-file size cap (security-design)

_RUNTIME_CAVEAT = (
    "This finding is a static analysis lead. Confirming it requires runtime/cost data "
    "(e.g. query execution plans, CloudWatch cost metrics, profiler output) not available "
    "through code inspection alone."
)

# UUID v5 namespace for deterministic finding IDs (BR-APP-009)
_FINDING_NAMESPACE = uuid.UUID("4d657f32-f421-4e75-9a33-5b6c3b98d112")

# IaC file indicators — secondary guard (BR-APP-007); primary filtering is in repo-connector
_IAC_EXTENSIONS = frozenset({".tf", ".tfvars", ".hcl"})
_IAC_FILENAMES = frozenset({
    "template.yaml", "template.yml", "template.json",
    "serverless.yml", "serverless.yaml",
    "cdk.json", "cdktf.json",
    "dockerfile", "docker-compose.yml", "docker-compose.yaml",
})
_IAC_PATH_SEGMENTS = frozenset({"cdk", "infra", "infrastructure", "cloudformation", "terraform"})


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_iac_file(file: File) -> bool:
    """Return True if the file looks like IaC/config (secondary guard, BR-APP-007)."""
    if file.extension in _IAC_EXTENSIONS:
        return True
    basename = file.path.rsplit("/", 1)[-1].lower()
    if basename in _IAC_FILENAMES:
        return True
    parts = {p.lower() for p in file.path.split("/")}
    return bool(parts & _IAC_PATH_SEGMENTS)


def _extract_context(lines: list[str], line_idx: int, context_lines: int) -> str:
    """Return surrounding lines as a joined string."""
    start = max(0, line_idx - context_lines)
    end = min(len(lines), line_idx + context_lines + 1)
    return "\n".join(lines[start:end])


def _make_finding_id(signal_id: str, file_path: str, line_number: int | None) -> str:
    """Deterministic UUID v5 from (signal_id, file_path, line_number) (BR-APP-009)."""
    ln = str(line_number) if line_number is not None else "0"
    name = f"{signal_id}:{file_path}:{ln}"
    return str(uuid.uuid5(_FINDING_NAMESPACE, name))


def _get_law_title(rubric: Rubric, law_id: int) -> str:
    """Return the law title for a given law number from the loaded rubric."""
    for law in rubric.laws:
        if law.number == law_id:
            return law.title
    return f"Law {law_id}"  # fallback — rubric-core validator ensures 1–7 always present


def _scrub_evidence(text: str) -> str:
    """Remove common credential patterns from evidence text before storing (security-design)."""
    import re

    patterns = [
        (re.compile(r'(?i)(password|passwd|secret|token|api_?key)\s*[=:]\s*\S+'), r'\1=<REDACTED>'),
        (re.compile(r'AKIA[0-9A-Z]{16}'), '<AWS_KEY_REDACTED>'),
        (re.compile(r'(?i)(aws_secret_access_key)\s*[=:]\s*\S+'), r'\1=<REDACTED>'),
    ]
    result = text
    for compiled, replacement in patterns:
        result = compiled.sub(replacement, result)
    return result


# ── match_pattern ─────────────────────────────────────────────────────────────

def match_pattern(file: File, signal: AntiPatternSignal) -> list[PatternMatch]:
    """Apply a single AntiPatternSignal to a single File.

    Returns zero or more PatternMatch objects. Parse/encoding errors are
    absorbed with a DEBUG log entry (BR-APP-005). Binary/oversized files
    are skipped silently.
    """
    # Per-file size cap (security-design)
    if file.size_bytes > _MAX_FILE_BYTES:
        logger.debug("Skipping %s: size %d bytes exceeds cap", file.path, file.size_bytes)
        return []

    patterns = SIGNAL_PATTERNS.get(signal.id, [])
    if not patterns:
        return []

    matches: list[PatternMatch] = []
    lines = file.content.splitlines()

    for spec in patterns:
        # Skip if this pattern doesn't apply to the file's extension
        if spec.language_filters and file.extension not in spec.language_filters:
            continue

        try:
            for m in spec.pattern.finditer(file.content):
                # Derive 1-based line number from match start position
                line_number = file.content.count("\n", 0, m.start()) + 1
                line_idx = line_number - 1
                matched_line = lines[line_idx].strip() if line_idx < len(lines) else m.group(0)
                context = _extract_context(lines, line_idx, spec.context_lines)
                matches.append(PatternMatch(
                    signal_id=signal.id,
                    file_path=file.path,
                    line_number=line_number,
                    matched_text=matched_line,
                    context_snippet=context,
                ))
        except Exception as exc:  # noqa: BLE001
            logger.debug("Pattern match error on %s / %s: %s", file.path, signal.id, exc)

    return matches


# ── Deduplication & noise suppression ────────────────────────────────────────

def _deduplicate(findings: list[PossibleFinding]) -> list[PossibleFinding]:
    """Keep only the first occurrence of each (anti_pattern_type, file_path, line_number)."""
    seen: set[tuple[str, str, int | None]] = set()
    result: list[PossibleFinding] = []
    for f in findings:
        key = (f.anti_pattern_type, f.file_path, f.line_number)
        if key not in seen:
            seen.add(key)
            result.append(f)
    return result


def _noise_suppress(
    findings: list[PossibleFinding],
    max_instances: int,
) -> tuple[list[PossibleFinding], list[str]]:
    """Cap findings at max_instances per (file_path, anti_pattern_type) (BR-APP-008).

    Returns (kept_findings, systemic_notes).
    """
    if max_instances == 0:
        return findings, []

    counts: dict[tuple[str, str], int] = defaultdict(int)
    kept: list[PossibleFinding] = []
    overflow: dict[tuple[str, str], int] = defaultdict(int)

    for f in findings:
        key = (f.file_path, f.anti_pattern_type)
        counts[key] += 1
        if counts[key] <= max_instances:
            kept.append(f)
        else:
            overflow[key] += 1

    notes: list[str] = []
    for (file_path, pattern_type), extra in overflow.items():
        notes.append(
            f"'{pattern_type}' pattern found in {extra} additional location(s) in "
            f"{file_path} beyond the {max_instances} shown."
        )

    return kept, notes


# ── Main entry point ──────────────────────────────────────────────────────────

def scan_app_antipatterns(
    inventory: PrioritizedInventory,
    rubric: Rubric,
) -> AppScannerResult:
    """Scan app-code files in inventory for anti-patterns defined in rubric.

    Steps (per business-logic-model):
      1. Load signals from rubric (once per invocation, BR-APP-006)
      2. Select app-code files (IaC files excluded, BR-APP-007)
      3. match_pattern for each (file, signal) pair
      4. Convert PatternMatch → PossibleFinding
      5. Deduplicate + noise-suppress
      6. Build ScanSummary and return AppScannerResult

    All findings carry confidence="POSSIBLE" (BR-APP-001).
    ScanSummary is always returned (BR-APP-010).
    """
    # Step 1 — load signals once (BR-APP-006)
    signals: list[AntiPatternSignal] = list(rubric.anti_pattern_signals)

    max_instances = int(os.environ.get("SCAN_MAX_INSTANCES_PER_SIGNAL", str(_DEFAULT_MAX_INSTANCES)))

    # Step 2 — filter app files; secondary IaC guard (BR-APP-007)
    app_files = [f for f in inventory.app_files if not _is_iac_file(f)]

    files_scanned = 0
    parse_error_skipped = 0
    all_matches: list[tuple[PatternMatch, AntiPatternSignal]] = []

    # Step 3 — match patterns
    for file in app_files:
        file_had_match_attempt = False
        for signal in signals:
            try:
                hits = match_pattern(file, signal)
                all_matches.extend((hit, signal) for hit in hits)
                file_had_match_attempt = True
            except Exception as exc:  # noqa: BLE001 — BR-APP-005
                logger.debug("Scan error on %s / %s: %s", file.path, signal.id, exc)
                parse_error_skipped += 1

        if file_had_match_attempt or not signals:
            files_scanned += 1

    # Step 4 — convert to PossibleFindings
    findings: list[PossibleFinding] = []
    for pm, signal in all_matches:
        law_id = signal.related_laws[0]
        law_title = _get_law_title(rubric, law_id)
        evidence = _scrub_evidence(f"{pm.matched_text}\n---\n{pm.context_snippet}")
        findings.append(PossibleFinding(
            finding_id=_make_finding_id(signal.id, pm.file_path, pm.line_number),
            law_id=law_id,
            law_title=law_title,
            anti_pattern_type=signal.name,
            file_path=pm.file_path,
            line_number=pm.line_number,
            evidence=evidence,
            confidence="POSSIBLE",
            runtime_caveat=_RUNTIME_CAVEAT,
            cost_impact_explanation=signal.description,
        ))

    # Step 5 — dedup + noise-suppress
    findings = _deduplicate(findings)
    findings, systemic_notes = _noise_suppress(findings, max_instances)

    # Step 6 — build summary (BR-APP-010)
    cap_note: str | None = None
    if inventory.scan_cap_reached:
        cap_note = (
            f"Scan capped. Scanned {files_scanned} app-code files; "
            f"{inventory.files_skipped_count} were not reached. "
            "To analyze a specific area, specify a subdirectory path."
        )

    summary = ScanSummary(
        files_scanned=files_scanned,
        files_skipped=inventory.files_skipped_count + parse_error_skipped,
        anti_patterns_searched=len(signals),
        cap_reached=inventory.scan_cap_reached,
        cap_note=cap_note,
        systemic_pattern_notes=systemic_notes,
    )

    return AppScannerResult(findings=findings, summary=summary)
