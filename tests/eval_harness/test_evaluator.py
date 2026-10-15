"""
Tests for eval/evaluator.py — 4-dimension evaluation logic.

Coverage:
  - assess_groundedness: expects_citation, expects_evidence, both, either absent
  - assess_citation_correctness: correct law found, wrong law, fabricated law ID
  - assess_tool_selection: FIRM present/absent, POSSIBLE present/absent, unknown conf
  - assess_unknown_discipline: UNKNOWN+question present, fabricated verdict, no UNKNOWN
  - evaluate_test_case: routes to correct assessors based on configured assertions
  - aggregate_results: correct dimension tallies across multiple test case results
"""

from __future__ import annotations

import pytest

from eval.evaluator import (
    aggregate_results,
    assess_citation_correctness,
    assess_groundedness,
    assess_tool_selection,
    assess_unknown_discipline,
    evaluate_test_case,
)
from eval.evaluator import TestCaseResult
from eval.models import (
    CitationCorrectnessAssertion,
    GroundednessAssertion,
    TestCase,
    TestCaseAssertions,
    ToolSelectionAssertion,
    UnknownDisciplineAssertion,
)


# ── assess_groundedness ───────────────────────────────────────────────────────


class TestAssessGroundedness:
    def test_pass_when_law_and_evidence_present(self):
        response = "Law V violation: always-on EC2 instance. FIRM confidence. file: infra/stack.py"
        result = assess_groundedness(response, GroundednessAssertion(True, True))
        assert result.passed is True

    def test_fail_when_no_law_citation(self):
        response = "Always-on EC2 instance. FIRM confidence. file: infra/stack.py"
        result = assess_groundedness(response, GroundednessAssertion(True, True))
        assert result.passed is False
        assert "law_citation=False" in result.actual

    def test_fail_when_no_evidence(self):
        response = "Law V issue found in your project."
        result = assess_groundedness(response, GroundednessAssertion(True, True))
        assert result.passed is False
        assert "evidence=False" in result.actual

    def test_pass_when_only_law_required(self):
        response = "Law III mentions this."
        result = assess_groundedness(response, GroundednessAssertion(expects_law_citation=True, expects_evidence=False))
        assert result.passed is True

    def test_pass_when_only_evidence_required(self):
        response = "POSSIBLE finding in service.py."
        result = assess_groundedness(response, GroundednessAssertion(expects_law_citation=False, expects_evidence=True))
        assert result.passed is True

    def test_all_roman_numerals_recognized(self):
        for law_id in ("I", "II", "III", "IV", "V", "VI", "VII"):
            response = f"Law {law_id} violation. FIRM"
            result = assess_groundedness(response, GroundednessAssertion(True, True))
            assert result.passed is True, f"Law {law_id} should be recognized"


# ── assess_citation_correctness ───────────────────────────────────────────────


class TestAssessCitationCorrectness:
    def test_pass_when_expected_law_cited(self):
        response = "This violates Law VI — Incremental Frugality. FIRM"
        result = assess_citation_correctness(response, CitationCorrectnessAssertion("VI"))
        assert result.passed is True

    def test_fail_when_expected_law_absent(self):
        response = "Law I issue detected."
        result = assess_citation_correctness(response, CitationCorrectnessAssertion("VI"))
        assert result.passed is False
        assert "found=False" in result.actual

    def test_fail_on_fabricated_law_id(self):
        response = "Law IX violation detected."  # IX is not a valid law
        result = assess_citation_correctness(response, CitationCorrectnessAssertion("I"))
        # Fabricated law present → fail even if Law I is also present
        assert result.passed is False

    def test_pass_case_insensitive(self):
        response = "law vi recommendation: use incremental deployments"
        result = assess_citation_correctness(response, CitationCorrectnessAssertion("vi"))
        assert result.passed is True

    def test_multiple_valid_laws_no_fabrication(self):
        response = "Law I, Law IV, Law VII — all relevant here. FIRM"
        result = assess_citation_correctness(response, CitationCorrectnessAssertion("IV"))
        assert result.passed is True


# ── assess_tool_selection ─────────────────────────────────────────────────────


class TestAssessToolSelection:
    def test_firm_present_passes(self):
        response = "Findings (FIRM): Law V — always-on EC2 in infra/cdk.py"
        result = assess_tool_selection(response, ToolSelectionAssertion("FIRM"))
        assert result.passed is True

    def test_firm_absent_fails(self):
        response = "POSSIBLE: N+1 query in service.py"
        result = assess_tool_selection(response, ToolSelectionAssertion("FIRM"))
        assert result.passed is False

    def test_possible_present_passes(self):
        response = "POSSIBLE: N+1 query pattern in service.py — confirm with profiling"
        result = assess_tool_selection(response, ToolSelectionAssertion("POSSIBLE"))
        assert result.passed is True

    def test_possible_absent_fails(self):
        response = "FIRM: over-provisioned RDS in database.tf"
        result = assess_tool_selection(response, ToolSelectionAssertion("POSSIBLE"))
        assert result.passed is False

    def test_unknown_confidence_value_fails(self):
        response = "FIRM and POSSIBLE findings"
        result = assess_tool_selection(response, ToolSelectionAssertion("MAYBE"))
        assert result.passed is False

    def test_case_insensitive_expected(self):
        response = "FIRM finding in cdk.py"
        result = assess_tool_selection(response, ToolSelectionAssertion("firm"))
        assert result.passed is True


# ── assess_unknown_discipline ─────────────────────────────────────────────────


class TestAssessUnknownDiscipline:
    def test_pass_when_unknown_and_question(self):
        response = "UNKNOWN — what is the expected monthly revenue? Please provide business context."
        result = assess_unknown_discipline(response, UnknownDisciplineAssertion(True))
        assert result.passed is True

    def test_fail_when_no_unknown(self):
        response = "STRONG — your architecture is frugal across all dimensions."
        result = assess_unknown_discipline(response, UnknownDisciplineAssertion(True))
        assert result.passed is False
        assert "UNKNOWN=False" in result.actual

    def test_fail_when_no_question(self):
        response = "UNKNOWN. The system is not scoreable without context."
        result = assess_unknown_discipline(response, UnknownDisciplineAssertion(True))
        assert result.passed is False
        assert "probing_question=False" in result.actual

    def test_fail_on_fabricated_verdict_without_unknown(self):
        response = "PARTIAL — some frugality measures are in place."
        result = assess_unknown_discipline(response, UnknownDisciplineAssertion(True))
        assert result.passed is False
        assert "fabricated_verdict=True" in result.actual

    def test_pass_when_unknown_and_question_co_located_with_other_verdicts(self):
        # UNKNOWN present alongside other verdicts for dimensions that ARE answerable
        response = "Law II: UNKNOWN — what is your revenue model? Law IV: PARTIAL"
        result = assess_unknown_discipline(response, UnknownDisciplineAssertion(True))
        # UNKNOWN is present and there is a question — should pass
        assert result.passed is True


# ── evaluate_test_case ────────────────────────────────────────────────────────


class TestEvaluateTestCase:
    def _make_case(self, **assertions) -> TestCase:
        return TestCase(
            test_id="t1",
            description="",
            input_text="test",
            assertions=TestCaseAssertions(**assertions),
        )

    def test_only_configured_dimensions_set(self):
        case = self._make_case(
            groundedness=GroundednessAssertion(True, True),
        )
        response = "Law IV mentioned. FIRM confidence in cdk.py"
        result = evaluate_test_case(case, response)
        assert result.groundedness is not None
        assert result.citation_correctness is None
        assert result.tool_selection is None
        assert result.unknown_discipline is None

    def test_all_dimensions_configured(self):
        case = self._make_case(
            groundedness=GroundednessAssertion(True, True),
            citation_correctness=CitationCorrectnessAssertion("IV"),
            tool_selection=ToolSelectionAssertion("FIRM"),
            unknown_discipline=None,
        )
        response = "Law IV: FIRM finding in cdk.py."
        result = evaluate_test_case(case, response)
        assert result.groundedness is not None
        assert result.citation_correctness is not None
        assert result.tool_selection is not None
        assert result.unknown_discipline is None


# ── aggregate_results ─────────────────────────────────────────────────────────


class TestAggregateResults:
    def test_all_pass(self):
        cases = [
            TestCaseResult(
                test_id="t1",
                groundedness=_pass_result(),
                tool_selection=_pass_result(),
            ),
            TestCaseResult(
                test_id="t2",
                groundedness=_pass_result(),
                tool_selection=_pass_result(),
            ),
        ]
        dims = aggregate_results(cases)
        assert dims["groundedness"].pass_count == 2
        assert dims["groundedness"].fail_count == 0
        assert dims["groundedness"].pass_rate == pytest.approx(1.0)

    def test_mixed_pass_fail(self):
        cases = [
            TestCaseResult(test_id="t1", groundedness=_pass_result()),
            TestCaseResult(test_id="t2", groundedness=_fail_result()),
            TestCaseResult(test_id="t3", groundedness=_fail_result()),
        ]
        dims = aggregate_results(cases)
        g = dims["groundedness"]
        assert g.pass_count == 1
        assert g.fail_count == 2
        assert g.pass_rate == pytest.approx(1 / 3)
        assert "t2" in g.failing_case_ids
        assert "t3" in g.failing_case_ids

    def test_dimension_with_no_cases_has_zero_counts(self):
        cases = [TestCaseResult(test_id="t1", groundedness=_pass_result())]
        dims = aggregate_results(cases)
        assert dims["citation_correctness"].pass_count == 0
        assert dims["citation_correctness"].fail_count == 0
        assert dims["citation_correctness"].pass_rate == pytest.approx(0.0)

    def test_failing_assertions_populated(self):
        cases = [TestCaseResult(test_id="fail-case", groundedness=_fail_result())]
        dims = aggregate_results(cases)
        assert len(dims["groundedness"].failing_assertions) == 1
        fa = dims["groundedness"].failing_assertions[0]
        assert fa.test_id == "fail-case"
        assert fa.dimension == "groundedness"


# ── helpers ───────────────────────────────────────────────────────────────────

from eval.evaluator import AssertionResult  # noqa: E402


def _pass_result() -> AssertionResult:
    return AssertionResult(passed=True, expected="x", actual="x")


def _fail_result() -> AssertionResult:
    return AssertionResult(passed=False, expected="x", actual="y")
