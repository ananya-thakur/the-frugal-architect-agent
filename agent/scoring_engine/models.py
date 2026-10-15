"""Domain entities for the scoring-engine unit.

Enumerations, value objects, entities, aggregates, and custom exceptions
for the Frugal Architecture 7-law scoring pipeline.

All public inputs/outputs use Pydantic v2 strict typing (SR-01).
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ── Enumerations ──────────────────────────────────────────────────────────────


class Verdict(str, Enum):
    """Four-point verdict vocabulary for per-law assessment."""

    STRONG  = "STRONG"   # ≥80% of strong_looks_like criteria met by FIRM evidence
    PARTIAL = "PARTIAL"  # Some criteria met or hinted; gap analysis required
    GAP     = "GAP"      # No meaningful coverage; gap analysis required
    UNKNOWN = "UNKNOWN"  # Insufficient evidence/context; probing question required


class ConfidenceTag(str, Enum):
    """Source confidence tier for evidence items and remediation steps (ADR-003)."""

    FIRM      = "FIRM"       # IaC/config scan (iac-scanner); exhaustive, high confidence
    POSSIBLE  = "POSSIBLE"   # App-code anti-pattern (app-scanner); needs runtime confirmation
    DESCRIBED = "DESCRIBED"  # User description in DESIGN REVIEW mode; inference only


class Effort(str, Enum):
    """Estimated implementation effort for a remediation step."""

    LOW    = "LOW"     # Hours to days; config change or single resource update
    MEDIUM = "MEDIUM"  # Days to a sprint; moderate rework
    HIGH   = "HIGH"    # Multi-sprint; architectural change


class Impact(str, Enum):
    """Frugality improvement impact for a remediation step."""

    LOW      = "LOW"       # Minor frugality improvement
    MEDIUM   = "MEDIUM"    # Measurable cost reduction
    HIGH     = "HIGH"      # Significant ongoing cost reduction
    CRITICAL = "CRITICAL"  # Systemic cost drain or fundamental law misalignment


class ScoringMode(str, Enum):
    """Operating mode for score_project."""

    CODEBASE_REVIEW = "CODEBASE_REVIEW"  # Full scan pipeline; firm + possible findings
    DESIGN_REVIEW   = "DESIGN_REVIEW"    # No scan; probing-question dialogue
    DECISION_CHECK  = "DECISION_CHECK"   # Targeted 1–3 law evaluation of a decision


# ── String constants (BR-10, BR-11) ──────────────────────────────────────────

ATTRIBUTION_TEXT: str = (
    "The 7 Laws of Frugal Architecture are the intellectual property of Werner Vogels / "
    "The Frugal Architect (thefrugalarchitect.com). This scorecard is an advisory analysis "
    "grounded in those laws."
)

CAVEAT_TEXT: str = (
    "This scorecard reflects static analysis of the code as provided. Findings do not "
    "constitute a verified runtime or cost audit. POSSIBLE findings are investigation leads "
    "that require runtime or cost data to confirm."
)

RUNTIME_CONFIRMATION_NOTE: str = (
    "Confirm this finding with runtime/cost data before acting — this is an investigation "
    "lead, not a verified issue."
)

# ── Scoring thresholds and weights ───────────────────────────────────────────

STRONG_THRESHOLD: float = 0.80  # Fraction of unviolated criteria required for STRONG verdict

IMPACT_WEIGHT: dict[Impact, int] = {
    Impact.CRITICAL: 4,
    Impact.HIGH:     3,
    Impact.MEDIUM:   2,
    Impact.LOW:      1,
}

EFFORT_WEIGHT: dict[Effort, int] = {
    Effort.LOW:    0,
    Effort.MEDIUM: 1,
    Effort.HIGH:   2,
}

# ── Size limits (SR-06) ───────────────────────────────────────────────────────

FIRM_LIMIT:        int = 500
POSSIBLE_LIMIT:    int = 500
COMBINED_LIMIT:    int = 800
ROADMAP_LIMIT:     int = 50
SNIPPET_MAX_CHARS: int = 2_000


# ── Exceptions ────────────────────────────────────────────────────────────────


class RubricNotLoadedError(Exception):
    """score_project called without a loaded rubric (BR-12)."""


class RubricIntegrityError(Exception):
    """Rubric failed origin / integrity check (SR-02)."""


class IncompleteGapAnalysisError(Exception):
    """PARTIAL/GAP LawScore is missing required gap-analysis fields (BR-04)."""


class InputSizeLimitError(Exception):
    """Input lists exceed configured size limits (SR-06)."""


class ScorecardIntegrityError(Exception):
    """Scorecard attribution or caveat absent or modified (SR-04)."""


# ── Value Objects ─────────────────────────────────────────────────────────────


class EvidenceItem(BaseModel):
    """Immutable evidence item. FIRM items precede POSSIBLE in all lists (BR-07)."""

    model_config = ConfigDict(frozen=True)

    source_type:    ConfidenceTag
    citation:       str = Field(min_length=1)
    description:    str = Field(min_length=1)
    confidence_tag: str = Field(min_length=1)
    law_id:         str = Field(min_length=1)


class RemediationStep(BaseModel):
    """One ordered remediation action. priority_rank is assigned by build_roadmap."""

    # Not frozen: priority_rank is set after sorting in build_roadmap.
    model_config = ConfigDict(frozen=False)

    law_id:                    str
    description:               str
    aws_actions:               list[str]
    portable_principle:        str
    effort:                    Effort
    impact:                    Impact
    source_confidence:         ConfidenceTag
    priority_score:            int
    priority_rank:             int = 0
    runtime_confirmation_note: str | None = None


class BusinessContext(BaseModel):
    """Business and architectural context provided by agent-assembly.

    revenue_model is required for Law II scoring (BR-02).
    law_answers keyed by law_id string e.g. "law-2".
    """

    model_config = ConfigDict(frozen=True)

    revenue_model:        str | None = None
    cost_allocation:      str | None = None
    business_criticality: str | None = None
    design_description:   str | None = None
    law_answers:          dict[str, str] = Field(default_factory=dict)


class ScanMetadata(BaseModel):
    """Scan boundary data for scope-transparency messages."""

    model_config = ConfigDict(frozen=True)

    files_scanned:  int
    iac_files:      int
    app_files:      int
    capped:         bool
    capped_reason:  str | None = None
    layers_covered: list[str]


# ── Entities ──────────────────────────────────────────────────────────────────


class LawScore(BaseModel):
    """Per-law scoring result produced by score_law.

    Invariants (BR-04, BR-03):
    - gap/target_state/remediation_steps/effort/impact are ALL present for PARTIAL/GAP.
    - probing_question is present if and only if verdict == UNKNOWN.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    law_id:            str
    law_title:         str
    law_number:        int
    verdict:           Verdict
    evidence:          list[EvidenceItem]
    gap:               str | None = None
    target_state:      str | None = None
    remediation_steps: list[RemediationStep] | None = None
    effort:            Effort | None = None
    impact:            Impact | None = None
    probing_question:  str | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> LawScore:
        if self.verdict in (Verdict.PARTIAL, Verdict.GAP):
            gap_fields = ("gap", "target_state", "remediation_steps", "effort", "impact")
            missing = [f for f in gap_fields if getattr(self, f) is None]
            if missing:
                raise ValueError(
                    f"LawScore({self.law_id}) verdict={self.verdict} is missing: {missing} (BR-04)"
                )
        if self.verdict == Verdict.UNKNOWN and self.probing_question is None:
            raise ValueError(
                f"LawScore({self.law_id}) UNKNOWN verdict requires probing_question (BR-03)"
            )
        if self.verdict != Verdict.UNKNOWN and self.probing_question is not None:
            raise ValueError(
                f"LawScore({self.law_id}) probing_question must be None for {self.verdict}"
            )
        return self


# ── Aggregates ────────────────────────────────────────────────────────────────


class RemediationRoadmap(BaseModel):
    """Priority-ordered remediation roadmap across all laws."""

    model_config = ConfigDict(frozen=True)

    steps: list[RemediationStep]


class Scorecard(BaseModel):
    """Aggregate root for CODEBASE REVIEW and DESIGN REVIEW modes.

    Exactly 7 LawScore entries ordered by law_number ASC (BR-06).
    attribution and caveat are required non-empty strings (BR-10, BR-11).
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    law_scores:          list[LawScore]
    roadmap:             RemediationRoadmap
    overall_verdict:     str
    biggest_opportunity: str | None = None
    caveat:              str
    attribution:         str
    scan_metadata:       ScanMetadata
    mode:                ScoringMode
    created_at:          Any  # datetime; Any avoids strict-mode issues with datetime

    @model_validator(mode="after")
    def _check_scorecard(self) -> Scorecard:
        if len(self.law_scores) != 7:
            raise ValueError(
                f"Scorecard must have exactly 7 LawScores, got {len(self.law_scores)} (BR-06)"
            )
        if not self.attribution:
            raise ScorecardIntegrityError(
                "Scorecard.attribution must not be empty (BR-10)"
            )
        if not self.caveat:
            raise ScorecardIntegrityError(
                "Scorecard.caveat must not be empty (BR-11)"
            )
        return self


class DecisionCheckResult(BaseModel):
    """Result for DECISION CHECK mode. 1–3 LawScores (BR-16, BR-17)."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    relevant_laws:  list[LawScore]
    recommendation: str
    attribution:    str
    decision_text:  str

    @field_validator("relevant_laws")
    @classmethod
    def _cap_relevant_laws(cls, v: list[LawScore]) -> list[LawScore]:
        if len(v) > 3:
            raise ValueError(
                f"DecisionCheckResult.relevant_laws must not exceed 3 entries, got {len(v)} (BR-16)"
            )
        if len(v) < 1:
            raise ValueError("DecisionCheckResult.relevant_laws must contain at least 1 entry")
        return v
