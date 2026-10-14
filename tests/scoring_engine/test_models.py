"""Tests for scoring-engine model invariants and validators."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent.scoring_engine.models import (
    ATTRIBUTION_TEXT,
    CAVEAT_TEXT,
    BusinessContext,
    ConfidenceTag,
    DecisionCheckResult,
    Effort,
    EvidenceItem,
    Impact,
    LawScore,
    RemediationRoadmap,
    RemediationStep,
    ScanMetadata,
    Scorecard,
    ScorecardIntegrityError,
    ScoringMode,
    Verdict,
)


# ── EvidenceItem ──────────────────────────────────────────────────────────────


class TestEvidenceItem:
    def test_construction_valid(self) -> None:
        item = EvidenceItem(
            source_type=ConfidenceTag.FIRM,
            citation="infra/main.tf:MyBucket",
            description="S3 bucket missing lifecycle policy.",
            confidence_tag="FIRM",
            law_id="law-5",
        )
        assert item.source_type == ConfidenceTag.FIRM
        assert item.law_id == "law-5"

    def test_immutable(self) -> None:
        item = EvidenceItem(
            source_type=ConfidenceTag.POSSIBLE,
            citation="src/app.py:42",
            description="N+1 query pattern detected.",
            confidence_tag="POSSIBLE (investigate)",
            law_id="law-4",
        )
        with pytest.raises((ValidationError, TypeError)):
            item.source_type = ConfidenceTag.FIRM  # type: ignore[misc]


# ── RemediationStep ───────────────────────────────────────────────────────────


class TestRemediationStep:
    def _make_step(self, **kwargs: object) -> RemediationStep:
        defaults: dict[str, object] = {
            "law_id": "law-1",
            "description": "Set Lambda MemorySize to profiled value.",
            "aws_actions": ["aws lambda update-function-configuration --memory-size 256"],
            "portable_principle": "Right-size compute resources.",
            "effort": Effort.LOW,
            "impact": Impact.MEDIUM,
            "source_confidence": ConfidenceTag.FIRM,
            "priority_score": 2,
        }
        defaults.update(kwargs)
        return RemediationStep(**defaults)

    def test_construction_valid(self) -> None:
        step = self._make_step()
        assert step.priority_rank == 0
        assert step.runtime_confirmation_note is None

    def test_possible_step_has_runtime_note(self) -> None:
        step = self._make_step(
            source_confidence=ConfidenceTag.POSSIBLE,
            runtime_confirmation_note="Confirm with runtime data.",
        )
        assert step.runtime_confirmation_note is not None

    def test_priority_rank_mutable(self) -> None:
        step = self._make_step()
        step.priority_rank = 3
        assert step.priority_rank == 3


# ── LawScore invariants ───────────────────────────────────────────────────────


class TestLawScoreInvariants:
    def _make_step(self) -> RemediationStep:
        return RemediationStep(
            law_id="law-1",
            description="Fix over-provisioned Lambda.",
            aws_actions=["Review Lambda MemorySize."],
            portable_principle="Right-size compute.",
            effort=Effort.LOW,
            impact=Impact.MEDIUM,
            source_confidence=ConfidenceTag.FIRM,
            priority_score=2,
        )

    def test_unknown_requires_probing_question(self) -> None:
        with pytest.raises(ValidationError, match="probing_question"):
            LawScore(
                law_id="law-2",
                law_title="Law 2",
                law_number=2,
                verdict=Verdict.UNKNOWN,
                evidence=[],
                # probing_question missing
            )

    def test_unknown_valid_with_probing_question(self) -> None:
        score = LawScore(
            law_id="law-2",
            law_title="Law 2",
            law_number=2,
            verdict=Verdict.UNKNOWN,
            evidence=[],
            probing_question="What revenue model does this system use?",
        )
        assert score.probing_question is not None

    def test_partial_requires_all_gap_fields(self) -> None:
        with pytest.raises(ValidationError, match="BR-04"):
            LawScore(
                law_id="law-1",
                law_title="Law 1",
                law_number=1,
                verdict=Verdict.PARTIAL,
                evidence=[],
                # gap fields missing
            )

    def test_partial_valid_with_all_gap_fields(self) -> None:
        score = LawScore(
            law_id="law-1",
            law_title="Law 1",
            law_number=1,
            verdict=Verdict.PARTIAL,
            evidence=[],
            gap="Cost targets not defined.",
            target_state="Cost targets are defined per component.",
            remediation_steps=[self._make_step()],
            effort=Effort.MEDIUM,
            impact=Impact.HIGH,
        )
        assert score.verdict == Verdict.PARTIAL
        assert score.probing_question is None

    def test_gap_requires_all_gap_fields(self) -> None:
        with pytest.raises(ValidationError, match="BR-04"):
            LawScore(
                law_id="law-3",
                law_title="Law 3",
                law_number=3,
                verdict=Verdict.GAP,
                evidence=[],
            )

    def test_strong_no_gap_fields_required(self) -> None:
        score = LawScore(
            law_id="law-1",
            law_title="Law 1",
            law_number=1,
            verdict=Verdict.STRONG,
            evidence=[],
        )
        assert score.verdict == Verdict.STRONG
        assert score.gap is None
        assert score.probing_question is None

    def test_non_unknown_no_probing_question_allowed(self) -> None:
        with pytest.raises(ValidationError, match="probing_question must be None"):
            LawScore(
                law_id="law-1",
                law_title="Law 1",
                law_number=1,
                verdict=Verdict.STRONG,
                evidence=[],
                probing_question="Should not be here.",
            )


# ── Scorecard invariants ──────────────────────────────────────────────────────


class TestScorecardInvariants:
    def _make_law_score(self, number: int) -> LawScore:
        return LawScore(
            law_id=f"law-{number}",
            law_title=f"Law {number}",
            law_number=number,
            verdict=Verdict.UNKNOWN,
            evidence=[],
            probing_question=f"Probing question for law {number}?",
        )

    def _make_scorecard(self, **kwargs: object) -> Scorecard:
        law_scores = [self._make_law_score(n) for n in range(1, 8)]
        defaults: dict[str, object] = {
            "law_scores": law_scores,
            "roadmap": RemediationRoadmap(steps=[]),
            "overall_verdict": "UNKNOWN — initial assessment",
            "caveat": CAVEAT_TEXT,
            "attribution": ATTRIBUTION_TEXT,
            "scan_metadata": ScanMetadata(
                files_scanned=0, iac_files=0, app_files=0,
                capped=False, layers_covered=[],
            ),
            "mode": ScoringMode.CODEBASE_REVIEW,
            "created_at": "2026-01-01T00:00:00Z",
        }
        defaults.update(kwargs)
        return Scorecard(**defaults)

    def test_exactly_seven_law_scores(self) -> None:
        sc = self._make_scorecard()
        assert len(sc.law_scores) == 7

    def test_rejects_wrong_law_count(self) -> None:
        with pytest.raises(ValidationError, match="exactly 7"):
            self._make_scorecard(law_scores=[self._make_law_score(1)])

    def test_attribution_required(self) -> None:
        with pytest.raises((ValidationError, ScorecardIntegrityError)):
            self._make_scorecard(attribution="")

    def test_caveat_required(self) -> None:
        with pytest.raises((ValidationError, ScorecardIntegrityError)):
            self._make_scorecard(caveat="")


# ── DecisionCheckResult invariants ───────────────────────────────────────────


class TestDecisionCheckResultInvariants:
    def _law_score(self, num: int) -> LawScore:
        return LawScore(
            law_id=f"law-{num}",
            law_title=f"Law {num}",
            law_number=num,
            verdict=Verdict.UNKNOWN,
            evidence=[],
            probing_question="What is the revenue model?",
        )

    def test_max_three_laws(self) -> None:
        with pytest.raises(ValidationError, match="must not exceed 3"):
            DecisionCheckResult(
                relevant_laws=[self._law_score(i) for i in range(1, 5)],
                recommendation="Test.",
                attribution=ATTRIBUTION_TEXT,
                decision_text="Use provisioned DynamoDB.",
            )

    def test_minimum_one_law(self) -> None:
        with pytest.raises(ValidationError, match="at least 1"):
            DecisionCheckResult(
                relevant_laws=[],
                recommendation="Test.",
                attribution=ATTRIBUTION_TEXT,
                decision_text="Use provisioned DynamoDB.",
            )

    def test_valid_one_to_three(self) -> None:
        result = DecisionCheckResult(
            relevant_laws=[self._law_score(1), self._law_score(5)],
            recommendation="Consider on-demand billing.",
            attribution=ATTRIBUTION_TEXT,
            decision_text="Use provisioned DynamoDB.",
        )
        assert len(result.relevant_laws) == 2
