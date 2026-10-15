"""
4-dimension evaluator for the Frugal Architect Agent eval-harness.

Dimensions:
  (a) groundedness        — every finding cites a real law and real evidence
  (b) citation_correctness — cited law matches the expected finding type
  (c) tool_selection      — right scan tool inferred from confidence tags
                            (FIRM → scan_iac; POSSIBLE → scan_app_antipatterns)
  (d) unknown_discipline  — incomplete inputs yield UNKNOWN + probing question,
                            never a fabricated verdict

Each assess_* function takes a raw agent response string and an assertion
dataclass; returns an AssertionResult with pass/fail and the expected vs actual
description used in the EvalReport.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from eval.models import (
    CitationCorrectnessAssertion,
    DimensionResult,
    FailingAssertion,
    GroundednessAssertion,
    TestCase,
    ToolSelectionAssertion,
    UnknownDisciplineAssertion,
)

# ── Response patterns ─────────────────────────────────────────────────────────

# "Law I" through "Law VII" — Roman numerals only (the rubric's canonical form)
_LAW_CITATION: re.Pattern[str] = re.compile(
    r"\bLaw\s+(VII|VI|VIII?|IV|V|I{1,3})\b", re.IGNORECASE
)
# Any path-like or dot-separated identifier as a proxy for file evidence
_FILE_EVIDENCE: re.Pattern[str] = re.compile(r"[a-zA-Z0-9_\-]+[./][a-zA-Z0-9_\-./]+")

_FIRM: re.Pattern[str] = re.compile(r"\bFIRM\b")
_POSSIBLE: re.Pattern[str] = re.compile(r"\bPOSSIBLE\b")
_UNKNOWN: re.Pattern[str] = re.compile(r"\bUNKNOWN\b")
_PROBING_Q: re.Pattern[str] = re.compile(r"\?")
_DEFINITIVE_VERDICT: re.Pattern[str] = re.compile(r"\b(STRONG|PARTIAL|GAP)\b")

# Canonical valid Roman-numeral law IDs
_VALID_LAW_IDS: frozenset[str] = frozenset({"I", "II", "III", "IV", "V", "VI", "VII"})


# ── Per-assertion result ──────────────────────────────────────────────────────


@dataclass
class AssertionResult:
    """Single assertion outcome with human-readable expected/actual strings."""

    passed: bool
    expected: str
    actual: str


@dataclass
class TestCaseResult:
    """Per-case evaluation results across all configured dimensions."""

    test_id: str
    groundedness: AssertionResult | None = None
    citation_correctness: AssertionResult | None = None
    tool_selection: AssertionResult | None = None
    unknown_discipline: AssertionResult | None = None


# ── Dimension assessors ───────────────────────────────────────────────────────


def assess_groundedness(
    response: str, assertion: GroundednessAssertion
) -> AssertionResult:
    """
    Check that the response contains at least one law citation with evidence.

    A grounded response has at least one "Law X" reference (X ∈ I–VII) AND at
    least one piece of evidence: FIRM/POSSIBLE confidence tag or a file-path-
    like string.
    """
    has_law = bool(_LAW_CITATION.search(response))
    has_evidence = bool(
        _FIRM.search(response) or _POSSIBLE.search(response) or _FILE_EVIDENCE.search(response)
    )

    if assertion.expects_law_citation and assertion.expects_evidence:
        passed = has_law and has_evidence
        expected = "law_citation=True and evidence=True"
        actual = f"law_citation={has_law} and evidence={has_evidence}"
    elif assertion.expects_law_citation:
        passed = has_law
        expected = "law_citation=True"
        actual = f"law_citation={has_law}"
    else:
        passed = has_evidence
        expected = "evidence=True"
        actual = f"evidence={has_evidence}"

    return AssertionResult(passed=passed, expected=expected, actual=actual)


def assess_citation_correctness(
    response: str, assertion: CitationCorrectnessAssertion
) -> AssertionResult:
    """
    Check that the expected law ID is cited and that no fabricated law IDs appear.

    A fabricated law ID is any Roman numeral not in {I, II, III, IV, V, VI, VII}.
    """
    law_id = assertion.expected_law_id.upper()
    target_pattern = re.compile(rf"\bLaw\s+{re.escape(law_id)}\b", re.IGNORECASE)
    found = bool(target_pattern.search(response))

    cited_ids = {m.group(1).upper() for m in _LAW_CITATION.finditer(response)}
    fabricated = cited_ids - _VALID_LAW_IDS

    passed = found and not fabricated
    expected = f"Law {law_id} cited; no fabricated law IDs"
    actual = (
        f"Law {law_id} found={found}; cited={sorted(cited_ids)}; fabricated={sorted(fabricated)}"
    )
    return AssertionResult(passed=passed, expected=expected, actual=actual)


def assess_tool_selection(
    response: str, assertion: ToolSelectionAssertion
) -> AssertionResult:
    """
    Infer tool selection from confidence tags in the response.

    FIRM   → scan_iac was exercised (IaC exhaustive scan)
    POSSIBLE → scan_app_antipatterns was exercised (targeted app-code scan)
    """
    expected_conf = assertion.expected_confidence.upper()

    if expected_conf == "FIRM":
        passed = bool(_FIRM.search(response))
        actual = f"FIRM_present={passed}"
    elif expected_conf == "POSSIBLE":
        passed = bool(_POSSIBLE.search(response))
        actual = f"POSSIBLE_present={passed}"
    else:
        passed = False
        actual = f"unrecognised_confidence={expected_conf!r}"

    return AssertionResult(
        passed=passed,
        expected=f"confidence={expected_conf}",
        actual=actual,
    )


def assess_unknown_discipline(
    response: str, assertion: UnknownDisciplineAssertion
) -> AssertionResult:
    """
    Check that incomplete inputs yield UNKNOWN + a probing question, not a fabricated score.

    A well-disciplined response on incomplete input:
      - Contains "UNKNOWN"
      - Contains at least one probing question (sentence ending with "?")
      - Does NOT contain a definitive verdict (STRONG / PARTIAL / GAP) without
        co-located UNKNOWN context — a simple heuristic: if UNKNOWN is absent
        entirely, any STRONG/PARTIAL/GAP is treated as fabricated.
    """
    has_unknown = bool(_UNKNOWN.search(response))
    has_question = bool(_PROBING_Q.search(response))

    # Fabrication heuristic: definitive verdicts without any UNKNOWN in the response
    has_fabricated = not has_unknown and bool(_DEFINITIVE_VERDICT.search(response))

    passed = has_unknown and has_question and not has_fabricated
    expected = "UNKNOWN=True, probing_question=True, fabricated_verdict=False"
    actual = (
        f"UNKNOWN={has_unknown}, probing_question={has_question}, "
        f"fabricated_verdict={has_fabricated}"
    )
    return AssertionResult(passed=passed, expected=expected, actual=actual)


# ── Per-case and aggregate evaluation ────────────────────────────────────────


def evaluate_test_case(test_case: TestCase, agent_response: str) -> TestCaseResult:
    """
    Evaluate a single test case against the agent's text response.

    Only the configured assertions are evaluated; unconfigured dimensions are
    left as None in the result (not counted in dimension tallies).
    """
    result = TestCaseResult(test_id=test_case.test_id)
    a = test_case.assertions

    if a.groundedness is not None:
        result.groundedness = assess_groundedness(agent_response, a.groundedness)

    if a.citation_correctness is not None:
        result.citation_correctness = assess_citation_correctness(
            agent_response, a.citation_correctness
        )

    if a.tool_selection is not None:
        result.tool_selection = assess_tool_selection(agent_response, a.tool_selection)

    if a.unknown_discipline is not None:
        result.unknown_discipline = assess_unknown_discipline(
            agent_response, a.unknown_discipline
        )

    return result


def aggregate_results(case_results: list[TestCaseResult]) -> dict[str, DimensionResult]:
    """
    Aggregate per-case TestCaseResults into one DimensionResult per dimension.

    Only cases with a non-None assertion for a dimension contribute to that
    dimension's counts (avoids penalising dimensions not under test).
    """
    buckets: dict[str, dict[str, object]] = {
        dim: {"pass": 0, "fail": 0, "ids": [], "assertions": []}
        for dim in ("groundedness", "citation_correctness", "tool_selection", "unknown_discipline")
    }

    dim_attrs = [
        ("groundedness", "groundedness"),
        ("citation_correctness", "citation_correctness"),
        ("tool_selection", "tool_selection"),
        ("unknown_discipline", "unknown_discipline"),
    ]

    for r in case_results:
        for dim_name, attr_name in dim_attrs:
            attr: AssertionResult | None = getattr(r, attr_name)
            if attr is None:
                continue
            b = buckets[dim_name]
            if attr.passed:
                b["pass"] = int(b["pass"]) + 1  # type: ignore[arg-type]
            else:
                b["fail"] = int(b["fail"]) + 1  # type: ignore[arg-type]
                ids: list[str] = b["ids"]  # type: ignore[assignment]
                ids.append(r.test_id)
                assertions_list: list[FailingAssertion] = b["assertions"]  # type: ignore[assignment]
                assertions_list.append(
                    FailingAssertion(
                        test_id=r.test_id,
                        dimension=dim_name,
                        expected=attr.expected,
                        actual=attr.actual,
                    )
                )

    results: dict[str, DimensionResult] = {}
    for dim_name, b in buckets.items():
        p = int(b["pass"])  # type: ignore[arg-type]
        f = int(b["fail"])  # type: ignore[arg-type]
        total = p + f
        rate = p / total if total > 0 else 0.0
        results[dim_name] = DimensionResult(
            dimension=dim_name,
            pass_count=p,
            fail_count=f,
            pass_rate=rate,
            failing_case_ids=list(b["ids"]),  # type: ignore[arg-type]
            failing_assertions=list(b["assertions"]),  # type: ignore[arg-type]
        )

    return results
