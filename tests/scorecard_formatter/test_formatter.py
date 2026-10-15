"""Tests for scorecard-formatter Markdown and JSON rendering.

Covers business rules R-01 through R-16 and validation invariants.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from agent.scorecard_formatter import (
    ScorecardFormatterError,
    format_json,
    format_law_block,
    format_markdown,
    format_roadmap,
)
from agent.scoring_engine.models import (
    ATTRIBUTION_TEXT,
    CAVEAT_TEXT,
    ConfidenceTag,
    Effort,
    EvidenceItem,
    Impact,
    LawScore,
    RemediationRoadmap,
    RemediationStep,
    ScanMetadata,
    Scorecard,
    ScoringMode,
    Verdict,
)


# ── Markdown: validation ──────────────────────────────────────────────────────


def test_format_markdown_raises_on_wrong_law_count(scan_metadata):
    """Constructing a Scorecard with != 7 laws raises an error (R-09 — Pydantic enforces this)."""
    from tests.scorecard_formatter.conftest import _make_law_score

    law_scores = [_make_law_score(law_number=i) for i in range(1, 6)]  # only 5
    with pytest.raises(Exception, match="7 LawScores"):
        Scorecard(
            law_scores=law_scores,
            roadmap=RemediationRoadmap(steps=[]),
            overall_verdict="STRONG",
            biggest_opportunity=None,
            caveat=CAVEAT_TEXT,
            attribution=ATTRIBUTION_TEXT,
            scan_metadata=scan_metadata,
            mode=ScoringMode.CODEBASE_REVIEW,
            created_at=datetime(2026, 10, 14, tzinfo=timezone.utc),
        )


def test_format_markdown_raises_on_missing_remediation_for_gap():
    """Constructing a GAP LawScore without remediation raises an error (R-10 — Pydantic enforces this)."""
    with pytest.raises(Exception, match="missing"):
        LawScore(
            law_id="law-1",
            law_title="Law 1 Title",
            law_number=1,
            verdict=Verdict.GAP,
            evidence=[],
            gap="Missing cost tagging.",
            target_state=None,  # missing — triggers Pydantic BR-04 validator
            remediation_steps=None,
            effort=None,
            impact=None,
        )


# ── Markdown: structure ───────────────────────────────────────────────────────


def test_format_markdown_contains_attribution_first(gap_scorecard):
    """Attribution block is the first visible content line (R-07)."""
    output = format_markdown(gap_scorecard)
    first_nonblank = next(line for line in output.splitlines() if line.strip())
    assert "Werner Vogels" in first_nonblank or "Attribution" in first_nonblank


def test_format_markdown_contains_caveat_last(gap_scorecard):
    """Advisory caveat is appended as the final content block (R-08)."""
    output = format_markdown(gap_scorecard)
    assert "static analysis" in output
    # Caveat must appear after all law blocks.
    assert output.index("static analysis") > output.index("Law 7")


def test_format_markdown_all_seven_laws_present(gap_scorecard):
    """All 7 law blocks are rendered in canonical order (R-04)."""
    output = format_markdown(gap_scorecard)
    for i in range(1, 8):
        assert f"Law {i}:" in output


def test_format_markdown_law_order_canonical(gap_scorecard):
    """Laws appear in ascending order Law 1 → Law 7 (R-04)."""
    output = format_markdown(gap_scorecard)
    positions = [output.index(f"## Law {i}:") for i in range(1, 8)]
    assert positions == sorted(positions)


def test_format_markdown_contains_overall_verdict(gap_scorecard):
    """Overall verdict line is rendered."""
    output = format_markdown(gap_scorecard)
    assert "Overall" in output
    assert "GAP" in output


def test_format_markdown_biggest_opportunity_rendered(gap_scorecard):
    """Biggest opportunity summary is present."""
    output = format_markdown(gap_scorecard)
    assert "Biggest opportunity" in output
    assert "Law 1" in output


# ── Markdown: R-01 FIRM before POSSIBLE ──────────────────────────────────────


def test_firm_before_possible_in_law_block(gap_scorecard):
    """FIRM evidence section appears before POSSIBLE evidence section in law 1 block (R-01)."""
    output = format_markdown(gap_scorecard)
    firm_pos = output.index("IaC/Config Evidence (FIRM)")
    # Law 3 has POSSIBLE evidence; its law block comes after law 1.
    possible_section = "App-Code Signals (POSSIBLE"
    assert firm_pos < output.index(possible_section)


def test_firm_block_present_even_when_empty(gap_scorecard):
    """Even when a law has no FIRM findings, the FIRM section is rendered (R-01)."""
    # Law 3 has only POSSIBLE evidence.
    output = format_markdown(gap_scorecard)
    # The IaC Evidence header for law 3's block — find law 3 section first.
    law3_pos = output.index("## Law 3:")
    law4_pos = output.index("## Law 4:")
    law3_block = output[law3_pos:law4_pos]
    assert "IaC/Config Evidence (FIRM)" in law3_block
    assert "No IaC/config evidence found" in law3_block


# ── Markdown: R-02 POSSIBLE framing ──────────────────────────────────────────


def test_possible_findings_framed_as_leads(gap_scorecard):
    """POSSIBLE findings include the investigation-lead framing text (R-02)."""
    output = format_markdown(gap_scorecard)
    assert "investigation lead, not a verdict" in output


def test_possible_badge_present(gap_scorecard):
    """POSSIBLE badge is rendered for POSSIBLE evidence items (R-15)."""
    output = format_markdown(gap_scorecard)
    assert "⚠️ **POSSIBLE**" in output


def test_firm_badge_present(gap_scorecard):
    """FIRM badge is rendered for FIRM evidence items (R-15)."""
    output = format_markdown(gap_scorecard)
    assert "🔒 **FIRM**" in output


# ── Markdown: R-03 UNKNOWN law blocks ────────────────────────────────────────


def test_unknown_law_renders_only_probing_question(gap_scorecard):
    """Law 2 (UNKNOWN) renders heading, verdict, probing question — no evidence or gap (R-03)."""
    output = format_markdown(gap_scorecard)
    law2_pos = output.index("## Law 2:")
    law3_pos = output.index("## Law 3:")
    law2_block = output[law2_pos:law3_pos]

    assert "UNKNOWN" in law2_block
    assert "revenue model" in law2_block  # probing question content
    # Must NOT contain gap analysis or evidence sections.
    assert "IaC/Config Evidence" not in law2_block
    assert "Gap:" not in law2_block
    assert "Recommended Actions" not in law2_block


# ── Markdown: R-16 aggregate confidence ──────────────────────────────────────


def test_aggregate_confidence_firm_when_firm_evidence_present(gap_scorecard):
    """Law 1 (has FIRM evidence) shows **Confidence:** FIRM in verdict line (R-16)."""
    output = format_markdown(gap_scorecard)
    law1_pos = output.index("## Law 1:")
    law2_pos = output.index("## Law 2:")
    law1_block = output[law1_pos:law2_pos]
    assert "**Confidence:** FIRM" in law1_block


def test_aggregate_confidence_possible_when_only_possible_evidence(gap_scorecard):
    """Law 3 (only POSSIBLE evidence) shows **Confidence:** POSSIBLE (R-16)."""
    output = format_markdown(gap_scorecard)
    law3_pos = output.index("## Law 3:")
    law4_pos = output.index("## Law 4:")
    law3_block = output[law3_pos:law4_pos]
    assert "**Confidence:** POSSIBLE" in law3_block


# ── Roadmap: R-05 overflow disclosure ────────────────────────────────────────


def test_roadmap_overflow_disclosed(gap_scorecard):
    """When more roadmap items exist than top_n, overflow count is disclosed (R-05)."""
    output = format_markdown(gap_scorecard, top_n=1)
    # 2 total steps, 1 displayed — 1 remaining.
    assert "1 additional recommendation" in output


def test_roadmap_no_overflow_note_when_all_fit(gap_scorecard):
    """No overflow note when all roadmap items fit within top_n (R-05)."""
    output = format_markdown(gap_scorecard, top_n=10)
    assert "additional recommendation" not in output


def test_roadmap_empty_when_all_strong(strong_scorecard):
    """Empty roadmap renders a 'no remediation required' note."""
    output = format_markdown(strong_scorecard)
    assert "No remediation steps required" in output


# ── JSON: structure ───────────────────────────────────────────────────────────


def test_format_json_valid_json(gap_scorecard):
    """format_json returns valid JSON."""
    output = format_json(gap_scorecard)
    parsed = json.loads(output)
    assert isinstance(parsed, dict)


def test_format_json_schema_version(gap_scorecard):
    """JSON output includes schema_version 1.0 at root (R-13)."""
    parsed = json.loads(format_json(gap_scorecard))
    assert parsed["schema_version"] == "1.0"


def test_format_json_attribution_present(gap_scorecard):
    """JSON output includes attribution block (R-07)."""
    parsed = json.loads(format_json(gap_scorecard))
    assert "attribution" in parsed
    assert "Werner Vogels" in parsed["attribution"]["author"]


def test_format_json_caveat_present(gap_scorecard):
    """JSON output includes caveat field (R-08)."""
    parsed = json.loads(format_json(gap_scorecard))
    assert "caveat" in parsed
    assert "static analysis" in parsed["caveat"]


def test_format_json_seven_law_scores(gap_scorecard):
    """JSON output contains exactly 7 law_scores (R-04)."""
    parsed = json.loads(format_json(gap_scorecard))
    assert len(parsed["law_scores"]) == 7


def test_format_json_law_order_canonical(gap_scorecard):
    """JSON law_scores are in ascending law_number order (R-04)."""
    parsed = json.loads(format_json(gap_scorecard))
    numbers = [ls["law_number"] for ls in parsed["law_scores"]]
    assert numbers == sorted(numbers)


def test_format_json_requires_runtime_confirmation_on_possible(gap_scorecard):
    """Every POSSIBLE finding in JSON has requires_runtime_confirmation: true (R-11)."""
    parsed = json.loads(format_json(gap_scorecard))
    for ls in parsed["law_scores"]:
        for f in ls.get("possible_findings", []):
            assert f["requires_runtime_confirmation"] is True


def test_format_json_scan_metadata_present(gap_scorecard):
    """JSON output includes scan_metadata block (R-14)."""
    parsed = json.loads(format_json(gap_scorecard))
    sm = parsed["scan_metadata"]
    assert sm["files_scanned"] == 120
    assert sm["iac_files"] == 15
    assert sm["capped"] is False


def test_format_json_roadmap_total_and_displayed(gap_scorecard):
    """JSON roadmap shows total_count and displayed_count (R-05)."""
    parsed = json.loads(format_json(gap_scorecard, top_n=1))
    roadmap = parsed["roadmap"]
    assert roadmap["total_count"] == 2
    assert roadmap["displayed_count"] == 1
    assert len(roadmap["items"]) == 1


def test_format_json_unknown_law_has_probing_question(gap_scorecard):
    """JSON Law 2 (UNKNOWN) includes probing_question, no gap (R-03)."""
    parsed = json.loads(format_json(gap_scorecard))
    law2 = next(ls for ls in parsed["law_scores"] if ls["law_number"] == 2)
    assert "probing_question" in law2
    assert "gap" not in law2


def test_format_json_gap_law_has_remediation(gap_scorecard):
    """JSON Law 1 (GAP) includes gap, target_state, remediation_steps (R-10, R-12)."""
    parsed = json.loads(format_json(gap_scorecard))
    law1 = next(ls for ls in parsed["law_scores"] if ls["law_number"] == 1)
    assert "gap" in law1
    assert "target_state" in law1
    assert len(law1["remediation_steps"]) >= 1


def test_format_json_no_data_loss_between_formats(gap_scorecard):
    """Every finding in Markdown appears in JSON (R-12)."""
    md = format_markdown(gap_scorecard)
    parsed = json.loads(format_json(gap_scorecard))

    # Collect all finding descriptions from JSON.
    json_descs: set[str] = set()
    for ls in parsed["law_scores"]:
        for f in ls.get("firm_findings", []):
            json_descs.add(f["description"])
        for f in ls.get("possible_findings", []):
            json_descs.add(f["description"])

    # Each JSON description should appear in the Markdown.
    for desc in json_descs:
        # Use a substring check — descriptions appear verbatim in markdown.
        assert desc in md, f"Description '{desc}' in JSON but not in Markdown (R-12)"


# ── format_law_block: direct unit tests ──────────────────────────────────────


def test_format_law_block_strong_no_remediation():
    """STRONG law block renders without gap or remediation section."""
    from tests.scorecard_formatter.conftest import _make_evidence, _make_law_score

    score = _make_law_score(
        law_number=1,
        verdict=Verdict.STRONG,
        evidence=[_make_evidence()],
    )
    block = format_law_block(score)
    assert "✅ STRONG" in block
    assert "Recommended Actions" not in block
    assert "🔒 **FIRM**" in block


def test_format_law_block_unknown_no_evidence_or_gap():
    """UNKNOWN law block: only heading, verdict, probing question (R-03)."""
    from tests.scorecard_formatter.conftest import _make_law_score

    score = _make_law_score(
        law_number=2,
        verdict=Verdict.UNKNOWN,
        probing_question="What is the revenue model?",
    )
    block = format_law_block(score)
    assert "❓ UNKNOWN" in block
    assert "What is the revenue model?" in block
    assert "IaC/Config Evidence" not in block
    assert "Gap:" not in block


def test_format_law_block_firm_before_possible():
    """Within a single law block, FIRM section precedes POSSIBLE section (R-01)."""
    from tests.scorecard_formatter.conftest import _make_evidence, _make_remediation_step

    firm_ev = _make_evidence(source_type=ConfidenceTag.FIRM)
    possible_ev = _make_evidence(
        source_type=ConfidenceTag.POSSIBLE,
        description="N+1 query detected.",
        citation="src/repo.py:42",
    )
    step = _make_remediation_step()
    score = LawScore(
        law_id="law-1",
        law_title="Law 1 Title",
        law_number=1,
        verdict=Verdict.PARTIAL,
        evidence=[firm_ev, possible_ev],
        gap="Cost not a NFR.",
        target_state="All resources sized to profiled optimum.",
        remediation_steps=[step],
        effort=Effort.LOW,
        impact=Impact.HIGH,
    )
    block = format_law_block(score)
    firm_pos = block.index("IaC/Config Evidence (FIRM)")
    possible_pos = block.index("App-Code Signals (POSSIBLE")
    assert firm_pos < possible_pos


# ── format_roadmap: direct unit tests ────────────────────────────────────────


def test_format_roadmap_empty():
    """Empty roadmap renders the 'no remediation required' note."""
    roadmap = RemediationRoadmap(steps=[])
    output = format_roadmap(roadmap)
    assert "No remediation steps required" in output


def test_format_roadmap_overflow_note_present():
    """Overflow note rendered when steps > top_n (R-05)."""
    from tests.scorecard_formatter.conftest import _make_remediation_step

    steps = [_make_remediation_step(law_id=f"law-{i}", priority_rank=i) for i in range(1, 4)]
    roadmap = RemediationRoadmap(steps=steps)
    output = format_roadmap(roadmap, top_n=2)
    assert "1 additional recommendation" in output


def test_format_roadmap_no_overflow_when_all_fit():
    """No overflow note when all steps fit (R-05)."""
    from tests.scorecard_formatter.conftest import _make_remediation_step

    steps = [_make_remediation_step(law_id="law-1", priority_rank=1)]
    roadmap = RemediationRoadmap(steps=steps)
    output = format_roadmap(roadmap, top_n=5)
    assert "additional recommendation" not in output
