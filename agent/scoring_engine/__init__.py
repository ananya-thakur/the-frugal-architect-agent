"""scoring-engine unit — 7-law Frugal Architecture scoring pipeline.

Public API:
  score_project(firm, possible, rubric, biz, mode) → Scorecard
  decision_check(decision_text, rubric, biz)        → DecisionCheckResult
  score_law(law, firm, possible, biz, mode)          → LawScore
  build_roadmap(scores)                             → RemediationRoadmap

Strands tools (registered in tool.py):
  score_project   — full 7-law scorecard
  evaluate_decision — targeted 1–3 law DECISION CHECK

The 7 Laws of Frugal Architecture are the intellectual property of
Werner Vogels / The Frugal Architect (https://thefrugalarchitect.com/laws/).
"""

from agent.scoring_engine.engine import (
    build_roadmap,
    decision_check,
    derive_overall,
    extract_scan_metadata,
    score_law,
    score_project,
)
from agent.scoring_engine.models import (
    ATTRIBUTION_TEXT,
    CAVEAT_TEXT,
    BusinessContext,
    ConfidenceTag,
    DecisionCheckResult,
    Effort,
    EvidenceItem,
    Impact,
    IncompleteGapAnalysisError,
    InputSizeLimitError,
    LawScore,
    RemediationRoadmap,
    RemediationStep,
    RubricIntegrityError,
    RubricNotLoadedError,
    ScanMetadata,
    Scorecard,
    ScorecardIntegrityError,
    ScoringMode,
    Verdict,
)
from agent.scoring_engine.tool import (
    evaluate_decision,
    get_cached_findings,
    register_scan_results,
)

__all__ = [
    # Engine
    "score_project",
    "decision_check",
    "score_law",
    "build_roadmap",
    "derive_overall",
    "extract_scan_metadata",
    # Models
    "Verdict",
    "ConfidenceTag",
    "Effort",
    "Impact",
    "ScoringMode",
    "EvidenceItem",
    "RemediationStep",
    "BusinessContext",
    "ScanMetadata",
    "LawScore",
    "RemediationRoadmap",
    "Scorecard",
    "DecisionCheckResult",
    "ATTRIBUTION_TEXT",
    "CAVEAT_TEXT",
    # Exceptions
    "RubricNotLoadedError",
    "RubricIntegrityError",
    "IncompleteGapAnalysisError",
    "InputSizeLimitError",
    "ScorecardIntegrityError",
    # Tool helpers
    "evaluate_decision",
    "register_scan_results",
    "get_cached_findings",
]
