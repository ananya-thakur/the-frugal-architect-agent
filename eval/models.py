"""
Data models for the eval-harness test set and evaluation results.

Test case models describe the input and assertions for each labeled case.
Eval result models (FailingAssertion, DimensionResult, EvalReport) match the
closed field set defined in security-design-eval-harness SR-EH-005 — they
intentionally omit fixture content, scenario text, and raw agent responses.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ── Test case models ──────────────────────────────────────────────────────────


@dataclass
class GroundednessAssertion:
    """Assert that every finding cites a real law and provides evidence."""

    expects_law_citation: bool = True
    expects_evidence: bool = True


@dataclass
class CitationCorrectnessAssertion:
    """Assert that the cited law ID matches the finding type."""

    expected_law_id: str  # Roman numeral: "I" through "VII"
    expected_law_title_fragment: str = ""  # Optional partial-match on law title


@dataclass
class ToolSelectionAssertion:
    """Assert that the correct scan tool was indicated by confidence tags."""

    # FIRM  → scan_iac was used (IaC source; exhaustive)
    # POSSIBLE → scan_app_antipatterns was used (app-code source; targeted)
    expected_confidence: str


@dataclass
class UnknownDisciplineAssertion:
    """Assert that a missing/incomplete input yields UNKNOWN + probing question."""

    input_is_incomplete: bool = True  # If True, response must contain UNKNOWN + "?"


@dataclass
class TestCaseAssertions:
    """Container for the per-dimension assertions of one test case."""

    groundedness: GroundednessAssertion | None = None
    citation_correctness: CitationCorrectnessAssertion | None = None
    tool_selection: ToolSelectionAssertion | None = None
    unknown_discipline: UnknownDisciplineAssertion | None = None


@dataclass
class TestCase:
    """A labeled test case for the eval harness."""

    test_id: str
    description: str
    input_text: str
    fixture_repo: str | None = None  # Relative path to synthetic fixture repo
    assertions: TestCaseAssertions = field(default_factory=TestCaseAssertions)


# ── Eval result models (SR-EH-005 closed field set) ──────────────────────────


@dataclass
class FailingAssertion:
    """Minimal record of a failing assertion — no fixture/agent content."""

    test_id: str
    dimension: str
    expected: str
    actual: str


@dataclass
class DimensionResult:
    """Aggregated pass/fail counts for one evaluation dimension."""

    dimension: str
    pass_count: int
    fail_count: int
    pass_rate: float
    failing_case_ids: list[str] = field(default_factory=list)
    failing_assertions: list[FailingAssertion] = field(default_factory=list)


@dataclass
class EvalReport:
    """Top-level eval report — sanitized for safe storage and export."""

    run_id: str
    timestamp: str          # ISO 8601
    total_cases: int
    overall_verdict: str    # PASS | FAIL
    dimensions: list[DimensionResult]
