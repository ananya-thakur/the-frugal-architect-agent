"""app-scanner unit — targeted app-code cost anti-pattern detection.

Public API:
  scan_app_antipatterns(inventory, rubric) -> AppScannerResult

All findings carry confidence="POSSIBLE" per ADR-003 and BR-APP-001.
"""

from agent.app_scanner.models import (
    AppScannerResult,
    File,
    PatternMatch,
    PossibleFinding,
    PrioritizedInventory,
    ScanSummary,
)
from agent.app_scanner.scanner import match_pattern, scan_app_antipatterns

__all__ = [
    "scan_app_antipatterns",
    "match_pattern",
    "AppScannerResult",
    "File",
    "PatternMatch",
    "PossibleFinding",
    "PrioritizedInventory",
    "ScanSummary",
]
