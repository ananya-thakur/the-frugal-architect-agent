"""Pydantic v2 domain models for rubric-core."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class IaCSignal(BaseModel):
    """One detectable frugality anti-pattern at the IaC layer (ADR-002, BR-02)."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    formats: list[str] = Field(min_length=1)
    resource_type_filter: str | None = None
    attribute_path: str = Field(min_length=1)
    predicate: str = Field(min_length=1)
    gap_description: str = Field(min_length=1)
    evidence_template: str = Field(min_length=1)
    severity_rank: int = Field(ge=1, le=3)


class LawDefinition(BaseModel):
    """One of the 7 Laws of Frugal Architecture by Werner Vogels."""

    model_config = ConfigDict(frozen=True)

    number: int = Field(ge=1, le=7)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    strong_looks_like: str = Field(min_length=1)
    probing_questions: list[str] = Field(min_length=1)
    v2_verifiable_signals: list[str] = Field(default_factory=list)
    iac_signals: list[IaCSignal] = Field(default_factory=list)


class AntiPatternSignal(BaseModel):
    """One of the 10 app-code cost anti-pattern signal types."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    matching_criteria: str = Field(min_length=1)
    related_laws: list[int] = Field(min_length=1)


class Rubric(BaseModel):
    """Root aggregate: the loaded, validated rubric.yaml content."""

    model_config = ConfigDict(frozen=True)

    version: str = Field(min_length=1)
    attribution: str = Field(min_length=1)
    laws: list[LawDefinition]
    anti_pattern_signals: list[AntiPatternSignal]

    @model_validator(mode="after")
    def _validate_structure(self) -> Rubric:
        if len(self.laws) != 7:
            raise ValueError(
                f"Rubric must contain exactly 7 laws, got {len(self.laws)}"
            )
        numbers = {law.number for law in self.laws}
        if numbers != {1, 2, 3, 4, 5, 6, 7}:
            raise ValueError(
                f"Law numbers must be exactly {{1,2,3,4,5,6,7}}, got {sorted(numbers)}"
            )
        if len(self.anti_pattern_signals) != 10:
            raise ValueError(
                f"Rubric must contain exactly 10 anti-pattern signals, "
                f"got {len(self.anti_pattern_signals)}"
            )
        return self


class LawReference(BaseModel):
    """Value object: a citation pairing law number and exact title."""

    model_config = ConfigDict(frozen=True)

    number: int
    title: str


class RubricLoadError(Exception):
    """Raised when rubric.yaml cannot be loaded or fails validation (RULE-RUB-005)."""


class UnknownLawError(Exception):
    """Raised by get_law when the reference matches no rubric entry (RULE-RUB-007)."""


class RubricCitationError(Exception):
    """Raised by scoring-engine when validate_citation returns False (RULE-RUB-008)."""

    def __init__(self, law_number: int, law_title: str) -> None:
        super().__init__(
            f"Citation ({law_number!r}, {law_title!r}) does not match the loaded rubric"
        )
        self.law_number = law_number
        self.law_title = law_title
