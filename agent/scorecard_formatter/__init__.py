"""scorecard-formatter: pure Markdown and JSON rendering for Frugal Architecture scorecards.

Public API:
  format_markdown(scorecard, rubric=None, top_n=5) -> str
  format_json(scorecard, top_n=5)                  -> str

The 7 Laws of Frugal Architecture are the intellectual property of
Werner Vogels / The Frugal Architect (https://thefrugalarchitect.com/laws/).
"""

from agent.scorecard_formatter.formatter import (
    ScorecardFormatterError,
    format_json,
    format_law_block,
    format_markdown,
    format_roadmap,
)

__all__ = [
    "format_markdown",
    "format_json",
    "format_law_block",
    "format_roadmap",
    "ScorecardFormatterError",
]
