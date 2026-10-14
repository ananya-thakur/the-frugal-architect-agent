"""Tests for scoring-engine Strands tool wrappers and findings cache."""

from __future__ import annotations

import pytest

from agent.app_scanner.models import PossibleFinding
from agent.iac_scanner.models import FirmFinding
from agent.rubric_core.engine import load_rubric
from agent.scoring_engine.tool import (
    evaluate_decision,
    get_cached_findings,
    register_scan_results,
    score_project as tool_score_project,
)


@pytest.fixture(autouse=True)
def clear_findings_cache() -> None:
    """Clear the module-level findings cache between tests."""
    from agent.scoring_engine import tool as tool_module

    tool_module._findings_cache.clear()
    yield
    tool_module._findings_cache.clear()


@pytest.fixture
def firm1() -> FirmFinding:
    return FirmFinding(
        law_id="law-1",
        law_title="Make Cost a Non-Functional Requirement",
        file_path="infra/lambda_stack.py",
        resource_id="MyFunction",
        resource_type="aws_lambda_function",
        line=42,
        gap="Lambda function provisioned with more than 1024 MB.",
        evidence="MyFunction sets memory_size=4096 MB.",
        signal_id="tf-lambda-over-memory",
    )


@pytest.fixture
def possible4() -> PossibleFinding:
    return PossibleFinding(
        finding_id="pf-tool-test-001",
        law_id=4,
        law_title="Unobserved Systems Lead to Unknown Costs",
        anti_pattern_type="n_plus_one_query",
        file_path="src/orders.py",
        line_number=30,
        evidence="for o in orders: db.query(o.id)",
        confidence="POSSIBLE",
        runtime_caveat="Confirm with runtime data.",
        cost_impact_explanation="N+1 multiplies DB cost.",
    )


# ── Cache management ──────────────────────────────────────────────────────────


class TestFindingsCache:
    def test_register_and_retrieve(
        self, firm1: FirmFinding, possible4: PossibleFinding
    ) -> None:
        register_scan_results("repo:test", [firm1], [possible4])
        result = get_cached_findings("repo:test")
        assert result is not None
        firm_list, poss_list = result
        assert len(firm_list) == 1
        assert len(poss_list) == 1
        assert firm_list[0].law_id == "law-1"
        assert poss_list[0].law_id == 4

    def test_returns_none_for_unknown_ref(self) -> None:
        result = get_cached_findings("repo:not-registered")
        assert result is None

    def test_overwrite_existing_entry(self, firm1: FirmFinding) -> None:
        register_scan_results("repo:overwrite", [firm1], [])
        register_scan_results("repo:overwrite", [], [])
        result = get_cached_findings("repo:overwrite")
        assert result is not None
        firm_list, poss_list = result
        assert len(firm_list) == 0
        assert len(poss_list) == 0

    def test_multiple_repos_isolated(
        self, firm1: FirmFinding, possible4: PossibleFinding
    ) -> None:
        register_scan_results("repo:alpha", [firm1], [])
        register_scan_results("repo:beta", [], [possible4])
        alpha = get_cached_findings("repo:alpha")
        beta = get_cached_findings("repo:beta")
        assert alpha is not None and len(alpha[0]) == 1
        assert beta is not None and len(beta[1]) == 1

    def test_empty_registration_cacheable(self) -> None:
        register_scan_results("repo:empty", [], [])
        result = get_cached_findings("repo:empty")
        assert result is not None
        assert result == ([], [])


# ── score_project tool ────────────────────────────────────────────────────────


class TestToolScoreProject:
    def test_returns_dict(self, firm1: FirmFinding) -> None:
        register_scan_results("repo:dict-test", [firm1], [])
        result = tool_score_project(repo_ref="repo:dict-test")
        assert isinstance(result, dict)

    def test_dict_has_expected_keys(self, firm1: FirmFinding) -> None:
        register_scan_results("repo:keys-test", [firm1], [])
        result = tool_score_project(repo_ref="repo:keys-test")
        for key in ("law_scores", "roadmap", "overall_verdict", "attribution", "caveat", "mode"):
            assert key in result, f"Missing key: {key}"

    def test_law_scores_list_length(self, firm1: FirmFinding) -> None:
        register_scan_results("repo:length-test", [firm1], [])
        result = tool_score_project(repo_ref="repo:length-test")
        assert isinstance(result["law_scores"], list)
        assert len(result["law_scores"]) == 7

    def test_uncached_repo_returns_error_dict(self) -> None:
        result = tool_score_project(repo_ref="repo:unknown")
        assert isinstance(result, dict)
        assert "error" in result or "law_scores" in result  # either error or empty scorecard

    def test_codebase_review_mode_default(self, firm1: FirmFinding) -> None:
        register_scan_results("repo:mode-test", [firm1], [])
        result = tool_score_project(repo_ref="repo:mode-test")
        assert result["mode"] == "CODEBASE_REVIEW"

    def test_design_review_mode(self, firm1: FirmFinding) -> None:
        register_scan_results("repo:design-mode", [firm1], [])
        result = tool_score_project(
            repo_ref="repo:design-mode",
            mode="DESIGN_REVIEW",
            revenue_model="SaaS monthly subscription.",
        )
        assert result["mode"] == "DESIGN_REVIEW"

    def test_attribution_in_output(self, firm1: FirmFinding) -> None:
        register_scan_results("repo:attribution", [firm1], [])
        result = tool_score_project(repo_ref="repo:attribution")
        assert "Werner Vogels" in result.get("attribution", "")

    def test_caveat_in_output(self, firm1: FirmFinding) -> None:
        register_scan_results("repo:caveat", [firm1], [])
        result = tool_score_project(repo_ref="repo:caveat")
        assert "static analysis" in result.get("caveat", "").lower()


# ── evaluate_decision tool ─────────────────────────────────────────────────────


class TestToolEvaluateDecision:
    def test_returns_dict(self) -> None:
        result = evaluate_decision(
            decision_text="Should we use DynamoDB provisioned or on-demand?"
        )
        assert isinstance(result, dict)

    def test_dict_has_expected_keys(self) -> None:
        result = evaluate_decision(
            decision_text="Should we use DynamoDB provisioned or on-demand?"
        )
        for key in ("relevant_laws", "recommendation", "attribution", "decision_text"):
            assert key in result, f"Missing key: {key}"

    def test_relevant_laws_one_to_three(self) -> None:
        result = evaluate_decision(
            decision_text="Move from provisioned RDS to Aurora Serverless v2."
        )
        assert 1 <= len(result.get("relevant_laws", [])) <= 3

    def test_attribution_present(self) -> None:
        result = evaluate_decision(decision_text="Use spot instances for batch jobs?")
        assert "Werner Vogels" in result.get("attribution", "")

    def test_decision_text_preserved(self) -> None:
        text = "Use ElastiCache instead of direct DB queries for hot paths?"
        result = evaluate_decision(decision_text=text)
        assert result.get("decision_text") == text

    def test_recommendation_non_empty(self) -> None:
        result = evaluate_decision(
            decision_text="Choose between DynamoDB on-demand and provisioned capacity."
        )
        rec = result.get("recommendation", "")
        assert len(rec) > 20

    def test_with_revenue_model(self) -> None:
        result = evaluate_decision(
            decision_text="Use provisioned DynamoDB.",
            revenue_model="E-commerce platform: $50/transaction fee.",
        )
        assert isinstance(result, dict)
        assert len(result.get("relevant_laws", [])) >= 1
