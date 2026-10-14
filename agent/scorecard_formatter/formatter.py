"""Markdown and JSON rendering for Frugal Architecture scorecards.

Pure data transformation — no I/O, no side effects, no external calls.

Business rules enforced: R-01 through R-16 (business-rules-scorecard-formatter).
Security controls: pure-function library with no network or filesystem access.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from agent.scoring_engine.models import (
    ConfidenceTag,
    LawScore,
    RemediationRoadmap,
    RemediationStep,
    Scorecard,
    Verdict,
)

if TYPE_CHECKING:
    from agent.rubric_core.models import Rubric

# Default number of roadmap items to render (configurable via top_n param).
_DEFAULT_TOP_N: int = 5

_ATTRIBUTION_HEADER = (
    "> **Attribution:** The 7 Laws of Frugal Architecture are authored by "
    "Werner Vogels / The Frugal Architect "
    "(<https://thefrugalarchitect.com/laws/>). "
    "This scorecard is an advisory analysis grounded in those laws."
)

_CAVEAT_TEXT = (
    "> **Advisory notice:** Findings reflect static analysis of the code as given, "
    "not a verified runtime/cost audit. "
    "IaC/config findings (FIRM) are based on declared infrastructure. "
    "App-code findings (POSSIBLE) are investigation leads that may need "
    "runtime or cost data to confirm."
)

_VERDICT_LABELS: dict[Verdict, str] = {
    Verdict.GAP:     "❌ GAP",
    Verdict.PARTIAL: "⚠️ PARTIAL",
    Verdict.STRONG:  "✅ STRONG",
    Verdict.UNKNOWN: "❓ UNKNOWN",
}


class ScorecardFormatterError(ValueError):
    """Raised when a Scorecard fails required-field validation before rendering."""


# ── Validation ────────────────────────────────────────────────────────────────


def _validate(scorecard: Scorecard) -> None:
    """Validate required fields; raise ScorecardFormatterError on violation (R-09)."""
    if not scorecard.attribution:
        raise ScorecardFormatterError("Scorecard.attribution must not be empty (R-07)")
    if not scorecard.caveat:
        raise ScorecardFormatterError("Scorecard.caveat must not be empty (R-08)")
    if len(scorecard.law_scores) != 7:
        raise ScorecardFormatterError(
            f"Scorecard must have exactly 7 LawScores, got {len(scorecard.law_scores)} (R-09)"
        )
    for ls in scorecard.law_scores:
        if ls.verdict is None:
            raise ScorecardFormatterError(
                f"LawScore({ls.law_id}) is missing a verdict (R-09)"
            )
        if ls.verdict in (Verdict.PARTIAL, Verdict.GAP):
            missing = [
                f for f in ("gap", "target_state", "remediation_steps", "effort", "impact")
                if getattr(ls, f) is None
            ]
            if missing:
                raise ScorecardFormatterError(
                    f"LawScore({ls.law_id}) verdict={ls.verdict} missing: {missing} (R-10)"
                )
        if ls.verdict == Verdict.UNKNOWN and not ls.probing_question:
            raise ScorecardFormatterError(
                f"LawScore({ls.law_id}) UNKNOWN verdict requires probing_question (R-09)"
            )


# ── Confidence helpers (R-15, R-16) ──────────────────────────────────────────


def _firm_evidence(law_score: LawScore) -> list:
    return [e for e in law_score.evidence if e.source_type == ConfidenceTag.FIRM]


def _possible_evidence(law_score: LawScore) -> list:
    return [
        e for e in law_score.evidence
        if e.source_type in (ConfidenceTag.POSSIBLE, ConfidenceTag.DESCRIBED)
    ]


def _aggregate_confidence_label(law_score: LawScore) -> str:
    """Return aggregate confidence tag for the law verdict line (R-16)."""
    if _firm_evidence(law_score):
        return "FIRM"
    if _possible_evidence(law_score):
        return "POSSIBLE"
    return "UNKNOWN"


# ── Law block ─────────────────────────────────────────────────────────────────


def format_law_block(score: LawScore) -> str:
    """Render one law's findings and remediation as a Markdown section.

    Business rules: R-01 (FIRM before POSSIBLE), R-02 (POSSIBLE framing),
    R-03 (UNKNOWN early return), R-10, R-15, R-16.
    """
    parts: list[str] = []

    verdict_label = _VERDICT_LABELS.get(score.verdict, score.verdict.value)
    agg_confidence = _aggregate_confidence_label(score)

    parts.append(f"## Law {score.law_number}: {score.law_title}\n")
    parts.append(
        f"**Verdict:** {verdict_label}  |  **Confidence:** {agg_confidence}\n"
    )

    # R-03: UNKNOWN renders only heading, verdict badge, and probing question.
    if score.verdict == Verdict.UNKNOWN:
        parts.append(
            f"\n**Before this law can be scored, more information is needed:**\n\n"
            f"> {score.probing_question}\n"
        )
        return "\n".join(parts)

    # Gap and target state (PARTIAL or GAP only).
    if score.verdict in (Verdict.PARTIAL, Verdict.GAP) and score.gap:
        parts.append(f"\n**Gap:** {score.gap}\n")
    if score.verdict in (Verdict.PARTIAL, Verdict.GAP) and score.target_state:
        parts.append(f"**Target state:** {score.target_state}\n")

    # R-01: FIRM block unconditionally rendered before POSSIBLE block.
    firm = _firm_evidence(score)
    parts.append("\n**IaC/Config Evidence (FIRM)**\n")
    if firm:
        for ev in firm:
            parts.append(f"- 🔒 **FIRM** — {ev.description}\n  `{ev.citation}`\n")
    else:
        parts.append("_No IaC/config evidence found for this law._\n")

    # R-02: POSSIBLE findings framed as investigation leads.
    possible = _possible_evidence(score)
    if possible:
        parts.append("\n**App-Code Signals (POSSIBLE — investigation leads)**\n")
        for ev in possible:
            parts.append(
                f"- ⚠️ **POSSIBLE** — {ev.description}\n"
                f"  `{ev.citation}`\n"
                f"  _Runtime/cost data needed to confirm. "
                f"This is an investigation lead, not a verdict._\n"
            )

    # Remediation actions (PARTIAL or GAP only).
    if score.verdict in (Verdict.PARTIAL, Verdict.GAP) and score.remediation_steps:
        parts.append("\n**Recommended Actions**\n")
        for i, step in enumerate(score.remediation_steps, 1):
            parts.append(f"{i}. {step.description}\n")
            if step.aws_actions:
                for action in step.aws_actions:
                    parts.append(f"   - {action}\n")
            if step.portable_principle:
                parts.append(f"   _Principle: {step.portable_principle}_\n")
        if score.effort and score.impact:
            parts.append(
                f"\n**Effort:** {score.effort.value}  |  **Impact:** {score.impact.value}\n"
            )

    return "\n".join(parts)


# ── Roadmap ───────────────────────────────────────────────────────────────────


def format_roadmap(roadmap: RemediationRoadmap, top_n: int = _DEFAULT_TOP_N) -> str:
    """Render the prioritized remediation roadmap section.

    Business rules: R-05 (overflow disclosure), R-06 (ordering preserved).
    """
    parts: list[str] = []
    parts.append("## Prioritized Remediation Roadmap\n")
    parts.append(
        "_Ordered: highest frugality impact first, smallest viable step first._\n"
    )

    total = len(roadmap.steps)
    displayed = min(top_n, total)
    items = roadmap.steps[:displayed]

    if not items:
        parts.append("\n_No remediation steps required — project scores well across all 7 laws._\n")
        return "\n".join(parts)

    for i, step in enumerate(items, 1):
        parts.append(f"\n### {i}. {step.law_id} — {step.description}\n")
        if step.aws_actions:
            parts.append("**Steps:**\n")
            for j, action in enumerate(step.aws_actions, 1):
                parts.append(f"{j}. {action}\n")
        parts.append(
            f"\n**Effort:** {step.effort.value}  |  **Impact:** {step.impact.value}\n"
        )
        if step.portable_principle:
            parts.append(f"**Principle:** {step.portable_principle}\n")
        if step.runtime_confirmation_note:
            parts.append(
                f"\n> ⚠️ {step.runtime_confirmation_note}\n"
            )

    # R-05: disclose overflow — never silently truncate.
    remaining = total - displayed
    if remaining > 0:
        parts.append(
            f"\n_{remaining} additional recommendation{'s' if remaining > 1 else ''} available. "
            f"Ask 'show full roadmap' for details._\n"
        )

    return "\n".join(parts)


# ── Overall verdict line ──────────────────────────────────────────────────────


def _overall_verdict_line(scorecard: Scorecard) -> str:
    """Compose the one-line overall verdict summary."""
    verdict_str = scorecard.overall_verdict
    biggest = scorecard.biggest_opportunity or "No gaps found — maintain current practices."
    return f"**Overall:** {verdict_str}\n\n**Biggest opportunity:** {biggest}\n"


# ── Public entry points ───────────────────────────────────────────────────────


def format_markdown(
    scorecard: Scorecard,
    rubric: "Rubric | None" = None,
    top_n: int = _DEFAULT_TOP_N,
) -> str:
    """Render a Frugal Architecture Scorecard as a Markdown document.

    Steps per business-logic-model §Markdown Rendering Pipeline:
    1. Validate required fields.
    2. Attribution header (R-07) — first visible element.
    3. Overall verdict line.
    4. Per-law blocks in canonical order Law I–VII (R-04).
    5. Prioritized remediation roadmap.
    6. Advisory caveat (R-08) — final block.

    Args:
        scorecard: Scorecard aggregate produced by scoring-engine.
        rubric: Optional; not required because law titles and target states
                are already embedded in the Scorecard by scoring-engine.
        top_n: Maximum roadmap items to render; overflow disclosed (R-05).

    Returns:
        str — complete Markdown document.

    Raises:
        ScorecardFormatterError: if scorecard is missing required fields.
    """
    _validate(scorecard)

    sections: list[str] = []

    # Step 2: Attribution (R-07).
    sections.append(_ATTRIBUTION_HEADER)
    sections.append("")

    # Step 3: Overall verdict.
    sections.append(_overall_verdict_line(scorecard))

    # Step 4: Per-law blocks — canonical order by law_number (R-04).
    ordered = sorted(scorecard.law_scores, key=lambda ls: ls.law_number)
    for law_score in ordered:
        sections.append("---")
        sections.append("")
        sections.append(format_law_block(law_score))

    # Step 5: Roadmap.
    sections.append("---")
    sections.append("")
    sections.append(format_roadmap(scorecard.roadmap, top_n=top_n))

    # Step 6: Advisory caveat (R-08) — always last.
    sections.append("---")
    sections.append("")
    sections.append(_CAVEAT_TEXT)
    sections.append("")

    return "\n".join(sections)


def format_json(
    scorecard: Scorecard,
    top_n: int = _DEFAULT_TOP_N,
) -> str:
    """Render a Frugal Architecture Scorecard as a JSON document.

    Business rules: R-04, R-07, R-08, R-11, R-12, R-13, R-14.

    Args:
        scorecard: Scorecard aggregate produced by scoring-engine.
        top_n: Maximum roadmap items to include; overflow count disclosed (R-05).

    Returns:
        str — indented JSON document.

    Raises:
        ScorecardFormatterError: if scorecard is missing required fields.
    """
    _validate(scorecard)

    # R-04: canonical law order.
    ordered_laws = sorted(scorecard.law_scores, key=lambda ls: ls.law_number)

    law_scores_list = []
    for ls in ordered_laws:
        firm = _firm_evidence(ls)
        possible = _possible_evidence(ls)

        law_entry: dict = {
            "law_id": ls.law_id,
            "law_number": ls.law_number,
            "law_title": ls.law_title,
            "verdict": ls.verdict.value,
            "aggregate_confidence": _aggregate_confidence_label(ls),
            "firm_findings": [
                {
                    "confidence": "FIRM",
                    "description": ev.description,
                    "citation": ev.citation,
                    "law_id": ev.law_id,
                }
                for ev in firm
            ],
            "possible_findings": [
                {
                    "confidence": "POSSIBLE",
                    "description": ev.description,
                    "citation": ev.citation,
                    "law_id": ev.law_id,
                    # R-11: hardcoded on every POSSIBLE finding.
                    "requires_runtime_confirmation": True,
                }
                for ev in possible
            ],
        }

        if ls.verdict == Verdict.UNKNOWN:
            law_entry["probing_question"] = ls.probing_question
        elif ls.verdict in (Verdict.PARTIAL, Verdict.GAP):
            law_entry["gap"] = ls.gap
            law_entry["target_state"] = ls.target_state
            law_entry["remediation_steps"] = [
                {
                    "description": step.description,
                    "aws_actions": step.aws_actions,
                    "portable_principle": step.portable_principle,
                    "effort": step.effort.value,
                    "impact": step.impact.value,
                    "source_confidence": step.source_confidence.value,
                    "priority_rank": step.priority_rank,
                    "runtime_confirmation_note": step.runtime_confirmation_note,
                }
                for step in (ls.remediation_steps or [])
            ]
            law_entry["effort"] = ls.effort.value if ls.effort else None
            law_entry["impact"] = ls.impact.value if ls.impact else None

        law_scores_list.append(law_entry)

    # Roadmap — R-05 overflow disclosure.
    total = len(scorecard.roadmap.steps)
    displayed = min(top_n, total)
    roadmap_items = [
        {
            "law_id": step.law_id,
            "description": step.description,
            "aws_actions": step.aws_actions,
            "portable_principle": step.portable_principle,
            "effort": step.effort.value,
            "impact": step.impact.value,
            "source_confidence": step.source_confidence.value,
            "priority_rank": step.priority_rank,
            "runtime_confirmation_note": step.runtime_confirmation_note,
        }
        for step in scorecard.roadmap.steps[:displayed]
    ]

    # R-14: scan_metadata block.
    sm = scorecard.scan_metadata
    scan_metadata: dict = {
        "files_scanned": sm.files_scanned,
        "iac_files": sm.iac_files,
        "app_files": sm.app_files,
        "layers_covered": sm.layers_covered,
        "capped": sm.capped,
        "capped_reason": sm.capped_reason,
    }

    doc: dict = {
        # R-13: schema version at root.
        "schema_version": "1.0",
        # R-07: attribution in every output.
        "attribution": {
            "author": "Werner Vogels / The Frugal Architect",
            "url": "https://thefrugalarchitect.com/laws/",
            "notice": scorecard.attribution,
        },
        "mode": scorecard.mode.value,
        "overall_verdict": scorecard.overall_verdict,
        "biggest_opportunity": scorecard.biggest_opportunity,
        "law_scores": law_scores_list,
        "roadmap": {
            "items": roadmap_items,
            "displayed_count": displayed,
            "total_count": total,
        },
        "scan_metadata": scan_metadata,
        # R-08: caveat in every output.
        "caveat": scorecard.caveat,
    }

    return json.dumps(doc, indent=2, ensure_ascii=False)
