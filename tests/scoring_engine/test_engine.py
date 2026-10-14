"""Tests for scoring-engine core algorithms and business rules enforcement."""

from __future__ import annotations

import pytest

from agent.app_scanner.models import PossibleFinding
from agent.iac_scanner.models import FirmFinding
from agent.rubric_core.models import Rubric
from agent.scoring_engine.engine import (
    build_roadmap,
    decision_check,
    derive_overall,
    extract_scan_metadata,
    score_law,
    score_project,
)
from agent.scoring_engine.models import (
    ATTRIBUTION_TEXT,
    CAVEAT_TEXT,
    BusinessContext,
    ConfidenceTag,
    InputSizeLimitError,
    LawScore,
    RemediationRoadmap,
    RubricIntegrityError,
    RubricNotLoadedError,
    Scorecard,
    ScoringMode,
    Verdict,
)


# ── BR-12: RubricNotLoadedError ───────────────────────────────────────────────


class TestRubricGuards:
    def test_score_project_raises_on_none_rubric(self) -> None:
        with pytest.raises(RubricNotLoadedError):
            score_project(firm=[], possible=[], rubric=None)  # type: ignore[arg-type]

    def test_score_project_raises_on_non_rubric_object(self) -> None:
        with pytest.raises(RubricIntegrityError):
            score_project(firm=[], possible=[], rubric={"laws": []})  # type: ignore[arg-type]

    def test_decision_check_raises_on_none_rubric(self) -> None:
        with pytest.raises(RubricNotLoadedError):
            decision_check("Use DynamoDB provisioned mode", rubric=None)  # type: ignore[arg-type]


# ── BR-06: Exactly 7 LawScores ───────────────────────────────────────────────


class TestCompleteSevenLawCoverage:
    def test_score_project_returns_exactly_seven_laws(
        self, rubric: Rubric
    ) -> None:
        scorecard = score_project(firm=[], possible=[], rubric=rubric)
        assert len(scorecard.law_scores) == 7

    def test_law_scores_ordered_by_number(self, rubric: Rubric) -> None:
        scorecard = score_project(firm=[], possible=[], rubric=rubric)
        numbers = [ls.law_number for ls in scorecard.law_scores]
        assert numbers == sorted(numbers)
        assert numbers == list(range(1, 8))


# ── BR-01: No findings → UNKNOWN ────────────────────────────────────────────


class TestUnknownOnNoFindings:
    def test_all_laws_unknown_when_no_findings(self, rubric: Rubric) -> None:
        scorecard = score_project(firm=[], possible=[], rubric=rubric)
        for ls in scorecard.law_scores:
            assert ls.verdict == Verdict.UNKNOWN, f"Law {ls.law_number} should be UNKNOWN"

    def test_unknown_law_has_probing_question(self, rubric: Rubric) -> None:
        """BR-03: UNKNOWN verdict must include specific probing question."""
        scorecard = score_project(firm=[], possible=[], rubric=rubric)
        for ls in scorecard.law_scores:
            assert ls.probing_question is not None
            assert len(ls.probing_question) > 10, "Probing question must not be a placeholder"

    def test_unknown_probing_question_from_rubric(self, rubric: Rubric) -> None:
        """BR-03: probing_question must be drawn from rubric, not invented."""
        scorecard = score_project(firm=[], possible=[], rubric=rubric)
        law1_score = next(ls for ls in scorecard.law_scores if ls.law_number == 1)
        rubric_law1 = next(law for law in rubric.laws if law.number == 1)
        assert law1_score.probing_question in rubric_law1.probing_questions


# ── BR-02: Law II UNKNOWN without business context ───────────────────────────


class TestLawIIUnknown:
    def test_law_ii_unknown_without_biz(self, rubric: Rubric) -> None:
        """BR-02: Law II must be UNKNOWN if revenue_model is absent."""
        scorecard = score_project(
            firm=[], possible=[], rubric=rubric, biz=None
        )
        law2 = next(ls for ls in scorecard.law_scores if ls.law_number == 2)
        assert law2.verdict == Verdict.UNKNOWN

    def test_law_ii_unknown_probing_question_present(self, rubric: Rubric) -> None:
        scorecard = score_project(firm=[], possible=[], rubric=rubric)
        law2 = next(ls for ls in scorecard.law_scores if ls.law_number == 2)
        assert law2.probing_question is not None
        assert "revenue" in law2.probing_question.lower() or "business" in law2.probing_question.lower()

    def test_law_ii_scored_with_revenue_model(
        self, rubric: Rubric, biz_with_revenue: BusinessContext,
        firm_finding_law1: FirmFinding,
    ) -> None:
        """With revenue_model provided, Law II gets a real verdict (not forced UNKNOWN)."""
        # Provide a finding for law-2 to avoid UNKNOWN from no-evidence path
        firm2 = FirmFinding(
            law_id="law-2",
            law_title="Systems That Are Cost-Aware Are Systems That Can Be Optimized",
            file_path="infra/monitoring.tf",
            resource_id="CostAlerts",
            resource_type="aws_budgets_budget",
            line=1,
            gap="No cost anomaly detection configured.",
            evidence="No aws_ce_anomaly_detector resource found.",
            signal_id="tf-no-anomaly-detection",
        )
        scorecard = score_project(
            firm=[firm2], possible=[], rubric=rubric, biz=biz_with_revenue
        )
        law2 = next(ls for ls in scorecard.law_scores if ls.law_number == 2)
        assert law2.verdict != Verdict.UNKNOWN


# ── BR-04: PARTIAL/GAP require complete gap fields ────────────────────────────


class TestGapFieldsOnPartialAndGap:
    def test_partial_law_has_all_gap_fields(
        self, rubric: Rubric, firm_finding_law1: FirmFinding
    ) -> None:
        """BR-04: All 6 gap fields must be present for PARTIAL or GAP."""
        scorecard = score_project(
            firm=[firm_finding_law1], possible=[], rubric=rubric
        )
        law1 = next(ls for ls in scorecard.law_scores if ls.law_number == 1)
        assert law1.verdict in (Verdict.PARTIAL, Verdict.GAP)
        assert law1.gap is not None
        assert law1.target_state is not None
        assert law1.remediation_steps is not None
        assert law1.effort is not None
        assert law1.impact is not None
        assert len(law1.remediation_steps) >= 1

    def test_partial_law_evidence_list_non_empty(
        self, rubric: Rubric, firm_finding_law1: FirmFinding
    ) -> None:
        scorecard = score_project(
            firm=[firm_finding_law1], possible=[], rubric=rubric
        )
        law1 = next(ls for ls in scorecard.law_scores if ls.law_number == 1)
        assert len(law1.evidence) >= 1


# ── BR-05: target_state is rubric verbatim ────────────────────────────────────


class TestTargetStateVerbatim:
    def test_target_state_equals_rubric_strong_looks_like(
        self, rubric: Rubric, firm_finding_law1: FirmFinding
    ) -> None:
        """BR-05: target_state must copy strong_looks_like verbatim from rubric."""
        scorecard = score_project(
            firm=[firm_finding_law1], possible=[], rubric=rubric
        )
        law1 = next(ls for ls in scorecard.law_scores if ls.law_number == 1)
        rubric_law1 = next(law for law in rubric.laws if law.number == 1)
        assert law1.target_state == rubric_law1.strong_looks_like


# ── BR-07: FIRM evidence before POSSIBLE ─────────────────────────────────────


class TestEvidenceOrdering:
    def test_firm_evidence_precedes_possible(
        self,
        rubric: Rubric,
        firm_finding_law1: FirmFinding,
        possible_finding_law4: PossibleFinding,
    ) -> None:
        """BR-07: FIRM items must precede POSSIBLE items in evidence list."""
        # Give a firm finding for law-1 AND possible for law-4
        firm_4 = FirmFinding(
            law_id="law-4",
            law_title="Unobserved Systems Lead to Unknown Costs",
            file_path="infra/monitoring.tf",
            resource_id="CloudWatchAlarm",
            resource_type="aws_cloudwatch_metric_alarm",
            line=5,
            gap="No cost anomaly detection or alerting configured.",
            evidence="CloudWatchAlarm in infra/monitoring.tf has no cost metric.",
            signal_id="tf-no-cost-alarm",
        )
        scorecard = score_project(
            firm=[firm_finding_law1, firm_4],
            possible=[possible_finding_law4],
            rubric=rubric,
        )
        # Law 4 has both firm and possible findings
        law4 = next(ls for ls in scorecard.law_scores if ls.law_number == 4)
        if len(law4.evidence) >= 2:
            firm_idx = next(
                (i for i, e in enumerate(law4.evidence) if e.source_type == ConfidenceTag.FIRM),
                -1,
            )
            poss_idx = next(
                (i for i, e in enumerate(law4.evidence) if e.source_type == ConfidenceTag.POSSIBLE),
                -1,
            )
            if firm_idx >= 0 and poss_idx >= 0:
                assert firm_idx < poss_idx


# ── BR-08: POSSIBLE-sourced steps carry runtime confirmation note ─────────────


class TestRuntimeConfirmationNote:
    def test_possible_sourced_step_has_runtime_note(
        self,
        rubric: Rubric,
        possible_finding_law4: PossibleFinding,
    ) -> None:
        """BR-08: RemediationStep from POSSIBLE evidence must include runtime_confirmation_note."""
        scorecard = score_project(
            firm=[], possible=[possible_finding_law4], rubric=rubric
        )
        law4 = next(ls for ls in scorecard.law_scores if ls.law_number == 4)
        possible_steps = [
            s for s in (law4.remediation_steps or [])
            if s.source_confidence == ConfidenceTag.POSSIBLE
        ]
        assert len(possible_steps) >= 1
        for step in possible_steps:
            assert step.runtime_confirmation_note is not None


# ── BR-09: Roadmap ordered by priority formula ────────────────────────────────


class TestRoadmapOrdering:
    def test_roadmap_sorted_by_priority_desc(
        self,
        rubric: Rubric,
        firm_finding_law1: FirmFinding,
        firm_finding_law3: FirmFinding,
    ) -> None:
        """BR-09: roadmap steps sorted by priority_score DESC."""
        scorecard = score_project(
            firm=[firm_finding_law1, firm_finding_law3], possible=[], rubric=rubric
        )
        steps = scorecard.roadmap.steps
        if len(steps) >= 2:
            for i in range(len(steps) - 1):
                assert steps[i].priority_score >= steps[i + 1].priority_score, (
                    f"Step {i} (score={steps[i].priority_score}) should be >= "
                    f"step {i+1} (score={steps[i+1].priority_score})"
                )

    def test_roadmap_steps_have_priority_rank(
        self,
        rubric: Rubric,
        firm_finding_law1: FirmFinding,
    ) -> None:
        """Roadmap steps must have priority_rank assigned (starting from 1)."""
        scorecard = score_project(
            firm=[firm_finding_law1], possible=[], rubric=rubric
        )
        for i, step in enumerate(scorecard.roadmap.steps, 1):
            assert step.priority_rank == i


# ── BR-10, BR-11: Attribution and caveat always present ──────────────────────


class TestAttributionAndCaveat:
    def test_attribution_present_on_every_scorecard(self, rubric: Rubric) -> None:
        """BR-10: attribution must always be present."""
        scorecard = score_project(firm=[], possible=[], rubric=rubric)
        assert scorecard.attribution
        assert "Werner Vogels" in scorecard.attribution

    def test_caveat_present_on_every_scorecard(self, rubric: Rubric) -> None:
        """BR-11: caveat must always be present."""
        scorecard = score_project(firm=[], possible=[], rubric=rubric)
        assert scorecard.caveat
        assert "static analysis" in scorecard.caveat.lower()

    def test_attribution_text_matches_constant(self, rubric: Rubric) -> None:
        scorecard = score_project(firm=[], possible=[], rubric=rubric)
        assert scorecard.attribution == ATTRIBUTION_TEXT

    def test_caveat_text_matches_constant(self, rubric: Rubric) -> None:
        scorecard = score_project(firm=[], possible=[], rubric=rubric)
        assert scorecard.caveat == CAVEAT_TEXT


# ── BR-13: No hardcoded law references ────────────────────────────────────────


class TestRubricGrounding:
    def test_law_scores_ids_match_rubric(self, rubric: Rubric) -> None:
        """BR-13: law_id values must derive from the loaded rubric."""
        scorecard = score_project(firm=[], possible=[], rubric=rubric)
        rubric_ids = {f"law-{law.number}" for law in rubric.laws}
        for ls in scorecard.law_scores:
            assert ls.law_id in rubric_ids
            assert ls.law_title in {law.title for law in rubric.laws}


# ── SR-06: Size limits ────────────────────────────────────────────────────────


class TestSizeLimits:
    def _make_firm(self, n: int, law_num: int = 1) -> list[FirmFinding]:
        return [
            FirmFinding(
                law_id=f"law-{law_num}",
                law_title=f"Law {law_num}",
                file_path=f"infra/file_{i}.tf",
                resource_id=f"Resource{i}",
                resource_type="aws_lambda_function",
                line=i,
                gap="Over-provisioned memory.",
                evidence=f"Resource{i} sets memory=4096.",
                signal_id="tf-lambda-over-memory",
            )
            for i in range(n)
        ]

    def test_raises_when_firm_exceeds_limit(self, rubric: Rubric) -> None:
        with pytest.raises(InputSizeLimitError, match="firm findings exceed"):
            score_project(firm=self._make_firm(501), possible=[], rubric=rubric)

    def test_raises_when_combined_exceeds_limit(self, rubric: Rubric) -> None:
        firm_list = self._make_firm(500)
        possible_list = [
            PossibleFinding(
                finding_id=f"pf-{i}",
                law_id=4,
                law_title="Law 4",
                anti_pattern_type="n_plus_one_query",
                file_path=f"src/file_{i}.py",
                line_number=i,
                evidence="for x in items: db.query(x)",
                confidence="POSSIBLE",
                runtime_caveat="Confirm with runtime data.",
                cost_impact_explanation="N+1 multiplies DB costs.",
            )
            for i in range(301)
        ]
        with pytest.raises(InputSizeLimitError, match="combined"):
            score_project(firm=firm_list, possible=possible_list, rubric=rubric)


# ── BR-16, BR-17: DECISION CHECK constraints ─────────────────────────────────


class TestDecisionCheck:
    def test_decision_check_returns_one_to_three_laws(self, rubric: Rubric) -> None:
        """BR-16: DECISION CHECK must return 1–3 laws."""
        result = decision_check(
            "Should we use DynamoDB on-demand or provisioned capacity?",
            rubric=rubric,
        )
        assert 1 <= len(result.relevant_laws) <= 3

    def test_decision_check_recommendation_non_empty(self, rubric: Rubric) -> None:
        """BR-17: recommendation must be non-empty."""
        result = decision_check(
            "Should we use DynamoDB on-demand or provisioned capacity?",
            rubric=rubric,
        )
        assert len(result.recommendation) > 50

    def test_decision_check_recommendation_cites_law(self, rubric: Rubric) -> None:
        """BR-17: recommendation must cite law number and title."""
        result = decision_check(
            "Should we use DynamoDB on-demand or provisioned capacity?",
            rubric=rubric,
        )
        # At least one cited law should appear in the recommendation
        cited = any(
            f"Law {ls.law_number}" in result.recommendation
            for ls in result.relevant_laws
        )
        assert cited

    def test_decision_check_attribution_present(self, rubric: Rubric) -> None:
        result = decision_check("Use serverless Lambda vs ECS Fargate?", rubric=rubric)
        assert "Werner Vogels" in result.attribution

    def test_decision_check_decision_text_preserved(self, rubric: Rubric) -> None:
        text = "Should we cache API responses in ElastiCache?"
        result = decision_check(text, rubric=rubric)
        assert result.decision_text == text


# ── DESIGN REVIEW mode ────────────────────────────────────────────────────────


class TestDesignReviewMode:
    def test_design_review_unknown_on_no_answer(self, rubric: Rubric) -> None:
        """BR-15: DESIGN REVIEW with no answer for a law yields UNKNOWN."""
        biz = BusinessContext()
        scorecard = score_project(
            firm=[], possible=[], rubric=rubric, biz=biz,
            mode=ScoringMode.DESIGN_REVIEW,
        )
        # Without any law_answers, all should be UNKNOWN
        for ls in scorecard.law_scores:
            assert ls.verdict == Verdict.UNKNOWN

    def test_design_review_strong_with_comprehensive_answer(self, rubric: Rubric) -> None:
        """DESIGN REVIEW with a comprehensive answer covering most criteria can yield STRONG."""
        rubric_law1 = next(law for law in rubric.laws if law.number == 1)
        # Use the strong_looks_like text itself as the answer (guarantees coverage)
        biz = BusinessContext(
            revenue_model="SaaS subscription product",
            law_answers={"law-1": rubric_law1.strong_looks_like},
        )
        scorecard = score_project(
            firm=[], possible=[], rubric=rubric, biz=biz,
            mode=ScoringMode.DESIGN_REVIEW,
        )
        law1 = next(ls for ls in scorecard.law_scores if ls.law_number == 1)
        assert law1.verdict == Verdict.STRONG


# ── Overall verdict derivation ────────────────────────────────────────────────


class TestOverallVerdict:
    def _make_scores(self, verdicts: list[Verdict]) -> list[LawScore]:
        scores: list[LawScore] = []
        for i, verdict in enumerate(verdicts, 1):
            base: dict = {
                "law_id": f"law-{i}",
                "law_title": f"Law {i}",
                "law_number": i,
                "verdict": verdict,
                "evidence": [],
            }
            if verdict == Verdict.UNKNOWN:
                base["probing_question"] = f"Probing question for law {i}?"
            elif verdict in (Verdict.PARTIAL, Verdict.GAP):
                base.update({
                    "gap": f"Gap for law {i}",
                    "target_state": f"Target for law {i}",
                    "remediation_steps": [],
                    "effort": "LOW",
                    "impact": "MEDIUM",
                })
            scores.append(LawScore(**base))
        return scores

    def test_significant_gaps_three_or_more_gaps(self) -> None:
        verdicts = [Verdict.GAP] * 3 + [Verdict.UNKNOWN] * 4
        scores = self._make_scores(verdicts)
        overall, _ = derive_overall(scores, RemediationRoadmap(steps=[]))
        assert "SIGNIFICANT GAPS" in overall

    def test_partial_alignment_one_gap(self) -> None:
        verdicts = [Verdict.GAP] + [Verdict.UNKNOWN] * 6
        scores = self._make_scores(verdicts)
        overall, _ = derive_overall(scores, RemediationRoadmap(steps=[]))
        assert "PARTIAL ALIGNMENT" in overall

    def test_mostly_aligned_one_partial(self) -> None:
        verdicts = [Verdict.PARTIAL] + [Verdict.STRONG] * 6
        scores = self._make_scores(verdicts)
        overall, _ = derive_overall(scores, RemediationRoadmap(steps=[]))
        assert "MOSTLY ALIGNED" in overall

    def test_strong_alignment_all_strong(self) -> None:
        verdicts = [Verdict.STRONG] * 7
        scores = self._make_scores(verdicts)
        overall, _ = derive_overall(scores, RemediationRoadmap(steps=[]))
        assert "STRONG ALIGNMENT" in overall


# ── Portable principles (BR-14) ───────────────────────────────────────────────


class TestPortablePrinciples:
    def test_firm_remediation_steps_have_portable_principle(
        self, rubric: Rubric, firm_finding_law1: FirmFinding
    ) -> None:
        """BR-14: Every RemediationStep must include portable_principle."""
        scorecard = score_project(
            firm=[firm_finding_law1], possible=[], rubric=rubric
        )
        law1 = next(ls for ls in scorecard.law_scores if ls.law_number == 1)
        for step in (law1.remediation_steps or []):
            assert step.portable_principle
            assert len(step.portable_principle) > 5

    def test_possible_remediation_steps_have_portable_principle(
        self, rubric: Rubric, possible_finding_law4: PossibleFinding
    ) -> None:
        scorecard = score_project(
            firm=[], possible=[possible_finding_law4], rubric=rubric
        )
        law4 = next(ls for ls in scorecard.law_scores if ls.law_number == 4)
        for step in (law4.remediation_steps or []):
            assert step.portable_principle


# ── scan_metadata extraction ──────────────────────────────────────────────────


class TestScanMetadata:
    def test_empty_scan_metadata(self, rubric: Rubric) -> None:
        scorecard = score_project(firm=[], possible=[], rubric=rubric)
        assert scorecard.scan_metadata.files_scanned == 0
        assert scorecard.scan_metadata.iac_files == 0
        assert not scorecard.scan_metadata.capped

    def test_populated_scan_metadata(
        self, rubric: Rubric, firm_finding_law1: FirmFinding
    ) -> None:
        scorecard = score_project(
            firm=[firm_finding_law1], possible=[], rubric=rubric
        )
        assert scorecard.scan_metadata.iac_files >= 1
        assert scorecard.scan_metadata.files_scanned >= 1

    def test_layers_covered_iac_present(
        self, rubric: Rubric, firm_finding_law1: FirmFinding
    ) -> None:
        scorecard = score_project(
            firm=[firm_finding_law1], possible=[], rubric=rubric
        )
        assert len(scorecard.scan_metadata.layers_covered) >= 1
