"""Shared fixtures for the scorecard-formatter test suite."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent.scoring_engine.models import (
    ATTRIBUTION_TEXT,
    CAVEAT_TEXT,
    ConfidenceTag,
    Effort,
    EvidenceItem,
    Impact,
    LawScore,
    RemediationRoadmap,
    RemediationStep,
    ScanMetadata,
    Scorecard,
    ScoringMode,
    Verdict,
)


def _make_evidence(
    law_id: str = "law-1",
    source_type: ConfidenceTag = ConfidenceTag.FIRM,
    description: str = "Lambda over-provisioned at 4096 MB.",
    citation: str = "MyFunction in infra/stack.py sets memory_size=4096 MB.",
) -> EvidenceItem:
    return EvidenceItem(
        source_type=source_type,
        citation=citation,
        description=description,
        confidence_tag=source_type.value,
        law_id=law_id,
    )


def _make_remediation_step(
    law_id: str = "law-1",
    description: str = "Right-size Lambda memory via profiling.",
    effort: Effort = Effort.LOW,
    impact: Impact = Impact.HIGH,
    source_confidence: ConfidenceTag = ConfidenceTag.FIRM,
    priority_rank: int = 1,
) -> RemediationStep:
    return RemediationStep(
        law_id=law_id,
        description=description,
        aws_actions=[
            "Enable AWS Lambda Power Tuning to profile memory vs. cost.",
            "Reduce memory_size to the profiled optimum in the CDK/Terraform config.",
        ],
        portable_principle="Right-size resources based on actual profiling data.",
        effort=effort,
        impact=impact,
        source_confidence=source_confidence,
        priority_score=10,
        priority_rank=priority_rank,
        runtime_confirmation_note=None,
    )


def _make_law_score(
    law_number: int = 1,
    verdict: Verdict = Verdict.STRONG,
    evidence: list | None = None,
    gap: str | None = None,
    target_state: str | None = None,
    remediation_steps: list | None = None,
    effort: Effort | None = None,
    impact: Impact | None = None,
    probing_question: str | None = None,
) -> LawScore:
    return LawScore(
        law_id=f"law-{law_number}",
        law_title=f"Law {law_number} Title",
        law_number=law_number,
        verdict=verdict,
        evidence=evidence or [],
        gap=gap,
        target_state=target_state,
        remediation_steps=remediation_steps,
        effort=effort,
        impact=impact,
        probing_question=probing_question,
    )


@pytest.fixture
def scan_metadata() -> ScanMetadata:
    return ScanMetadata(
        files_scanned=120,
        iac_files=15,
        app_files=105,
        capped=False,
        capped_reason=None,
        layers_covered=["IaC/config", "app-code"],
    )


@pytest.fixture
def strong_scorecard(scan_metadata: ScanMetadata) -> Scorecard:
    """All 7 laws STRONG — no gaps, empty roadmap."""
    law_scores = [
        _make_law_score(
            law_number=i,
            verdict=Verdict.STRONG,
            evidence=[_make_evidence(law_id=f"law-{i}")],
        )
        for i in range(1, 8)
    ]
    return Scorecard(
        law_scores=law_scores,
        roadmap=RemediationRoadmap(steps=[]),
        overall_verdict="STRONG",
        biggest_opportunity=None,
        caveat=CAVEAT_TEXT,
        attribution=ATTRIBUTION_TEXT,
        scan_metadata=scan_metadata,
        mode=ScoringMode.CODEBASE_REVIEW,
        created_at=datetime(2026, 10, 14, 12, 0, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def gap_scorecard(scan_metadata: ScanMetadata) -> Scorecard:
    """Law 1 GAP (FIRM evidence), Law 2 UNKNOWN, Law 3 PARTIAL (POSSIBLE evidence), Laws 4-7 STRONG."""
    step_law1 = _make_remediation_step(law_id="law-1", priority_rank=1)
    step_law3 = _make_remediation_step(
        law_id="law-3",
        description="Add cost allocation tags to all DynamoDB tables.",
        effort=Effort.LOW,
        impact=Impact.MEDIUM,
        source_confidence=ConfidenceTag.POSSIBLE,
        priority_rank=2,
        # POSSIBLE-sourced steps should carry a runtime confirmation note.
    )

    law_scores = []

    # Law 1 — GAP with FIRM evidence.
    law_scores.append(
        _make_law_score(
            law_number=1,
            verdict=Verdict.GAP,
            evidence=[_make_evidence(law_id="law-1", source_type=ConfidenceTag.FIRM)],
            gap="Lambda function over-provisioned; cost not treated as NFR.",
            target_state="All compute resources sized to profiled optimum with cost tagging.",
            remediation_steps=[step_law1],
            effort=Effort.LOW,
            impact=Impact.HIGH,
        )
    )

    # Law 2 — UNKNOWN (no business context).
    law_scores.append(
        _make_law_score(
            law_number=2,
            verdict=Verdict.UNKNOWN,
            probing_question=(
                "What is the revenue model for this service? "
                "Understanding cost-to-revenue ratio is required to score Law II."
            ),
        )
    )

    # Law 3 — PARTIAL with POSSIBLE evidence.
    law_scores.append(
        _make_law_score(
            law_number=3,
            verdict=Verdict.PARTIAL,
            evidence=[
                _make_evidence(
                    law_id="law-3",
                    source_type=ConfidenceTag.POSSIBLE,
                    description="DynamoDB table appears to lack cost allocation tags.",
                    citation="src/db/tables.py line 42: DynamoDB resource creation without tags.",
                )
            ],
            gap="Cost allocation tags missing from DynamoDB resources.",
            target_state="All resources tagged by feature/team for cost attribution.",
            remediation_steps=[step_law3],
            effort=Effort.LOW,
            impact=Impact.MEDIUM,
        )
    )

    # Laws 4–7 — STRONG.
    for i in range(4, 8):
        law_scores.append(
            _make_law_score(
                law_number=i,
                verdict=Verdict.STRONG,
                evidence=[_make_evidence(law_id=f"law-{i}")],
            )
        )

    roadmap = RemediationRoadmap(steps=[step_law1, step_law3])

    return Scorecard(
        law_scores=law_scores,
        roadmap=roadmap,
        overall_verdict="GAP",
        biggest_opportunity="Law 1: Lambda over-provisioned — right-size to cut compute costs.",
        caveat=CAVEAT_TEXT,
        attribution=ATTRIBUTION_TEXT,
        scan_metadata=scan_metadata,
        mode=ScoringMode.CODEBASE_REVIEW,
        created_at=datetime(2026, 10, 14, 12, 0, 0, tzinfo=timezone.utc),
    )
