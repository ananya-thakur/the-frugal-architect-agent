"""Entity serialisation / deserialisation tests (unit-8).

Validates that every entity can round-trip through to_dict() → from_dict()
without data loss, and that edge cases (optional None fields, JSON string-key
coercion for law_verdicts) are handled correctly.
"""

import pytest

from agent.memory_manager.entities import (
    Finding,
    LawScore,
    RemediationStep,
    Scorecard,
    ScorecardSummary,
)
from tests.memory_manager.conftest import make_law_score, make_scorecard, make_summary


class TestLawScoreRoundtrip:
    def test_full_roundtrip(self) -> None:
        ls = make_law_score(2, "Systems that are Cost Aware", "PARTIAL")
        restored = LawScore.from_dict(ls.to_dict())
        assert restored.law_number == 2
        assert restored.verdict == "PARTIAL"
        assert restored.gap == ls.gap
        assert restored.remediation_steps == ls.remediation_steps

    def test_optional_fields_none(self) -> None:
        ls = make_law_score(1, "Make Cost a Requirement", "STRONG")
        restored = LawScore.from_dict(ls.to_dict())
        assert restored.gap is None
        assert restored.target_state is None
        assert restored.remediation_steps is None
        assert restored.confidence_tag is None
        assert restored.evidence is None

    def test_unknown_verdict_roundtrip(self) -> None:
        ls = LawScore(law_number=3, law_title="Architecture for Cost", verdict="UNKNOWN")
        assert LawScore.from_dict(ls.to_dict()).verdict == "UNKNOWN"


class TestFindingRoundtrip:
    def test_firm_finding_roundtrip(self) -> None:
        f = Finding(
            id="f-001",
            law_number=2,
            law_title="Systems that are Cost Aware",
            confidence="FIRM",
            summary="Missing cost tags",
            cost_impact_explanation="Prevents cost attribution",
            needs_runtime_confirmation=False,
            file_path="infra/main.tf",
            resource_id="aws_dynamodb_table.main",
        )
        restored = Finding.from_dict(f.to_dict())
        assert restored.id == "f-001"
        assert restored.confidence == "FIRM"
        assert not restored.needs_runtime_confirmation
        assert restored.file_path == "infra/main.tf"
        assert restored.anti_pattern_type is None

    def test_possible_finding_roundtrip(self) -> None:
        f = Finding(
            id="f-002",
            law_number=4,
            law_title="Unobserved Systems",
            confidence="POSSIBLE",
            summary="N+1 query pattern",
            cost_impact_explanation="Excessive read units",
            needs_runtime_confirmation=True,
            anti_pattern_type="n_plus_one_query",
            line_number=42,
        )
        restored = Finding.from_dict(f.to_dict())
        assert restored.confidence == "POSSIBLE"
        assert restored.anti_pattern_type == "n_plus_one_query"
        assert restored.needs_runtime_confirmation


class TestRemediationStepRoundtrip:
    def test_roundtrip(self) -> None:
        step = RemediationStep(
            rank=1,
            law_number=2,
            action="Add cost allocation tags",
            underlying_principle="Cost awareness",
            effort="low",
            impact="high",
        )
        restored = RemediationStep.from_dict(step.to_dict())
        assert restored.rank == 1
        assert restored.effort == "low"
        assert restored.impact == "high"


class TestScorecardRoundtrip:
    def test_full_roundtrip(self) -> None:
        sc = make_scorecard("sess-42")
        restored = Scorecard.from_dict(sc.to_dict())
        assert restored.session_id == "sess-42"
        assert len(restored.law_scores) == 7
        assert len(restored.findings) == 2
        assert len(restored.roadmap) == 1
        assert restored.caveat == sc.caveat

    def test_law_scores_preserved(self) -> None:
        sc = make_scorecard()
        restored = Scorecard.from_dict(sc.to_dict())
        for orig, rest in zip(sc.law_scores, restored.law_scores):
            assert orig.law_number == rest.law_number
            assert orig.verdict == rest.verdict

    def test_empty_findings_and_roadmap(self) -> None:
        sc = make_scorecard()
        sc_empty = Scorecard(
            session_id=sc.session_id,
            overall_verdict="STRONG",
            biggest_opportunity="None — all laws met",
            law_scores=sc.law_scores,
            findings=[],
            roadmap=[],
            caveat=sc.caveat,
            created_at=sc.created_at,
        )
        restored = Scorecard.from_dict(sc_empty.to_dict())
        assert restored.findings == []
        assert restored.roadmap == []


class TestScorecardSummaryRoundtrip:
    def test_full_roundtrip(self) -> None:
        summary = make_summary()
        restored = ScorecardSummary.from_dict(summary.to_dict())
        assert restored.user_id == "user-001"
        assert restored.project_id == "proj-001"
        assert restored.law_verdicts == summary.law_verdicts
        assert restored.top_finding_summaries == summary.top_finding_summaries

    def test_law_verdicts_string_key_coercion(self) -> None:
        """JSON round-trip converts int keys to str and back."""
        import json

        summary = make_summary()
        raw = json.dumps(summary.to_dict())
        data = json.loads(raw)
        # After JSON round-trip all keys are strings
        assert all(isinstance(k, str) for k in data["law_verdicts"])
        # from_dict must coerce them back to int
        restored = ScorecardSummary.from_dict(data)
        assert all(isinstance(k, int) for k in restored.law_verdicts)
        assert restored.law_verdicts[4] == "GAP"

    def test_last_review_at_defaults_to_empty(self) -> None:
        summary = make_summary()
        assert summary.last_review_at == ""

    def test_empty_top_finding_summaries(self) -> None:
        summary = ScorecardSummary(
            user_id="u",
            project_id="p",
            overall_verdict="STRONG",
            law_verdicts={i: "STRONG" for i in range(1, 8)},
            top_finding_summaries=[],
        )
        restored = ScorecardSummary.from_dict(summary.to_dict())
        assert restored.top_finding_summaries == []
