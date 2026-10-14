"""Core scoring algorithms for the scoring-engine unit.

Implements score_project (public Strands tool entry point), score_law,
build_roadmap, derive_overall, decision_check, and extract_scan_metadata.

Rules enforced: BR-01 through BR-17 (business-rules-scoring-engine).
Security controls: SR-01 through SR-08 (security-design-scoring-engine).
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from agent.app_scanner.models import PossibleFinding
from agent.iac_scanner.models import FirmFinding
from agent.rubric_core.models import LawDefinition, Rubric
from agent.scoring_engine.models import (
    ATTRIBUTION_TEXT,
    CAVEAT_TEXT,
    COMBINED_LIMIT,
    EFFORT_WEIGHT,
    FIRM_LIMIT,
    IMPACT_WEIGHT,
    POSSIBLE_LIMIT,
    ROADMAP_LIMIT,
    RUNTIME_CONFIRMATION_NOTE,
    SNIPPET_MAX_CHARS,
    STRONG_THRESHOLD,
    BusinessContext,
    ConfidenceTag,
    DecisionCheckResult,
    Effort,
    EvidenceItem,
    Impact,
    IncompleteGapAnalysisError,
    InputSizeLimitError,
    LawScore,
    RemediationRoadmap,
    RemediationStep,
    RubricIntegrityError,
    RubricNotLoadedError,
    ScanMetadata,
    Scorecard,
    ScoringMode,
    Verdict,
)


# ── Text utilities ────────────────────────────────────────────────────────────

_STOP_WORDS: frozenset[str] = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "are", "was", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "shall", "this", "that", "these",
    "those", "it", "its", "if", "not", "no", "so", "than", "then", "when",
    "where", "how", "which", "what", "who", "each", "all", "per", "via",
    "can", "into", "over", "after", "before", "about", "than", "such",
})


def _significant_words(text: str) -> frozenset[str]:
    """Extract significant lowercase words (>3 chars, not stop words)."""
    return frozenset(
        w for w in re.findall(r"[a-z]+", text.lower())
        if len(w) > 3 and w not in _STOP_WORDS
    )


def _text_overlap(text1: str, text2: str) -> int:
    """Count significant word overlaps between two strings."""
    return len(_significant_words(text1) & _significant_words(text2))


def _parse_criteria(strong_looks_like: str) -> list[str]:
    """Split strong_looks_like prose into individual sentence-criteria."""
    text = strong_looks_like.strip()
    parts = text.split(". ")
    criteria = [p.strip(" .\n\r\t") for p in parts]
    return [c for c in criteria if c] or [text]


def _truncate_snippet(text: str) -> str:
    """Truncate to SNIPPET_MAX_CHARS and redact credential-like patterns (SR-05)."""
    redacted = re.sub(
        r"(?i)(password|secret|token|key)\s*=\s*\S+",
        "[redacted — potential credential]",
        text,
    )
    if len(redacted) > SNIPPET_MAX_CHARS:
        redacted = redacted[:SNIPPET_MAX_CHARS] + "…[truncated]"
    return redacted


# ── Law ID helpers ────────────────────────────────────────────────────────────

def _law_id_str(number: int) -> str:
    return f"law-{number}"


def _firm_law_number(finding: FirmFinding) -> int:
    """Extract integer law number from FirmFinding.law_id (format 'law-N')."""
    try:
        return int(finding.law_id.split("-")[-1])
    except (ValueError, IndexError):
        return 0


# ── Evidence construction ─────────────────────────────────────────────────────

def _firm_to_evidence(finding: FirmFinding, law_id: str) -> EvidenceItem:
    citation = _truncate_snippet(f"{finding.file_path}:{finding.resource_id}")
    description = _truncate_snippet(finding.gap)
    return EvidenceItem(
        source_type=ConfidenceTag.FIRM,
        citation=citation,
        description=description,
        confidence_tag="FIRM",
        law_id=law_id,
    )


def _possible_to_evidence(finding: PossibleFinding, law_id: str) -> EvidenceItem:
    citation = _truncate_snippet(
        f"{finding.file_path}:{finding.line_number or 'unknown'}"
    )
    description = _truncate_snippet(finding.cost_impact_explanation)
    return EvidenceItem(
        source_type=ConfidenceTag.POSSIBLE,
        citation=citation,
        description=description,
        confidence_tag="POSSIBLE (investigate)",
        law_id=law_id,
    )


def _described_to_evidence(answer: str, law_id: str) -> EvidenceItem:
    return EvidenceItem(
        source_type=ConfidenceTag.DESCRIBED,
        citation=f"User description for {law_id}",
        description=_truncate_snippet(answer[:500]),
        confidence_tag="DESCRIBED (user-provided)",
        law_id=law_id,
    )


# ── Criteria violation mapping ────────────────────────────────────────────────

def _map_violations_to_criteria(
    firm: list[FirmFinding],
    criteria: list[str],
) -> set[int]:
    """Return criterion indices matched by at least one firm violation.

    Each FirmFinding is matched against every criterion via keyword overlap.
    If no keyword overlap is found, the finding is attributed to criterion 0
    to ensure at least one criterion is always marked as violated.
    """
    violated: set[int] = set()
    for finding in firm:
        finding_text = f"{finding.gap} {finding.evidence}"
        matched_any = False
        for i, criterion in enumerate(criteria):
            if _text_overlap(finding_text, criterion) > 0:
                violated.add(i)
                matched_any = True
        if not matched_any and criteria:
            violated.add(0)  # Default: attribute to first criterion
    return violated


# ── Remediation step builders ─────────────────────────────────────────────────

def _estimate_effort(firm_count: int, possible_count: int) -> Effort:
    total = firm_count + possible_count
    if total > 5:
        return Effort.HIGH
    if total > 2:
        return Effort.MEDIUM
    return Effort.LOW


def _estimate_impact(firm: list[FirmFinding], law: LawDefinition) -> Impact:
    if not firm:
        return Impact.MEDIUM
    total_rank = 0
    count = 0
    for finding in firm:
        signal = next((s for s in law.iac_signals if s.id == finding.signal_id), None)
        if signal:
            total_rank += signal.severity_rank
            count += 1
    if count == 0:
        return Impact.MEDIUM
    avg = total_rank / count
    if avg <= 1.0:
        return Impact.HIGH
    if avg <= 2.0:
        return Impact.MEDIUM
    return Impact.LOW


def _build_firm_step(
    finding: FirmFinding,
    law: LawDefinition,
    effort: Effort,
    impact: Impact,
    criteria: list[str],
) -> RemediationStep:
    signal = next((s for s in law.iac_signals if s.id == finding.signal_id), None)
    if signal:
        description = signal.gap_description
        aws_actions = [
            f"Remediate {finding.resource_id} in {finding.file_path}: {signal.gap_description}"
        ]
        portable_principle = (
            f"Right-size and right-price resources using actual usage data. "
            f"Law {law.number}: {law.title}."
        )
    else:
        description = finding.gap[:500]
        aws_actions = [
            f"Address frugality gap in {finding.resource_id} ({finding.file_path})"
        ]
        portable_principle = (
            f"Eliminate unnecessary cost. Law {law.number}: {law.title}. "
            + (criteria[0][:200] if criteria else "")
        )

    priority = IMPACT_WEIGHT[impact] - EFFORT_WEIGHT[effort]
    return RemediationStep(
        law_id=_law_id_str(law.number),
        description=description,
        aws_actions=aws_actions,
        portable_principle=portable_principle,
        effort=effort,
        impact=impact,
        source_confidence=ConfidenceTag.FIRM,
        priority_score=priority,
        runtime_confirmation_note=None,
    )


def _build_possible_step(finding: PossibleFinding, law: LawDefinition) -> RemediationStep:
    description = finding.cost_impact_explanation[:500]
    aws_actions = [
        f"Investigate '{finding.anti_pattern_type}' in {finding.file_path} "
        f"— verify with runtime/cost data."
    ]
    portable_principle = (
        f"Eliminate inefficient patterns. Law {law.number}: {law.title}."
    )
    effort = Effort.LOW
    impact = Impact.MEDIUM
    priority = IMPACT_WEIGHT[impact] - EFFORT_WEIGHT[effort]
    return RemediationStep(
        law_id=_law_id_str(law.number),
        description=description,
        aws_actions=aws_actions,
        portable_principle=portable_principle,
        effort=effort,
        impact=impact,
        source_confidence=ConfidenceTag.POSSIBLE,
        priority_score=priority,
        runtime_confirmation_note=RUNTIME_CONFIRMATION_NOTE,
    )


def _build_described_step(
    law: LawDefinition,
    criterion_index: int,
    criteria: list[str],
) -> RemediationStep:
    criterion_text = criteria[criterion_index] if criterion_index < len(criteria) else law.title
    effort = Effort.MEDIUM
    impact = Impact.MEDIUM
    priority = IMPACT_WEIGHT[impact] - EFFORT_WEIGHT[effort]
    return RemediationStep(
        law_id=_law_id_str(law.number),
        description=f"Address gap for Law {law.number}: {criterion_text[:300]}",
        aws_actions=[f"Review and implement: {criterion_text[:200]}"],
        portable_principle=f"Law {law.number}: {law.title}",
        effort=effort,
        impact=impact,
        source_confidence=ConfidenceTag.DESCRIBED,
        priority_score=priority,
        runtime_confirmation_note=None,
    )


def _gap_description(violated: set[int], criteria: list[str]) -> str:
    if not violated or not criteria:
        return "Frugality gaps identified in findings; see evidence for details."
    parts = [criteria[i] for i in sorted(violated) if i < len(criteria)]
    return "Criteria not fully satisfied: " + "; ".join(parts[:3])


# ── score_law ─────────────────────────────────────────────────────────────────


def score_law(
    law: LawDefinition,
    firm: list[FirmFinding],
    possible: list[PossibleFinding],
    biz: BusinessContext | None,
    mode: ScoringMode,
) -> LawScore:
    """Score a single law and return a LawScore.

    Law II (cost-aware architecture) requires business context (BR-02).
    UNKNOWN returned whenever evidence is insufficient (BR-01).
    """
    law_id = _law_id_str(law.number)

    # Special case: Law II requires business context (BR-02)
    if law.number == 2 and (biz is None or not biz.revenue_model):
        return LawScore(
            law_id=law_id,
            law_title=law.title,
            law_number=law.number,
            verdict=Verdict.UNKNOWN,
            evidence=[],
            probing_question=law.probing_questions[0],
        )

    if mode == ScoringMode.DESIGN_REVIEW and biz is not None:
        return _score_design_law(law, biz)

    return _score_codebase_law(law, firm, possible)


def _score_codebase_law(
    law: LawDefinition,
    firm: list[FirmFinding],
    possible: list[PossibleFinding],
) -> LawScore:
    """Verdict logic for CODEBASE REVIEW mode.

    No evidence → UNKNOWN (BR-01).
    Firm violations present → PARTIAL or GAP based on criteria fraction.
    Only possible findings → PARTIAL (investigation leads).
    """
    law_id = _law_id_str(law.number)

    # No evidence for this law → UNKNOWN (BR-01)
    if not firm and not possible:
        return LawScore(
            law_id=law_id,
            law_title=law.title,
            law_number=law.number,
            verdict=Verdict.UNKNOWN,
            evidence=[],
            probing_question=law.probing_questions[0],
        )

    # Build evidence list: FIRM first (BR-07)
    evidence = [_firm_to_evidence(f, law_id) for f in firm]
    evidence += [_possible_to_evidence(f, law_id) for f in possible]

    criteria = _parse_criteria(law.strong_looks_like)
    total = max(len(criteria), 1)

    if firm:
        violated = _map_violations_to_criteria(firm, criteria)
        fraction = len(violated) / total
        verdict = Verdict.GAP if fraction >= 0.5 else Verdict.PARTIAL

        effort = _estimate_effort(len(firm), len(possible))
        impact = _estimate_impact(firm, law)

        steps: list[RemediationStep] = []
        for f in firm:
            steps.append(_build_firm_step(f, law, effort, impact, criteria))
        for p in possible:
            steps.append(_build_possible_step(p, law))
        steps = steps[:ROADMAP_LIMIT]

        return LawScore(
            law_id=law_id,
            law_title=law.title,
            law_number=law.number,
            verdict=verdict,
            evidence=evidence,
            gap=_gap_description(violated, criteria),
            target_state=law.strong_looks_like,
            remediation_steps=steps,
            effort=effort,
            impact=impact,
        )

    # Only POSSIBLE findings → PARTIAL (investigation leads, not firm violations)
    p_steps = [_build_possible_step(p, law) for p in possible]
    return LawScore(
        law_id=law_id,
        law_title=law.title,
        law_number=law.number,
        verdict=Verdict.PARTIAL,
        evidence=evidence,
        gap="Potential frugality issues detected; confirmation requires runtime/cost data.",
        target_state=law.strong_looks_like,
        remediation_steps=p_steps[:ROADMAP_LIMIT],
        effort=Effort.LOW,
        impact=Impact.MEDIUM,
    )


def _score_design_law(law: LawDefinition, biz: BusinessContext) -> LawScore:
    """Verdict logic for DESIGN REVIEW mode.

    Uses biz.law_answers keyed by law_id string.
    No answer → UNKNOWN (BR-15).
    Answer present → STRONG / PARTIAL / GAP based on criteria coverage.
    """
    law_id = _law_id_str(law.number)
    answer = (biz.law_answers or {}).get(law_id) or (biz.design_description or "")

    # No answer for this law → UNKNOWN (BR-15)
    if not answer.strip():
        return LawScore(
            law_id=law_id,
            law_title=law.title,
            law_number=law.number,
            verdict=Verdict.UNKNOWN,
            evidence=[],
            probing_question=law.probing_questions[0],
        )

    evidence = [_described_to_evidence(answer, law_id)]
    criteria = _parse_criteria(law.strong_looks_like)
    total = max(len(criteria), 1)

    covered = {i for i, c in enumerate(criteria) if _text_overlap(answer, c) > 0}
    coverage = len(covered) / total

    if coverage >= STRONG_THRESHOLD:
        return LawScore(
            law_id=law_id,
            law_title=law.title,
            law_number=law.number,
            verdict=Verdict.STRONG,
            evidence=evidence,
        )

    uncovered = set(range(total)) - covered
    verdict = Verdict.PARTIAL if covered else Verdict.GAP

    d_steps = [
        _build_described_step(law, i, criteria)
        for i in sorted(uncovered)[:5]
    ]
    if not d_steps:
        d_steps = [
            RemediationStep(
                law_id=law_id,
                description=f"Document compliance with Law {law.number}: {law.title}",
                aws_actions=["Document architecture decisions for this law"],
                portable_principle=f"Law {law.number}: {law.title}",
                effort=Effort.LOW,
                impact=Impact.LOW,
                source_confidence=ConfidenceTag.DESCRIBED,
                priority_score=IMPACT_WEIGHT[Impact.LOW] - EFFORT_WEIGHT[Effort.LOW],
                runtime_confirmation_note=None,
            )
        ]

    return LawScore(
        law_id=law_id,
        law_title=law.title,
        law_number=law.number,
        verdict=verdict,
        evidence=evidence,
        gap=_gap_description(uncovered, criteria),
        target_state=law.strong_looks_like,
        remediation_steps=d_steps,
        effort=Effort.MEDIUM,
        impact=Impact.MEDIUM,
    )


# ── build_roadmap ──────────────────────────────────────────────────────────────


def build_roadmap(scores: list[LawScore]) -> RemediationRoadmap:
    """Aggregate remediation steps from PARTIAL/GAP laws, priority-sorted (BR-09).

    Priority formula: IMPACT_WEIGHT[impact] - EFFORT_WEIGHT[effort], descending.
    Ties broken by source_confidence: FIRM before POSSIBLE (BR-07).
    """
    steps: list[RemediationStep] = []
    for score in scores:
        if score.verdict in (Verdict.PARTIAL, Verdict.GAP) and score.remediation_steps:
            steps.extend(score.remediation_steps)

    # Sort: priority DESC, FIRM before POSSIBLE/DESCRIBED on ties (BR-09)
    steps.sort(key=lambda s: (
        -s.priority_score,
        0 if s.source_confidence == ConfidenceTag.FIRM else 1,
    ))

    capped = steps[:ROADMAP_LIMIT]
    for rank, step in enumerate(capped, 1):
        step.priority_rank = rank

    return RemediationRoadmap(steps=capped)


# ── derive_overall ─────────────────────────────────────────────────────────────


def derive_overall(
    scores: list[LawScore],
    roadmap: RemediationRoadmap,
) -> tuple[str, str | None]:
    """Derive one-line overall verdict and biggest opportunity description."""
    counts = Counter(s.verdict for s in scores)
    gap_count     = counts[Verdict.GAP]
    partial_count = counts[Verdict.PARTIAL]

    if gap_count >= 3:
        overall = "SIGNIFICANT GAPS — immediate action recommended"
    elif gap_count >= 1 or partial_count >= 4:
        overall = "PARTIAL ALIGNMENT — targeted improvements needed"
    elif partial_count >= 1:
        overall = "MOSTLY ALIGNED — minor improvements available"
    else:
        overall = "STRONG ALIGNMENT"

    biggest = roadmap.steps[0].description if roadmap.steps else None
    return overall, biggest


# ── extract_scan_metadata ──────────────────────────────────────────────────────


def extract_scan_metadata(
    firm: list[FirmFinding],
    possible: list[PossibleFinding],
) -> ScanMetadata:
    """Extract scan provenance from finding lists."""
    firm_paths   = {f.file_path for f in firm}
    poss_paths   = {f.file_path for f in possible}

    # Use getattr for optional fields not present on all FirmFinding versions
    capped = any(getattr(f, "scan_capped", False) for f in firm) or \
             any(getattr(f, "scan_capped", False) for f in possible)
    capped_reason: str | None = "SCAN_FILE_LIMIT" if capped else None

    layers: list[str] = []
    for f in firm:
        layer = str(getattr(f, "scan_layer", "") or "iac")
        if layer not in layers:
            layers.append(layer)
    for f in possible:
        layer = "app-code-antipatterns"
        if layer not in layers:
            layers.append(layer)

    return ScanMetadata(
        files_scanned=len(firm_paths | poss_paths),
        iac_files=len(firm_paths),
        app_files=len(poss_paths),
        capped=capped,
        capped_reason=capped_reason,
        layers_covered=layers,
    )


# ── score_project (main entry point) ──────────────────────────────────────────


def score_project(
    firm: list[FirmFinding],
    possible: list[PossibleFinding],
    rubric: Rubric,
    biz: BusinessContext | None = None,
    mode: ScoringMode = ScoringMode.CODEBASE_REVIEW,
) -> Scorecard:
    """Evaluate a project against the 7 Laws of Frugal Architecture.

    Args:
        firm:    IaC violation findings from iac-scanner (FIRM confidence).
        possible: App-code anti-pattern findings from app-scanner (POSSIBLE confidence).
        rubric:  Validated Rubric from rubric-core. Must not be None (BR-12).
        biz:     Business context; Law II scoring requires revenue_model (BR-02).
        mode:    Scoring mode; defaults to CODEBASE_REVIEW.

    Returns:
        Scorecard with exactly 7 LawScores ordered by law_number (BR-06).

    Raises:
        RubricNotLoadedError: rubric is None (BR-12).
        RubricIntegrityError: rubric is not a validated Rubric instance (SR-02).
        InputSizeLimitError:  input lists exceed size limits (SR-06).
    """
    # Guard: rubric must be loaded (BR-12)
    if rubric is None:  # type: ignore[comparison-overlap]
        raise RubricNotLoadedError(
            "score_project requires a loaded Rubric from rubric_core.load_rubric (BR-12)"
        )

    # Guard: rubric must be a proper Rubric instance (SR-02)
    if not isinstance(rubric, Rubric):
        raise RubricIntegrityError(
            "rubric must be a Rubric instance produced by rubric_core.load_rubric (SR-02)"
        )

    # Guard: size limits (SR-06)
    if len(firm) > FIRM_LIMIT:
        raise InputSizeLimitError(
            f"firm findings exceed limit: {len(firm)} > {FIRM_LIMIT} (SR-06)"
        )
    if len(possible) > POSSIBLE_LIMIT:
        raise InputSizeLimitError(
            f"possible findings exceed limit: {len(possible)} > {POSSIBLE_LIMIT} (SR-06)"
        )
    if len(firm) + len(possible) > COMBINED_LIMIT:
        raise InputSizeLimitError(
            f"combined findings exceed limit: {len(firm) + len(possible)} > {COMBINED_LIMIT} (SR-06)"
        )

    # Score all 7 laws in law_number order (BR-06, BR-13)
    laws_by_number = {law.number: law for law in rubric.laws}
    law_scores: list[LawScore] = []

    for number in sorted(laws_by_number):
        law = laws_by_number[number]
        law_firm     = [f for f in firm     if _firm_law_number(f) == number]
        law_possible = [f for f in possible if f.law_id == number]
        law_scores.append(score_law(law, law_firm, law_possible, biz, mode))

    roadmap = build_roadmap(law_scores)
    overall_verdict, biggest_opportunity = derive_overall(law_scores, roadmap)
    scan_metadata = extract_scan_metadata(firm, possible)

    return Scorecard(
        law_scores=law_scores,
        roadmap=roadmap,
        overall_verdict=overall_verdict,
        biggest_opportunity=biggest_opportunity,
        caveat=CAVEAT_TEXT,
        attribution=ATTRIBUTION_TEXT,
        scan_metadata=scan_metadata,
        mode=mode,
        created_at=datetime.now(tz=timezone.utc),
    )


# ── decision_check ────────────────────────────────────────────────────────────

_DECISION_CHECK_THRESHOLD: float = 0.05


def decision_check(
    decision_text: str,
    rubric: Rubric,
    biz: BusinessContext | None = None,
) -> DecisionCheckResult:
    """Evaluate a described architecture decision against the most relevant laws.

    Returns DecisionCheckResult with 1–3 LawScores (BR-16, BR-17).

    Raises:
        RubricNotLoadedError: rubric is None.
        RubricIntegrityError: rubric is not a Rubric instance.
    """
    if rubric is None:  # type: ignore[comparison-overlap]
        raise RubricNotLoadedError(
            "decision_check requires a loaded Rubric from rubric_core.load_rubric (BR-12)"
        )
    if not isinstance(rubric, Rubric):
        raise RubricIntegrityError(
            "rubric must be a Rubric instance produced by rubric_core.load_rubric (SR-02)"
        )

    # Score relevance of each law to the decision text
    scored: list[tuple[LawDefinition, float]] = []
    for law in rubric.laws:
        corpus = f"{law.title} {law.description} {law.strong_looks_like}"
        overlap = _text_overlap(decision_text, corpus)
        corpus_words = max(len(_significant_words(corpus)), 1)
        scored.append((law, overlap / corpus_words))

    scored.sort(key=lambda x: -x[1])

    # Select top 1–3 laws above threshold; fall back to top-1 (BR-16)
    selected = [law for law, rel in scored if rel > _DECISION_CHECK_THRESHOLD][:3]
    if not selected:
        selected = [scored[0][0]]

    # Build synthetic biz context from the decision text for DESIGN REVIEW scoring
    law_answers: dict[str, str] = {
        _law_id_str(law.number): decision_text for law in selected
    }
    synthetic_biz = BusinessContext(
        design_description=decision_text,
        law_answers=law_answers,
        revenue_model=biz.revenue_model if biz else None,
    )

    scores: list[LawScore] = [
        score_law(law, [], [], synthetic_biz, ScoringMode.DESIGN_REVIEW)
        for law in selected
    ]

    recommendation = _build_recommendation(decision_text, scores, rubric)

    return DecisionCheckResult(
        relevant_laws=scores,
        recommendation=recommendation,
        attribution=ATTRIBUTION_TEXT,
        decision_text=decision_text,
    )


def _build_recommendation(
    decision_text: str,
    scores: list[LawScore],
    rubric: Any,  # Rubric, typed Any to avoid repeated import
) -> str:
    """Build a rubric-grounded recommendation (BR-17)."""
    lines: list[str] = [
        "Based on Werner Vogels' 7 Laws of Frugal Architecture "
        "(https://thefrugalarchitect.com/laws/):",
        "",
    ]
    for score in scores:
        lines.append(
            f"Law {score.law_number}: {score.law_title} — Verdict: {score.verdict.value}"
        )
        if score.verdict == Verdict.UNKNOWN:
            lines.append(f"  Probing question: {score.probing_question}")
        elif score.verdict in (Verdict.PARTIAL, Verdict.GAP) and score.gap:
            lines.append(f"  Gap: {score.gap}")
            if score.target_state:
                excerpt = score.target_state[:200]
                lines.append(f"  Target state: {excerpt}…")
        elif score.verdict == Verdict.STRONG:
            lines.append("  Alignment: looks good based on your description.")
        lines.append("")
    return "\n".join(lines)
