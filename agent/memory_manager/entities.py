"""Domain entity definitions for memory-manager (unit-8).

These value objects are produced by scoring-engine and scorecard-formatter.
memory-manager treats them as opaque: it serialises, persists, retrieves, and
deserialises them without modifying their domain content.

Serialisation contract: ``to_dict()`` produces a JSON-serialisable dict;
``from_dict()`` reconstructs the entity from that dict.  JSON requires string
keys, so ``ScorecardSummary.law_verdicts`` uses ``str`` keys on the wire and
``int`` keys in memory — ``from_dict`` handles the coercion.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Literal


@dataclasses.dataclass
class LawScore:
    """Per-law verdict entry within a Scorecard, produced by scoring-engine."""

    law_number: int
    law_title: str
    verdict: Literal["STRONG", "PARTIAL", "GAP", "UNKNOWN"]
    confidence_tag: str | None = None
    evidence: str | None = None
    gap: str | None = None
    target_state: str | None = None
    remediation_steps: list[str] | None = None
    effort: str | None = None
    impact: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LawScore":
        return cls(
            law_number=int(d["law_number"]),
            law_title=str(d["law_title"]),
            verdict=d["verdict"],
            confidence_tag=d.get("confidence_tag"),
            evidence=d.get("evidence"),
            gap=d.get("gap"),
            target_state=d.get("target_state"),
            remediation_steps=d.get("remediation_steps"),
            effort=d.get("effort"),
            impact=d.get("impact"),
        )


@dataclasses.dataclass
class Finding:
    """A single issue from IaC scan (FIRM) or app-code anti-pattern scan (POSSIBLE)."""

    id: str
    law_number: int
    law_title: str
    confidence: Literal["FIRM", "POSSIBLE"]
    summary: str
    cost_impact_explanation: str
    needs_runtime_confirmation: bool
    anti_pattern_type: str | None = None
    file_path: str | None = None
    resource_id: str | None = None
    line_number: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Finding":
        return cls(
            id=str(d["id"]),
            law_number=int(d["law_number"]),
            law_title=str(d["law_title"]),
            confidence=d["confidence"],
            summary=str(d["summary"]),
            cost_impact_explanation=str(d["cost_impact_explanation"]),
            needs_runtime_confirmation=bool(d["needs_runtime_confirmation"]),
            anti_pattern_type=d.get("anti_pattern_type"),
            file_path=d.get("file_path"),
            resource_id=d.get("resource_id"),
            line_number=d.get("line_number"),
        )


@dataclasses.dataclass
class RemediationStep:
    """An ordered entry in the scored scorecard's prioritised roadmap."""

    rank: int
    law_number: int
    action: str
    underlying_principle: str
    effort: Literal["low", "medium", "high"]
    impact: Literal["low", "medium", "high"]

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RemediationStep":
        return cls(
            rank=int(d["rank"]),
            law_number=int(d["law_number"]),
            action=str(d["action"]),
            underlying_principle=str(d["underlying_principle"]),
            effort=d["effort"],
            impact=d["impact"],
        )


@dataclasses.dataclass
class Scorecard:
    """Full review output written to session memory after each analysis pipeline run.

    Invariants (enforced by scoring-engine at creation time):
    - ``law_scores`` contains exactly 7 entries, one per Frugal Architecture law.
    - ``roadmap`` contains only entries for PARTIAL or GAP verdicts.
    """

    session_id: str
    overall_verdict: str
    biggest_opportunity: str
    law_scores: list[LawScore]
    findings: list[Finding]
    roadmap: list[RemediationStep]
    caveat: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "overall_verdict": self.overall_verdict,
            "biggest_opportunity": self.biggest_opportunity,
            "law_scores": [ls.to_dict() for ls in self.law_scores],
            "findings": [f.to_dict() for f in self.findings],
            "roadmap": [r.to_dict() for r in self.roadmap],
            "caveat": self.caveat,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Scorecard":
        return cls(
            session_id=str(d["session_id"]),
            overall_verdict=str(d["overall_verdict"]),
            biggest_opportunity=str(d["biggest_opportunity"]),
            law_scores=[LawScore.from_dict(ls) for ls in d["law_scores"]],
            findings=[Finding.from_dict(f) for f in d["findings"]],
            roadmap=[RemediationStep.from_dict(r) for r in d["roadmap"]],
            caveat=str(d["caveat"]),
            created_at=str(d["created_at"]),
        )


@dataclasses.dataclass
class ScorecardSummary:
    """Compact projection of a Scorecard written to long-term memory.

    ``last_review_at`` is always stamped by ``MemoryManager.store_long_term``
    at write time; any value supplied by the caller is overwritten (BR-MEM-013).
    ``top_finding_summaries`` is bounded to ≤ 10 items (BR-MEM-014).
    """

    user_id: str
    project_id: str
    overall_verdict: str
    law_verdicts: dict[int, str]
    top_finding_summaries: list[str]
    last_review_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "project_id": self.project_id,
            "overall_verdict": self.overall_verdict,
            # JSON keys must be strings; int keys serialised as str here
            "law_verdicts": {str(k): v for k, v in self.law_verdicts.items()},
            "top_finding_summaries": list(self.top_finding_summaries),
            "last_review_at": self.last_review_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ScorecardSummary":
        return cls(
            user_id=str(d["user_id"]),
            project_id=str(d["project_id"]),
            overall_verdict=str(d["overall_verdict"]),
            # JSON deserialises keys as str; coerce back to int
            law_verdicts={int(k): str(v) for k, v in d["law_verdicts"].items()},
            top_finding_summaries=[str(s) for s in d["top_finding_summaries"]],
            last_review_at=str(d.get("last_review_at", "")),
        )
