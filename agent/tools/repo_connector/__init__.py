"""
repo-connector — read-only SCM access for the Frugal Architect Agent (unit-3).

Public API consumed by agent-assembly (unit-10) and scanner tools:
  clone_or_read_repo  — Strands tool; returns RepoInventory via cache
  get_cached_inventory — retrieve the last loaded inventory for scanner tools
  SCMNotConnectedError — typed exception caught by agent-assembly
  RepoInventory        — aggregate root crossing the hexagonal boundary
  ScopeNote            — scope boundary record (always present)
  FileRecord           — one file entry; tier is 'iac' or 'app'
"""

from agent.tools.repo_connector.tool import (
    clone_or_read_repo,
    get_cached_inventory,
)
from agent.tools.repo_connector.types import (
    ConnectivityResult,
    FileRecord,
    PrioritizedInventory,
    RawInventory,
    RepoInventory,
    RepoRef,
    SCMNotConnectedError,
    ScanLimits,
    ScopeNote,
)

__all__ = [
    "clone_or_read_repo",
    "get_cached_inventory",
    "ConnectivityResult",
    "FileRecord",
    "PrioritizedInventory",
    "RawInventory",
    "RepoInventory",
    "RepoRef",
    "SCMNotConnectedError",
    "ScanLimits",
    "ScopeNote",
]
