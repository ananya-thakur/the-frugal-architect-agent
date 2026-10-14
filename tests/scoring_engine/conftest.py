"""Shared test fixtures for the scoring-engine test suite."""

from __future__ import annotations

import pytest

from agent.app_scanner.models import PossibleFinding
from agent.iac_scanner.models import Confidence, FirmFinding
from agent.rubric_core.engine import _reset_cache, load_rubric
from agent.rubric_core.models import Rubric
from agent.scoring_engine.models import BusinessContext


@pytest.fixture(autouse=True)
def reset_rubric_cache() -> None:
    """Reset rubric-core module cache between tests for isolation."""
    _reset_cache()
    yield
    _reset_cache()


@pytest.fixture
def rubric() -> Rubric:
    """Load the real rubric.yaml for integration-style tests."""
    return load_rubric()


@pytest.fixture
def firm_finding_law1() -> FirmFinding:
    """A single FIRM finding for Law 1 (Lambda over-provisioned)."""
    return FirmFinding(
        law_id="law-1",
        law_title="Make Cost a Non-Functional Requirement",
        file_path="infra/lambda_stack.py",
        resource_id="MyFunction",
        resource_type="aws_lambda_function",
        line=42,
        gap="Lambda function provisioned with more than 1024 MB — verify justified by profiling data.",
        evidence="MyFunction in infra/lambda_stack.py sets memory_size=4096 MB.",
        signal_id="tf-lambda-over-memory",
    )


@pytest.fixture
def firm_finding_law3() -> FirmFinding:
    """A single FIRM finding for Law 3."""
    return FirmFinding(
        law_id="law-3",
        law_title="Architecture Is Proportional to Business Criticality",
        file_path="infra/database.tf",
        resource_id="MainDB",
        resource_type="aws_db_instance",
        line=10,
        gap="Database instance uses largest instance class without business-criticality justification.",
        evidence="MainDB in infra/database.tf sets instance_class=db.r6g.16xlarge.",
        signal_id="tf-rds-over-instance",
    )


@pytest.fixture
def possible_finding_law4() -> PossibleFinding:
    """A POSSIBLE finding for Law 4 (N+1 query anti-pattern)."""
    return PossibleFinding(
        finding_id="test-finding-law4-001",
        law_id=4,
        law_title="Unobserved Systems Lead to Unknown Costs",
        anti_pattern_type="n_plus_one_query",
        file_path="src/users/repository.py",
        line_number=88,
        evidence="for user in users:\n    db.query(f'SELECT * FROM orders WHERE user_id={user.id}')",
        confidence="POSSIBLE",
        runtime_caveat=(
            "This finding is a static analysis lead. Confirming it requires runtime/cost data."
        ),
        cost_impact_explanation=(
            "N+1 query pattern detected: a separate DB query per item in a loop. "
            "This multiplies DB cost proportional to dataset size."
        ),
    )


@pytest.fixture
def biz_with_revenue() -> BusinessContext:
    """BusinessContext with a revenue_model (satisfies Law II requirement)."""
    return BusinessContext(
        revenue_model="SaaS subscription: $99/month per seat. 10,000 active users.",
        cost_allocation="Per-feature cost tagging via AWS Cost Allocation Tags.",
        business_criticality="High — payment processing for enterprise customers.",
    )


@pytest.fixture
def biz_no_revenue() -> BusinessContext:
    """BusinessContext without revenue_model (triggers Law II UNKNOWN)."""
    return BusinessContext(
        cost_allocation="Per-feature cost tagging.",
    )
