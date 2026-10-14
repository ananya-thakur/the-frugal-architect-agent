"""MemoryManager — hexagonal adapter over the AgentCore Memory API (unit-8).

Implements the five public methods described in [[business-logic-model-memory-manager]]:
  store_session / get_session      — session-scoped Scorecard persistence
  store_long_term / get_long_term  — cross-session ScorecardSummary persistence
  resolve_reference                — natural-language → LawScore | Finding resolution

All AgentCore Memory API errors are non-fatal (BR-MEM-009, BR-MEM-010): methods
log a WARNING and return None so the primary review flow is never blocked.
Log messages contain only method name and an enumerated category label;
user_id, session_id, and scorecard content are never logged (SEC-MEM-009).
"""

from __future__ import annotations

import dataclasses
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from agent.memory_manager.entities import (
    Finding,
    LawScore,
    Scorecard,
    ScorecardSummary,
)
from agent.memory_manager.protocol import AgentCoreMemoryProtocol

logger = logging.getLogger(__name__)

# ── Reference resolution helpers ─────────────────────────────────────────────

_ROMAN_TO_INT: dict[str, int] = {
    "i": 1,
    "ii": 2,
    "iii": 3,
    "iv": 4,
    "v": 5,
    "vi": 6,
    "vii": 7,
}


def _normalize_tokens(text: str) -> list[str]:
    """Lowercase, strip non-word chars (keep hyphens and digits), split."""
    text = text.lower()
    text = re.sub(r"[^\w\s-]", " ", text)
    return [t for t in text.split() if t]


def _extract_law_number(tokens: list[str]) -> int | None:
    """Return a law number 1-7 if the token stream references one.

    The BLM requires the number to be either directly preceded by the word
    "law" or appear in a sentence that already contains the word "law"
    (law-context rule).
    """
    # Direct adjacency: "law <N>" or "law <roman>"
    for i, token in enumerate(tokens):
        if token in ("law", "laws") and i + 1 < len(tokens):
            nxt = tokens[i + 1]
            if nxt.isdigit() and 1 <= int(nxt) <= 7:
                return int(nxt)
            if nxt in _ROMAN_TO_INT:
                return _ROMAN_TO_INT[nxt]

    # Contextual: "law" anywhere + a number/roman elsewhere in the stream
    if "law" in tokens or "laws" in tokens:
        for token in tokens:
            if token.isdigit() and 1 <= int(token) <= 7:
                return int(token)
            if token in _ROMAN_TO_INT:
                return _ROMAN_TO_INT[token]

    return None


# ── MemoryManager ─────────────────────────────────────────────────────────────


class MemoryManager:
    """Adapter: routes all memory I/O through the injected AgentCore API client."""

    _SESSION_NS = "session"
    _LONG_TERM_NS = "long_term"

    def __init__(self, api: AgentCoreMemoryProtocol) -> None:
        self._api = api

    # ── Session memory ────────────────────────────────────────────────────────

    def store_session(self, session_id: str, scorecard: Scorecard) -> None:
        """Persist *scorecard* under the session namespace (BR-MEM-009).

        Failures are non-fatal: the review result was already streamed to the
        user; a store error is logged as WARNING and swallowed.
        """
        if not isinstance(scorecard, Scorecard):
            raise TypeError(f"expected Scorecard, got {type(scorecard).__name__}")
        try:
            blob = json.dumps(scorecard.to_dict())
            self._api.put(namespace=self._SESSION_NS, key=session_id, value=blob)
        except Exception:  # noqa: BLE001
            logger.warning("store_session: failed [category=api_error]")

    def get_session(self, session_id: str) -> Scorecard | None:
        """Return the session Scorecard, or None on miss / API error (BR-MEM-010)."""
        try:
            raw = self._api.get(namespace=self._SESSION_NS, key=session_id)
        except Exception:  # noqa: BLE001
            logger.warning("get_session: failed [category=api_error]")
            return None
        if raw is None:
            return None
        try:
            return Scorecard.from_dict(json.loads(raw))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            # Exception message is deliberately NOT re-logged to prevent
            # serialised payload content from leaking into CloudWatch (SEC-MEM-009)
            logger.warning("get_session: failed [category=deserialization_error]")
            return None

    # ── Long-term memory ──────────────────────────────────────────────────────

    def store_long_term(
        self, user_id: str, project_id: str, summary: ScorecardSummary
    ) -> None:
        """Persist *summary* under the composite user/project key (BR-MEM-005).

        Steps (per [[business-logic-model-memory-manager]] §store_long_term):
        1. Type guard — raises TypeError on wrong input (SEC-MEM-006, BR-MEM-015).
        2. Truncate top_finding_summaries to ≤10 via immutable copy (BR-MEM-014).
        3. Stamp last_review_at at write time (BR-MEM-013).
        4. Serialise + persist; API errors → WARNING, no raise (BR-MEM-009).
        """
        if not isinstance(summary, ScorecardSummary):
            raise TypeError(f"expected ScorecardSummary, got {type(summary).__name__}")

        working = summary
        if len(working.top_finding_summaries) > 10:
            logger.debug("store_long_term: truncating top_finding_summaries [category=payload_cap]")
            working = dataclasses.replace(
                working, top_finding_summaries=working.top_finding_summaries[:10]
            )

        stamped = dataclasses.replace(
            working,
            last_review_at=datetime.now(timezone.utc).isoformat(),
        )

        key = f"{user_id}:{project_id}"
        try:
            blob = json.dumps(stamped.to_dict())
            self._api.put(namespace=self._LONG_TERM_NS, key=key, value=blob)
        except Exception:  # noqa: BLE001
            logger.warning("store_long_term: failed [category=api_error]")

    def get_long_term(
        self, user_id: str, project_id: str
    ) -> ScorecardSummary | None:
        """Return a prior ScorecardSummary, or None for first-time users (BR-MEM-011)."""
        key = f"{user_id}:{project_id}"
        try:
            raw = self._api.get(namespace=self._LONG_TERM_NS, key=key)
        except Exception:  # noqa: BLE001
            logger.warning("get_long_term: failed [category=api_error]")
            return None
        if raw is None:
            return None
        try:
            return ScorecardSummary.from_dict(json.loads(raw))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            logger.warning("get_long_term: failed [category=deserialization_error]")
            return None

    # ── Reference resolution ──────────────────────────────────────────────────

    def resolve_reference(
        self, session_id: str, ref: str
    ) -> LawScore | Finding | None:
        """Map a natural-language user reference to a LawScore or Finding.

        Decision tree (per [[business-logic-model-memory-manager]] §resolve_reference):
          A. Law number match  (arabic 1-7 or Roman I-VII with "law" context)
          B. Law title keyword match  (≥2 shared tokens)
          C. Anti-pattern type substring match
          D. Finding summary keyword match  (≥2 shared tokens)
          E. No match → None

        LawScore takes precedence over Finding when both could match (BR-MEM-018).
        Returns None without raising on any failure (BR-MEM-019).
        """
        scorecard = self.get_session(session_id)
        if scorecard is None:
            return None

        tokens = _normalize_tokens(ref)

        # A — law number match
        law_num = _extract_law_number(tokens)
        if law_num is not None:
            for ls in scorecard.law_scores:
                if ls.law_number == law_num:
                    return ls

        # B — law title keyword match (≥2 tokens)
        best_law: LawScore | None = None
        best_law_hits = 0
        for ls in scorecard.law_scores:
            title_tokens = set(_normalize_tokens(ls.law_title))
            hits = sum(1 for t in tokens if t in title_tokens)
            if hits >= 2 and hits > best_law_hits:
                best_law = ls
                best_law_hits = hits
        if best_law is not None:
            return best_law

        # C — anti-pattern type substring match
        for finding in scorecard.findings:
            if finding.anti_pattern_type:
                apt_tokens = set(_normalize_tokens(finding.anti_pattern_type))
                if apt_tokens.intersection(tokens):
                    return finding

        # D — finding summary keyword match (≥2 tokens)
        best_finding: Finding | None = None
        best_finding_hits = 0
        for finding in scorecard.findings:
            summary_tokens = set(_normalize_tokens(finding.summary))
            hits = sum(1 for t in tokens if t in summary_tokens)
            if hits >= 2 and hits > best_finding_hits:
                best_finding = finding
                best_finding_hits = hits
        if best_finding is not None:
            return best_finding

        return None


# ── Factory ───────────────────────────────────────────────────────────────────


class _NoopClient:
    """Silent no-op backend used when AgentCore Memory is not configured."""

    def put(self, namespace: str, key: str, value: str) -> None:  # noqa: ARG002
        pass

    def get(self, namespace: str, key: str) -> str | None:  # noqa: ARG002
        return None


def create_memory_manager() -> MemoryManager:
    """Construct a MemoryManager wired to the configured backend.

    Reads ``agent.config`` lazily so the factory can be called at agent startup
    without triggering env-var checks during module import (important in tests).
    """
    from agent import config  # noqa: PLC0415 — lazy import by design
    from agent.memory_manager.agentcore_client import AgentCoreMemoryClient

    api: AgentCoreMemoryProtocol
    if config.AGENTCORE_MEMORY_ID:
        api = AgentCoreMemoryClient(
            memory_id=config.AGENTCORE_MEMORY_ID,
            region=config.AGENTCORE_MEMORY_REGION,
        )
    else:
        api = _NoopClient()
    return MemoryManager(api=api)
