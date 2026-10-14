"""
frugal-architect-agent package.

Each sub-module is populated by its own unit of work:
  rubric_core       — rubric.yaml loading and law lookup       (unit-2)
  repo_connector    — read-only SCM access                     (unit-3)
  iac_scanner       — exhaustive IaC/config scan               (unit-4)
  app_scanner       — targeted app-code anti-pattern search    (unit-5)
  scoring_engine    — per-law scorecard assembly               (unit-6)
  scorecard_fmt     — Frugal Architecture Scorecard rendering  (unit-7)
  memory_manager    — AgentCore Memory session + long-term     (unit-8)
  observability     — OTEL instrumentation                     (unit-9)
  agent_assembly    — Strands single-agent wiring              (unit-10)

This __init__ intentionally imports nothing so that individual units can be
developed, tested, and imported independently without triggering unresolved
imports from sibling units.
"""
