"""
Predicate evaluator for IaCSignal.predicate expressions.

Supported forms (no eval() — ReDoS-safe, injection-safe):
  > N      <  N      >= N     <= N   — numeric comparison; False when value is None
  == null  != null              — null / not-null check
  == 'X'   != 'X'              — string equality (quotes optional)
  == X     != X                — string equality without quotes
"""

from __future__ import annotations

import re
from typing import Any

# ── Compiled patterns (pre-compiled once at import time) ──────────────────────

_RE_NUMERIC_CMP = re.compile(r"^(>=|<=|>|<)\s*(-?\d+(?:\.\d+)?)$")
_RE_EQ_NULL = re.compile(r"^==\s*null$", re.IGNORECASE)
_RE_NEQ_NULL = re.compile(r"^!=\s*null$", re.IGNORECASE)
_RE_EQ_STR = re.compile(r"^==\s*['\"]?([^'\"]+)['\"]?$")
_RE_NEQ_STR = re.compile(r"^!=\s*['\"]?([^'\"]+)['\"]?$")


class PredicateError(ValueError):
    """Raised when a predicate expression cannot be parsed."""


def evaluate(predicate: str, value: Any) -> bool:
    """
    Return True when ``value`` satisfies ``predicate`` (gap detected).

    Args:
        predicate: Expression string from IaCSignal.predicate.
        value:     Resolved attribute value (may be None if attribute absent).

    Returns:
        True if the predicate matches (frugality gap present), False otherwise.

    Raises:
        PredicateError: if predicate syntax is unrecognised.
    """
    p = predicate.strip()

    # null checks
    if _RE_EQ_NULL.match(p):
        return value is None

    if _RE_NEQ_NULL.match(p):
        return value is not None

    # numeric comparisons — return False when value absent (can't confirm gap)
    m = _RE_NUMERIC_CMP.match(p)
    if m:
        op, rhs_str = m.group(1), m.group(2)
        if value is None:
            return False
        try:
            lhs = float(value)
        except (TypeError, ValueError):
            return False
        rhs = float(rhs_str)
        if op == ">":
            return lhs > rhs
        if op == "<":
            return lhs < rhs
        if op == ">=":
            return lhs >= rhs
        # op == "<="
        return lhs <= rhs

    # string inequality — None is "not equal to anything" → True
    m = _RE_NEQ_STR.match(p)
    if m:
        rhs = m.group(1).strip()
        if value is None:
            return True  # absent ≠ expected value → gap
        return str(value).strip() != rhs

    # string equality
    m = _RE_EQ_STR.match(p)
    if m:
        rhs = m.group(1).strip()
        if value is None:
            return False
        return str(value).strip() == rhs

    raise PredicateError(
        f"Unrecognised predicate expression: {predicate!r}. "
        "Supported forms: > N, < N, >= N, <= N, == null, != null, == 'X', != 'X'."
    )


def resolve_attribute(attributes: dict[str, Any], path: str) -> Any:
    """
    Resolve ``path`` from ``attributes``.

    Strategy:
    1. Try the full dot-notation path as a literal flat key first (matches the
       output of _flatten which stores "Properties.MemorySize" as a single key).
    2. Fall back to segment-by-segment navigation for genuinely nested dicts
       (e.g. when a sub-scanner stores attributes as a nested dict).

    Returns None when the path is absent at every resolution attempt.
    """
    # Fast path: flat key stored verbatim (common in _flatten output)
    if path in attributes:
        return attributes[path]

    # Segment walk: for parsers that store nested dicts
    parts = path.split(".")
    current: Any = attributes
    for part in parts:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
        if current is None:
            return None
    return current
