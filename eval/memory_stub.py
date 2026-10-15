"""
In-process memory stub for eval runs.

Permitted sole exception to ADR-004: eval runs activate this stub when
AGENTCORE_MEMORY_AVAILABLE=false (the default in offline CI). The stub
implements the same interface as agent.memory_manager so the harness can
exercise agent code paths that use memory without a live AgentCore endpoint.

Per security-design-eval-harness SR-EH-003:
  - No network I/O; purely in-process dict
  - Lifetime is bounded to the eval process — discarded on process exit
  - Cross-run isolation guaranteed because each harness run is a new process
"""

from __future__ import annotations

from typing import Any, Optional


class InProcessMemoryStub:
    """Dict-backed memory stub. Not persistent; not thread-safe by design."""

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}

    # ── Session memory ────────────────────────────────────────────────────────

    def store_session(self, session_id: str, data: Any) -> None:  # noqa: ANN401
        self._store[f"session:{session_id}"] = data

    def get_session(self, session_id: str) -> Optional[Any]:  # noqa: ANN401
        return self._store.get(f"session:{session_id}")

    # ── Long-term memory ──────────────────────────────────────────────────────

    def store_long_term(self, user_id: str, key: str, data: Any) -> None:  # noqa: ANN401
        self._store[f"long_term:{user_id}:{key}"] = data

    def get_long_term(self, user_id: str, key: str) -> Optional[Any]:  # noqa: ANN401
        return self._store.get(f"long_term:{user_id}:{key}")

    # ── Reference resolution ──────────────────────────────────────────────────

    def resolve_reference(self, ref: str) -> Optional[Any]:  # noqa: ANN401
        """Always returns None — the stub has no cross-session references."""
        return None

    # ── Namespace cleanup ─────────────────────────────────────────────────────

    def cleanup_namespace(self, namespace_prefix: str) -> None:
        """Remove session and long-term entries whose ID starts with namespace_prefix."""
        # Internal keys are stored as "session:{session_id}" or "long_term:{user_id}:…"
        # We match on the ID portion so "eval-abc" cleans up "session:eval-abc-t1".
        prefix_session = f"session:{namespace_prefix}"
        prefix_long_term = f"long_term:{namespace_prefix}"
        to_remove = [
            k for k in self._store
            if k.startswith(prefix_session) or k.startswith(prefix_long_term)
        ]
        for key in to_remove:
            del self._store[key]
