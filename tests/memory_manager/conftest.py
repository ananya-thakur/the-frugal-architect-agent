"""Shared fixtures and factory helpers for memory-manager tests."""

import pytest

from agent.memory_manager.entities import (
    Finding,
    LawScore,
    RemediationStep,
    Scorecard,
    ScorecardSummary,
)
from agent.memory_manager.manager import MemoryManager
from agent.memory_manager.stub import InMemoryStub


# ── Entity factories ──────────────────────────────────────────────────────────


def make_law_score(
    law_number: int = 1,
    law_title: str = "Make Cost a Requirement",
    verdict: str = "STRONG",
) -> LawScore:
    has_gap = verdict in ("PARTIAL", "GAP")
    return LawScore(
        law_number=law_number,
        law_title=law_title,
        verdict=verdict,
        gap="Missing cost allocation tags" if has_gap else None,
        target_state="All resources tagged with cost centre" if has_gap else None,
        remediation_steps=["Add tags to IaC", "Enforce via AWS Config"] if has_gap else None,
        effort="low" if has_gap else None,
        impact="high" if has_gap else None,
    )


def make_scorecard(session_id: str = "sess-001") -> Scorecard:
    return Scorecard(
        session_id=session_id,
        overall_verdict="PARTIAL — 2 gaps, 5 strong",
        biggest_opportunity="Enable cost allocation tags on all resources",
        law_scores=[
            make_law_score(1, "Make Cost a Non-Functional Requirement", "STRONG"),
            make_law_score(2, "Systems that are Cost Aware", "PARTIAL"),
            make_law_score(3, "Architecture for Cost", "STRONG"),
            make_law_score(4, "Unobserved Systems Lead to Unknown Costs", "GAP"),
            make_law_score(5, "Cost Optimise Iteratively", "STRONG"),
            make_law_score(6, "If in Doubt Leave it Out", "STRONG"),
            make_law_score(7, "Cost is a Continuous Process", "STRONG"),
        ],
        findings=[
            Finding(
                id="f-001",
                law_number=2,
                law_title="Systems that are Cost Aware",
                confidence="FIRM",
                summary="Missing cost allocation tags on DynamoDB table",
                cost_impact_explanation="Prevents attribution of DynamoDB spend to individual features",
                needs_runtime_confirmation=False,
                file_path="infra/main.tf",
                resource_id="aws_dynamodb_table.main",
            ),
            Finding(
                id="f-002",
                law_number=4,
                law_title="Unobserved Systems Lead to Unknown Costs",
                confidence="POSSIBLE",
                summary="N+1 query pattern detected in product listing handler",
                cost_impact_explanation="May cause excessive DynamoDB read units on hot paths",
                needs_runtime_confirmation=True,
                anti_pattern_type="n_plus_one_query",
                file_path="src/products.py",
                line_number=42,
            ),
        ],
        roadmap=[
            RemediationStep(
                rank=1,
                law_number=2,
                action="Add cost allocation tags to all DynamoDB tables in Terraform",
                underlying_principle="Systems that are cost-aware emit cost-attributable signals",
                effort="low",
                impact="high",
            ),
        ],
        caveat=(
            "Findings reflect static analysis of the code as given. "
            "POSSIBLE findings require runtime/cost data to confirm."
        ),
        created_at="2026-10-14T20:00:00+00:00",
    )


def make_summary(
    user_id: str = "user-001",
    project_id: str = "proj-001",
) -> ScorecardSummary:
    return ScorecardSummary(
        user_id=user_id,
        project_id=project_id,
        overall_verdict="PARTIAL — 2 gaps, 5 strong",
        law_verdicts={
            1: "STRONG",
            2: "PARTIAL",
            3: "STRONG",
            4: "GAP",
            5: "STRONG",
            6: "STRONG",
            7: "STRONG",
        },
        top_finding_summaries=[
            "Missing cost allocation tags on DynamoDB table",
            "N+1 query pattern detected in product listing handler",
        ],
        last_review_at="",  # overwritten by store_long_term
    )


# ── pytest fixtures ───────────────────────────────────────────────────────────


@pytest.fixture
def stub() -> InMemoryStub:
    return InMemoryStub()


@pytest.fixture
def manager(stub: InMemoryStub) -> MemoryManager:
    return MemoryManager(api=stub)
