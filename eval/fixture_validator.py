"""
Fixture security validator — runs at harness startup before any test execution.

Scans all YAML files under the fixture directory for real AWS credentials,
account IDs, and ARN patterns. Raises FixtureSecurityViolation (caller exits
with code 2) on the first violation found.

Per security-design-eval-harness SR-EH-001.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Patterns ──────────────────────────────────────────────────────────────────

# AWS access key ID (always starts with AKIA)
_AKIA_KEY: re.Pattern[str] = re.compile(r"AKIA[0-9A-Z]{16}")

# 12-digit AWS account IDs (not inside larger digit runs)
_ACCOUNT_ID: re.Pattern[str] = re.compile(r"(?<!\d)(\d{12})(?!\d)")

# ARN with an embedded account ID
_ARN_ACCOUNT: re.Pattern[str] = re.compile(
    r"arn:aws:[a-z0-9\-]+:[a-z0-9\-]*:(\d{12}):"
)

# Synthetic / placeholder account IDs safe to include in fixtures
SYNTHETIC_ALLOW_LIST: frozenset[str] = frozenset(
    {"123456789012", "000000000000", "111111111111"}
)


# ── Exception ─────────────────────────────────────────────────────────────────


class FixtureSecurityViolation(Exception):
    """Raised when a fixture file contains a real credential or account reference."""

    def __init__(self, path: Path, pattern: str, excerpt: str) -> None:
        super().__init__(
            f"Fixture security violation in {path}: [{pattern}] — {excerpt[:80]!r}"
        )
        self.path = path
        self.pattern = pattern
        self.excerpt = excerpt


# ── Public API ────────────────────────────────────────────────────────────────


def validate_fixtures(fixture_dir: Path) -> None:
    """
    Scan every YAML file under fixture_dir for real AWS credentials.

    Raises FixtureSecurityViolation on the first violation found (the harness
    must exit with code 2 without running any tests).

    File-permission errors are logged at WARNING and skipped — they are not
    treated as credential violations because a transient read error must never
    abort the entire eval run.
    """
    for yaml_file in sorted(fixture_dir.rglob("*.yaml")):
        _validate_file(yaml_file)


def _validate_file(yaml_file: Path) -> None:
    try:
        content = yaml_file.read_text(encoding="utf-8")
    except (PermissionError, OSError) as exc:
        logger.warning("fixture_validator: cannot read %s — %s (skipping)", yaml_file, exc)
        return

    # Check for real AWS access key IDs
    match = _AKIA_KEY.search(content)
    if match:
        raise FixtureSecurityViolation(yaml_file, "AWS_ACCESS_KEY_ID", match.group())

    # Check standalone 12-digit account IDs not in the allow-list
    for m in _ACCOUNT_ID.finditer(content):
        account_id = m.group(1)
        if account_id not in SYNTHETIC_ALLOW_LIST:
            raise FixtureSecurityViolation(yaml_file, "REAL_ACCOUNT_ID", account_id)

    # Check ARNs whose embedded account ID is not in the allow-list
    for m in _ARN_ACCOUNT.finditer(content):
        account_id = m.group(1)
        if account_id not in SYNTHETIC_ALLOW_LIST:
            raise FixtureSecurityViolation(yaml_file, "REAL_ARN", m.group())
