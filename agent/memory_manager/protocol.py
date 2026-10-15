"""AgentCore Memory API protocol — the hexagonal port (ADR-008).

MemoryManager depends on this Protocol, not on any concrete SDK class.
Unit tests inject InMemoryStub; production code injects AgentCoreMemoryClient.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class AgentCoreMemoryProtocol(Protocol):
    """Minimal key-value interface over the AgentCore Memory API."""

    def put(self, namespace: str, key: str, value: str) -> None:
        """Persist ``value`` under ``namespace:key``."""
        ...

    def get(self, namespace: str, key: str) -> str | None:
        """Return the stored string or ``None`` if the key does not exist."""
        ...
