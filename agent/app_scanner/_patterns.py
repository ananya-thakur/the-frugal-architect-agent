"""Internal regex pattern registry for the app-scanner unit.

Each entry maps a rubric signal ID to a list of (compiled_pattern, language_filters)
pairs. Patterns are applied line-by-line with re.MULTILINE.

Design notes:
- All patterns are pre-compiled at module import to catch malformed expressions early.
- Language filters are frozensets of lowercase extensions; empty set = all text files.
- Patterns are deliberately conservative to minimise false positives.
- No catastrophic-backtracking (nested quantifiers) — kept deliberately simple.
"""

from __future__ import annotations

import re
from typing import NamedTuple

_ALL_EXTENSIONS: frozenset[str] = frozenset()  # empty = apply to all text files


class SignalPattern(NamedTuple):
    pattern: re.Pattern[str]
    language_filters: frozenset[str]  # empty = all
    context_lines: int


def _p(
    pattern_str: str,
    extensions: list[str],
    context_lines: int = 2,
) -> SignalPattern:
    return SignalPattern(
        pattern=re.compile(pattern_str, re.IGNORECASE | re.MULTILINE),
        language_filters=frozenset(extensions) if extensions else _ALL_EXTENSIONS,
        context_lines=context_lines,
    )


# ---------------------------------------------------------------------------
# Signal → pattern registry
# ---------------------------------------------------------------------------

SIGNAL_PATTERNS: dict[str, list[SignalPattern]] = {
    # ── 1. N+1 Queries ───────────────────────────────────────────────────────
    # Look for common ORM/DB method calls that appear inside loop bodies
    # (indicated by 4+ spaces of indentation in Python, or .map/.forEach in JS/TS)
    "n-plus-one-queries": [
        _p(r"^\s{4,}(?:await\s+)?\w+\.(?:get|find|filter|fetch|query|execute)\s*\(", [".py", ".js", ".ts"], 3),
        _p(r"^\s{4,}(?:db|session|cursor|conn|orm)\.\w+\s*\(", [".py"], 3),
        _p(r"\.map\s*\(\s*async\s+\w+\s*=>\s*(?:await\s+)?\w+\.(get|find|fetch|query)\s*\(", [".js", ".ts"], 3),
    ],

    # ── 2. Missing Pagination ────────────────────────────────────────────────
    "missing-pagination": [
        # DynamoDB scan() with no keyword args (almost always unbounded)
        _p(r"(?:table|client)\.scan\(\s*\)", [".py"]),
        # ORM .all() / .find_all() with no args
        _p(r"\.(all|find_all|fetch_all|list_all)\(\s*\)", [".py", ".js", ".ts", ".rb"]),
        # SQL SELECT ... FROM without LIMIT anywhere on same line
        _p(r"\bSELECT\b.+\bFROM\b", [".py", ".sql", ".go", ".java", ".rb"]),
    ],

    # ── 3. Full-Table Scans ───────────────────────────────────────────────────
    "full-table-scans": [
        _p(r"\btable\.scan\s*\(|client\.scan\s*\(\s*TableName\s*=", [".py"]),
        _p(r"dynamodb.*\.scan\s*\(", [".py", ".js", ".ts"]),
        _p(r"\bSELECT\s+\*\s+FROM\b", [".py", ".sql", ".js", ".ts", ".go", ".java", ".rb"]),
    ],

    # ── 4. Polling Instead of Events ─────────────────────────────────────────
    "polling-instead-of-events": [
        _p(r"while\s+(?:True|1)\s*:", [".py"]),
        _p(r"\btime\.sleep\s*\(|\basyncio\.sleep\s*\(", [".py"]),
        _p(r"\bsetInterval\s*\(", [".js", ".ts"]),
    ],

    # ── 5. Unbounded / Aggressive Retries ────────────────────────────────────
    "unbounded-aggressive-retries": [
        # @retry() with no arguments — no max set
        _p(r"@retry\s*\(\s*\)", [".py"]),
        # tenacity @retry without stop= or max_attempts= (simple check)
        _p(r"@retry\s*\((?:[^)]*)\)", [".py"]),
        # boto3 Config without retry (simple presence check)
        _p(r"botocore\.config\.Config\s*\(", [".py"]),
        # Infinite retry while loop pattern
        _p(r"while\s+(?:not\s+\w+|True)\s*:", [".py"]),
    ],

    # ── 6. No Caching on Hot Paths ────────────────────────────────────────────
    "no-caching-on-hot-paths": [
        # SSM Parameter Store calls (flag all; inside handler = likely no caching)
        _p(r"\bssm(?:_client)?\.get_parameter\s*\(", [".py"]),
        # Secrets Manager inside handler bodies
        _p(r"\bsecrets(?:manager|_client)?\.get_secret_value\s*\(", [".py"]),
        # boto3.client for SSM (common no-caching pattern)
        _p(r"boto3\.client\s*\(\s*['\"]ssm['\"]", [".py"]),
    ],

    # ── 7. Over-Fetching / Oversized Payloads ───────────────────────────────
    "over-fetching-oversized-payloads": [
        _p(r"\bSELECT\s+\*\s+FROM\b", [".py", ".sql", ".js", ".ts", ".go", ".java", ".rb"]),
        # DynamoDB get_item (flag all; scoring-engine evaluates ProjectionExpression presence)
        _p(r"\.get_item\s*\(\s*Key\s*=\s*\{", [".py"]),
        # ORM queries without field selection
        _p(r"\.(all|find_all|find)\(\s*\)", [".py", ".rb"]),
    ],

    # ── 8. Synchronous Where Async Possible ──────────────────────────────────
    "sync-where-async-possible": [
        # requests library (blocking HTTP calls)
        _p(r"\brequests\.(get|post|put|delete|patch|head)\s*\(", [".py"]),
        # SES send_email inline (should be queued)
        _p(r"ses(?:_client)?\.send_email\s*\(", [".py"]),
        # urllib blocking calls
        _p(r"\burllib\.request\.urlopen\s*\(", [".py"]),
    ],

    # ── 9. Per-Request Heavy Initialization ──────────────────────────────────
    "per-request-heavy-initialization": [
        # boto3.client/resource inside function bodies (line is indented + has boto3 call)
        _p(r"^\s{4,}.*\bboto3\.(?:client|resource)\s*\(", [".py"]),
        # Database connection creation inside functions (indented)
        _p(r"^\s{4,}.*(?:create_engine|psycopg2\.connect|pymysql\.connect|sqlite3\.connect)\s*\(", [".py"]),
    ],

    # ── 10. Loading Whole Object to Read One Field ────────────────────────────
    "whole-object-single-field-read": [
        # DynamoDB get_item (flag all; report notes to add ProjectionExpression)
        _p(r"\.get_item\s*\(\s*Key\s*=\s*\{", [".py"]),
        # S3 GetObject (full object fetch — often over-fetching)
        _p(r"\.get_object\s*\(\s*Bucket\s*=", [".py"]),
    ],
}
