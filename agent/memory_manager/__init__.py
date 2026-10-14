"""memory-manager — hexagonal adapter over AgentCore Memory API (unit-8).

All other agent components call methods on MemoryManager; none of them import
or call the AgentCore Memory SDK directly (ADR-004, ADR-008).
"""

from agent.memory_manager.agentcore_client import AgentCoreMemoryClient
from agent.memory_manager.entities import (
    Finding,
    LawScore,
    RemediationStep,
    Scorecard,
    ScorecardSummary,
)
from agent.memory_manager.manager import MemoryManager, create_memory_manager
from agent.memory_manager.protocol import AgentCoreMemoryProtocol
from agent.memory_manager.stub import InMemoryStub

__all__ = [
    "AgentCoreMemoryClient",
    "AgentCoreMemoryProtocol",
    "Finding",
    "InMemoryStub",
    "LawScore",
    "MemoryManager",
    "RemediationStep",
    "Scorecard",
    "ScorecardSummary",
    "create_memory_manager",
]
