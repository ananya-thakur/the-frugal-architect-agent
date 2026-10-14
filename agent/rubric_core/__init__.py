"""
rubric-core: domain-knowledge store for the Frugal Architect Agent.

Exposes the 7 Laws of Frugal Architecture by Werner Vogels
(https://thefrugalarchitect.com/laws/) via a validated, cached rubric.yaml.

Public API:
  load_rubric(path=None)           → Rubric
  get_law(law_ref)                 → LawDefinition
  get_anti_pattern_signals()       → list[AntiPatternSignal]
  validate_citation(number, title) → bool
"""

from agent.rubric_core.engine import (
    get_anti_pattern_signals,
    get_law,
    load_rubric,
    validate_citation,
)
from agent.rubric_core.models import (
    AntiPatternSignal,
    LawDefinition,
    LawReference,
    Rubric,
    RubricCitationError,
    RubricLoadError,
    UnknownLawError,
)

__all__ = [
    "load_rubric",
    "get_law",
    "get_anti_pattern_signals",
    "validate_citation",
    "Rubric",
    "LawDefinition",
    "AntiPatternSignal",
    "LawReference",
    "RubricLoadError",
    "UnknownLawError",
    "RubricCitationError",
]
