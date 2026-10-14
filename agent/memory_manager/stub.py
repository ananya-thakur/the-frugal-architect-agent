"""In-memory stubs for unit-testing the memory-manager (BR-MEM-020).

InMemoryStub — namespace-keyed dict; satisfies AgentCoreMemoryProtocol.
ErroringStub  — always raises RuntimeError; exercises degradation paths.
"""


class InMemoryStub:
    """Namespace-keyed in-memory store.  Never touches the AgentCore API."""

    def __init__(self) -> None:
        self._store: dict[str, dict[str, str]] = {}

    def put(self, namespace: str, key: str, value: str) -> None:
        self._store.setdefault(namespace, {})[key] = value

    def get(self, namespace: str, key: str) -> str | None:
        return self._store.get(namespace, {}).get(key)

    # ── test helpers ──────────────────────────────────────────────────────────

    def keys(self, namespace: str) -> list[str]:
        """Return all keys stored under *namespace*; useful in assertions."""
        return list(self._store.get(namespace, {}).keys())


class ErroringStub:
    """Always raises RuntimeError — exercises all API-error degradation paths."""

    def put(self, namespace: str, key: str, value: str) -> None:  # noqa: ARG002
        raise RuntimeError("Simulated AgentCore Memory API error")

    def get(self, namespace: str, key: str) -> str | None:  # noqa: ARG002
        raise RuntimeError("Simulated AgentCore Memory API error")
