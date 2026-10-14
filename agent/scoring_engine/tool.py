"""score_project Strands tool — entry point for the Frugal Architecture scorecard.

This module registers score_project as a Strands tool and manages the
module-level findings cache that agent-assembly populates after running
scan_iac and scan_app_antipatterns.

Strands decorator is optional at import time so unit tests can exercise
the business logic without the full Strands SDK installed.
"""

from __future__ import annotations

import logging
from typing import Any

from agent.app_scanner.models import PossibleFinding
from agent.iac_scanner.models import FirmFinding
from agent.rubric_core import load_rubric
from agent.scoring_engine.engine import decision_check as _engine_decision_check
from agent.scoring_engine.engine import score_project as _engine_score_project
from agent.scoring_engine.models import (
    BusinessContext,
    Scorecard,
    ScoringMode,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Strands SDK import — graceful fallback so unit tests run without the SDK
# ---------------------------------------------------------------------------
try:
    from strands import tool as _strands_tool  # type: ignore[import]

    def _tool_decorator(fn: Any) -> Any:  # type: ignore[return]
        return _strands_tool(fn)

except ImportError:
    def _tool_decorator(fn: Any) -> Any:  # type: ignore[return]
        return fn

# ---------------------------------------------------------------------------
# OTEL tracer — optional; no-ops when OTEL SDK is not initialised
# ---------------------------------------------------------------------------
try:
    from opentelemetry import trace as _otel_trace  # type: ignore[import]
    _tracer = _otel_trace.get_tracer(__name__)
except ImportError:
    _tracer = None  # type: ignore[assignment]


def _current_span() -> Any:
    """Return the active OTEL span, or a no-op sentinel when OTEL is absent."""
    if _tracer is None:
        return _NoOpSpan()
    try:
        from opentelemetry import trace as _t  # type: ignore[import]
        return _t.get_current_span()
    except Exception:
        return _NoOpSpan()


class _NoOpSpan:
    """Minimal no-op span for environments without OTEL."""

    def set_attribute(self, *_: Any, **__: Any) -> None:
        pass


# ---------------------------------------------------------------------------
# Module-level findings cache
# ---------------------------------------------------------------------------
# Populated by agent-assembly after scan_iac and scan_app_antipatterns run.
# Key: repo_ref string. Value: (firm_findings, possible_findings) tuple.
_findings_cache: dict[str, tuple[list[FirmFinding], list[PossibleFinding]]] = {}


def register_scan_results(
    repo_ref: str,
    firm: list[FirmFinding],
    possible: list[PossibleFinding],
) -> None:
    """Cache scan results for a repo so score_project can look them up.

    Called by agent-assembly after scan_iac and scan_app_antipatterns complete.
    """
    _findings_cache[repo_ref] = (firm, possible)
    logger.debug(
        "Cached scan results for %s: %d firm, %d possible",
        repo_ref, len(firm), len(possible),
    )


def get_cached_findings(
    repo_ref: str,
) -> tuple[list[FirmFinding], list[PossibleFinding]] | None:
    """Retrieve cached scan results by repo_ref. Returns None if not found."""
    return _findings_cache.get(repo_ref)


# ---------------------------------------------------------------------------
# Internal implementation (testable without Strands)
# ---------------------------------------------------------------------------

def _score_project_impl(
    repo_ref: str,
    mode: str = "CODEBASE_REVIEW",
    revenue_model: str | None = None,
    design_description: str | None = None,
    law_answers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Core score_project logic, separated for testability."""
    span = _current_span()
    span.set_attribute("scoring.repo_ref", repo_ref)
    span.set_attribute("scoring.mode", mode)

    # Resolve ScoringMode
    try:
        scoring_mode = ScoringMode(mode)
    except ValueError:
        valid = ", ".join(m.value for m in ScoringMode)
        return {
            "status": "error",
            "message": f"Invalid mode '{mode}'. Valid modes: {valid}",
        }

    # Build business context
    biz: BusinessContext | None = None
    if any([revenue_model, design_description, law_answers]):
        biz = BusinessContext(
            revenue_model=revenue_model,
            design_description=design_description,
            law_answers=law_answers or {},
        )

    # Look up cached scan results
    cached = _findings_cache.get(repo_ref, ([], []))
    firm, possible = cached

    span.set_attribute("scoring.firm_count",     len(firm))
    span.set_attribute("scoring.possible_count", len(possible))

    # Load rubric
    rubric = load_rubric()

    # Run scoring engine
    scorecard: Scorecard = _engine_score_project(
        firm=firm,
        possible=possible,
        rubric=rubric,
        biz=biz,
        mode=scoring_mode,
    )

    span.set_attribute("scoring.overall_verdict", scorecard.overall_verdict)
    span.set_attribute("scoring.laws_scored", len(scorecard.law_scores))

    return {
        "status": "ok",
        "mode": scorecard.mode.value,
        "overall_verdict": scorecard.overall_verdict,
        "biggest_opportunity": scorecard.biggest_opportunity,
        "attribution": scorecard.attribution,
        "caveat": scorecard.caveat,
        "scan_metadata": scorecard.scan_metadata.model_dump(),
        "law_scores": [
            {
                "law_number":     ls.law_number,
                "law_id":         ls.law_id,
                "law_title":      ls.law_title,
                "verdict":        ls.verdict.value,
                "evidence_count": len(ls.evidence),
                "gap":            ls.gap,
                "target_state":   ls.target_state,
                "probing_question": ls.probing_question,
                "effort":         ls.effort.value if ls.effort else None,
                "impact":         ls.impact.value if ls.impact else None,
                "remediation_steps": [
                    {
                        "rank":             s.priority_rank,
                        "description":      s.description,
                        "aws_actions":      s.aws_actions,
                        "portable_principle": s.portable_principle,
                        "effort":           s.effort.value,
                        "impact":           s.impact.value,
                        "source_confidence": s.source_confidence.value,
                        "runtime_note":     s.runtime_confirmation_note,
                    }
                    for s in (ls.remediation_steps or [])
                ],
            }
            for ls in scorecard.law_scores
        ],
        "roadmap": [
            {
                "rank":               s.priority_rank,
                "law_id":             s.law_id,
                "description":        s.description,
                "aws_actions":        s.aws_actions,
                "portable_principle": s.portable_principle,
                "effort":             s.effort.value,
                "impact":             s.impact.value,
                "source_confidence":  s.source_confidence.value,
                "runtime_note":       s.runtime_confirmation_note,
            }
            for s in scorecard.roadmap.steps
        ],
    }


def _decision_check_impl(
    decision_text: str,
    revenue_model: str | None = None,
) -> dict[str, Any]:
    """Core decision_check logic, separated for testability."""
    span = _current_span()
    span.set_attribute("scoring.mode", "DECISION_CHECK")

    rubric = load_rubric()
    biz: BusinessContext | None = None
    if revenue_model:
        biz = BusinessContext(revenue_model=revenue_model)

    result = _engine_decision_check(
        decision_text=decision_text,
        rubric=rubric,
        biz=biz,
    )

    return {
        "status": "ok",
        "mode": "DECISION_CHECK",
        "decision_text": result.decision_text,
        "attribution": result.attribution,
        "recommendation": result.recommendation,
        "relevant_laws": [
            {
                "law_number":  ls.law_number,
                "law_title":   ls.law_title,
                "verdict":     ls.verdict.value,
                "gap":         ls.gap,
                "probing_question": ls.probing_question,
            }
            for ls in result.relevant_laws
        ],
    }


# ---------------------------------------------------------------------------
# Strands tool registration
# ---------------------------------------------------------------------------


@_tool_decorator
def score_project(
    repo_ref: str,
    mode: str = "CODEBASE_REVIEW",
    revenue_model: str | None = None,
    design_description: str | None = None,
    law_answers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Score a repository against the 7 Laws of Frugal Architecture.

    Returns a Frugal Architecture Scorecard covering all 7 laws with
    per-law verdicts (STRONG | PARTIAL | GAP | UNKNOWN), evidence,
    gap analysis, and a prioritized remediation roadmap.

    The 7 Laws of Frugal Architecture are the intellectual property of
    Werner Vogels / The Frugal Architect (https://thefrugalarchitect.com/laws/).
    This tool produces advisory analysis only — it never modifies code or
    calls AWS account/cost APIs.

    Args:
        repo_ref:           Repository reference (same key used with clone_or_read_repo
                            and the scan tools).
        mode:               Scoring mode — CODEBASE_REVIEW (default), DESIGN_REVIEW,
                            or DECISION_CHECK.
        revenue_model:      How the system generates revenue (required for Law II).
        design_description: Free-text architecture description for DESIGN REVIEW mode.
        law_answers:        Per-law answers to probing questions, keyed by law_id
                            (e.g. {"law-2": "We charge per API call..."}).

    Returns:
        dict with overall_verdict, law_scores (7 entries), roadmap, attribution,
        caveat, and scan_metadata.
    """
    return _score_project_impl(
        repo_ref=repo_ref,
        mode=mode,
        revenue_model=revenue_model,
        design_description=design_description,
        law_answers=law_answers,
    )


@_tool_decorator
def evaluate_decision(
    decision_text: str,
    revenue_model: str | None = None,
) -> dict[str, Any]:
    """Evaluate a specific architecture decision against the most relevant Frugal Architecture laws.

    Returns a targeted assessment (1–3 laws) rather than a full 7-law scorecard.
    Use this for DECISION CHECK mode when the user asks about a specific choice.

    The 7 Laws of Frugal Architecture are the intellectual property of
    Werner Vogels / The Frugal Architect (https://thefrugalarchitect.com/laws/).

    Args:
        decision_text:  Description of the architecture decision to evaluate.
        revenue_model:  Business revenue context (helps Law II scoring).

    Returns:
        dict with relevant_laws (1–3), recommendation, and attribution.
    """
    return _decision_check_impl(
        decision_text=decision_text,
        revenue_model=revenue_model,
    )
