"""
agent-assembly — Strands single-agent wiring for the Frugal Architect Agent (unit-10).

Creates the Strands Agent with all tools, the system prompt, and AgentCore
Memory integration.  This is the sole deployable service entry point; the
entrypoint.py calls create_agent() here.

Architecture decisions:
  ADR-001  Strands single-agent with LLM-choreographed tool chain
  ADR-002  rubric.yaml as sole domain-knowledge store
  ADR-003  Tiered confidence — FIRM for IaC, POSSIBLE for app-code
  ADR-004  AgentCore Memory exclusively for all agent persistence
  ADR-005  strands.models.BedrockModel with config-driven model ID
  ADR-006  Strands built-in OTEL for all instrumentation
  ADR-007  Scan cap enforcement at repo-connector boundary
  ADR-008  Hexagonal component boundary for all external integrations
"""

from __future__ import annotations

import logging
import re
from typing import Any

from agent import observability
from agent.app_scanner.models import (
    File as AppFile,
    PrioritizedInventory as AppInventory,
)
from agent.iac_scanner import scan_iac as _scan_iac
from agent.iac_scanner.models import (
    File as IaCFile,
    IaCFormat,
    PrioritizedInventory as IaCInventory,
)
from agent.rubric_core import get_law as _get_law
from agent.rubric_core import load_rubric as _load_rubric
from agent.scoring_engine.tool import (
    evaluate_decision,
    get_cached_findings,
    register_scan_results,
    score_project,
)
from agent.tools.repo_connector.tool import clone_or_read_repo, get_cached_inventory

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Strands SDK — graceful fallback so unit tests run without the SDK installed
# ---------------------------------------------------------------------------
try:
    from strands import Agent  # type: ignore[import]
    from strands import tool as _strands_tool
    from strands.models import BedrockModel  # type: ignore[import]

    def _tool_decorator(fn: Any) -> Any:  # type: ignore[return]
        return _strands_tool(fn)

except ImportError:
    Agent = None  # type: ignore[assignment,misc]
    BedrockModel = None  # type: ignore[assignment]

    def _tool_decorator(fn: Any) -> Any:  # type: ignore[return]
        return fn


# ---------------------------------------------------------------------------
# IaC format inference (path-only, deterministic; no content inspection)
# ---------------------------------------------------------------------------

def _infer_iac_format(path: str) -> IaCFormat:
    """
    Infer IaCFormat from a repo-relative file path.

    The mapping tracks repo-connector's classify_tier patterns so that every
    file classified as 'iac' by the repo-connector receives a format the
    iac-scanner can dispatch to a parser.  Files that match no specific pattern
    fall back to UNKNOWN (parse_file returns [] for UNKNOWN; not an error).
    """
    lower = path.replace("\\", "/").lower()
    segments = lower.split("/")
    basename = segments[-1]
    dir_segments = set(segments[:-1])

    # Terraform: *.tf, *.tfvars, *.hcl
    if basename.endswith((".tf", ".tfvars", ".hcl")):
        return IaCFormat.TERRAFORM

    # Dockerfile family
    if re.match(r"^dockerfile", basename):
        return IaCFormat.DOCKERFILE
    if re.match(r"^docker-compose.*\.(yml|yaml)$", basename):
        return IaCFormat.DOCKERFILE

    # Serverless Framework
    if basename in ("serverless.yml", "serverless.yaml"):
        return IaCFormat.SERVERLESS_YML

    # CDK Python: *_stack.py; app.py inside an IaC directory
    if basename.endswith("_stack.py"):
        return IaCFormat.CDK_PYTHON
    if basename == "app.py" and dir_segments & {"cdk", "infra", "infrastructure"}:
        return IaCFormat.CDK_PYTHON

    # CDK TypeScript: *_stack.ts; *.ts inside a CDK/infra directory
    if basename.endswith("_stack.ts"):
        return IaCFormat.CDK_TYPESCRIPT
    if basename.endswith(".ts") and dir_segments & {"cdk", "infra", "infrastructure"}:
        return IaCFormat.CDK_TYPESCRIPT

    # CDK JSON config files
    if basename in ("cdk.json", "cdk.context.json"):
        return IaCFormat.CDK_TYPESCRIPT

    # Kubernetes: YAML inside k8s/kubernetes directories
    if dir_segments & {"k8s", "kubernetes"} and basename.endswith((".yaml", ".yml")):
        return IaCFormat.KUBERNETES

    # CloudFormation / SAM: template.yaml / template.json
    if basename in ("template.yaml", "template.yml", "template.json"):
        return IaCFormat.CLOUDFORMATION

    # YAML in CF/SAM-specific directories → treat as CloudFormation
    if basename.endswith((".yaml", ".yml")):
        if dir_segments & {"cloudformation", "sam", "cfn", "stacks"}:
            return IaCFormat.CLOUDFORMATION

    return IaCFormat.UNKNOWN


# ---------------------------------------------------------------------------
# Type adapter: repo-connector RepoInventory → iac-scanner PrioritizedInventory
# ---------------------------------------------------------------------------

def _build_iac_inventory(repo_ref: str) -> IaCInventory:
    """
    Build an iac-scanner PrioritizedInventory from the cached RepoInventory.

    Only files with tier='iac' and non-None content are included.
    Files beyond the scan cap (content=None stubs) are silently excluded;
    the scope-transparency message is handled separately.
    """
    repo_inv = get_cached_inventory(repo_ref)
    if repo_inv is None:
        return IaCInventory.empty()

    iac_files: list[IaCFile] = []
    total_tokens = 0

    for record in repo_inv.files:
        if record.tier != "iac" or record.content is None:
            continue
        iac_files.append(
            IaCFile(
                path=record.path,
                content=record.content,
                format=_infer_iac_format(record.path),
                size_bytes=len(record.content.encode("utf-8", errors="replace")),
            )
        )
        total_tokens += record.estimated_tokens

    return IaCInventory(
        iac_files=tuple(iac_files),
        app_files=(),
        total_file_count=len(repo_inv.files),
        scan_token_estimate=total_tokens,
        repo_root=repo_inv.repo_ref.raw,
    )


# ---------------------------------------------------------------------------
# Type adapter: repo-connector RepoInventory → app-scanner PrioritizedInventory
# ---------------------------------------------------------------------------

def _build_app_inventory(repo_ref: str) -> AppInventory:
    """
    Build an app-scanner PrioritizedInventory from the cached RepoInventory.

    Both iac and app files are populated (app-scanner needs both for its
    secondary IaC guard to function correctly).  Files beyond the scan cap
    (content=None stubs) are counted as skipped.
    """
    repo_inv = get_cached_inventory(repo_ref)
    if repo_inv is None:
        return AppInventory(
            iac_files=[],
            app_files=[],
            files_scanned_count=0,
            files_skipped_count=0,
            scan_cap_reached=False,
            repo_root="",
        )

    iac_files: list[AppFile] = []
    app_files: list[AppFile] = []
    files_scanned = 0
    files_skipped = 0

    for record in repo_inv.files:
        # Derive extension from the filename component only
        filename = record.path.rsplit("/", 1)[-1]
        ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""

        if record.content is None:
            files_skipped += 1
            continue

        f = AppFile(
            path=record.path,
            content=record.content,
            extension=ext,
            size_bytes=len(record.content.encode("utf-8", errors="replace")),
        )
        if record.tier == "iac":
            iac_files.append(f)
        else:
            app_files.append(f)
        files_scanned += 1

    scope = repo_inv.scope_note
    return AppInventory(
        iac_files=iac_files,
        app_files=app_files,
        files_scanned_count=files_scanned,
        files_skipped_count=files_skipped,
        scan_cap_reached=scope.capped,
        repo_root=repo_inv.repo_ref.raw,
    )


# ---------------------------------------------------------------------------
# Strands tool: load_rubric
# ---------------------------------------------------------------------------

@_tool_decorator
def load_rubric_tool() -> dict[str, Any]:
    """
    Load and validate the Frugal Architecture rubric at agent startup.

    Returns a summary of the rubric structure so the agent can confirm it is
    grounded in the correct 7-law definitions before any analysis.

    The rubric.yaml is the sole source of the 7 Laws of Frugal Architecture
    by Werner Vogels / The Frugal Architect (https://thefrugalarchitect.com/laws/).
    Call this first in every review to confirm the rubric is loaded and valid.

    Returns:
        dict with 'status', 'law_count', 'anti_pattern_signal_count', and
        'laws' list (each with 'number' and 'title') for grounding confirmation.
    """
    try:
        rubric = _load_rubric()
        return {
            "status": "ok",
            "law_count": len(rubric.laws),
            "anti_pattern_signal_count": len(rubric.anti_pattern_signals),
            "laws": [
                {"number": law.number, "title": law.title}
                for law in rubric.laws
            ],
        }
    except Exception as exc:  # noqa: BLE001
        logger.error("load_rubric_tool failed: %s", exc)
        return {"status": "error", "message": str(exc)}


# ---------------------------------------------------------------------------
# Strands tool: scan_iac
# ---------------------------------------------------------------------------

@_tool_decorator
def scan_iac_tool(repo_ref: str) -> dict[str, Any]:
    """
    Exhaustively scan all IaC and config files for frugality gaps (FIRM confidence).

    Requires clone_or_read_repo to have been called first with the same repo_ref.
    Covers CDK (Python + TypeScript), CloudFormation/SAM, Terraform,
    serverless.yml, Kubernetes manifests, and Dockerfiles.

    Findings are tagged FIRM — sourced entirely from infrastructure-as-code.
    Every finding cites the specific Frugal Architecture law from the rubric.

    Attribution: the 7 Laws of Frugal Architecture are the intellectual property
    of Werner Vogels / The Frugal Architect (https://thefrugalarchitect.com/laws/).

    Args:
        repo_ref: Repository reference — same value passed to clone_or_read_repo.

    Returns:
        dict with 'status', 'findings_count', 'findings' (list of FIRM findings),
        and 'summary' with format/scope metadata.
    """
    try:
        rubric = _load_rubric()
        inventory = _build_iac_inventory(repo_ref)
        findings, summary = _scan_iac(inventory, rubric)

        # Merge with any existing possible findings already cached
        existing = get_cached_findings(repo_ref)
        possible = existing[1] if existing else []
        register_scan_results(repo_ref, findings, possible)

        return {
            "status": "ok",
            "findings_count": len(findings),
            "formats_scanned": summary.formats_scanned,
            "files_processed": summary.files_processed,
            "files_skipped_budget": summary.files_skipped_budget,
            "findings": [
                {
                    "law_id": f.law_id,
                    "law_title": f.law_title,
                    "file_path": f.file_path,
                    "resource_id": f.resource_id,
                    "resource_type": f.resource_type,
                    "line": f.line,
                    "gap": f.gap,
                    "evidence": f.evidence,
                    "confidence": f.confidence.value,
                }
                for f in findings
            ],
        }
    except Exception as exc:  # noqa: BLE001
        logger.error("scan_iac_tool failed for %r: %s", repo_ref, exc)
        return {"status": "error", "message": str(exc)}


# ---------------------------------------------------------------------------
# Strands tool: scan_app_antipatterns
# ---------------------------------------------------------------------------

@_tool_decorator
def scan_app_antipatterns_tool(repo_ref: str) -> dict[str, Any]:
    """
    Scan application code for known cost anti-patterns (POSSIBLE confidence).

    Requires clone_or_read_repo to have been called first with the same repo_ref.
    Searches for 10 specific cost anti-patterns: N+1 queries, missing pagination,
    full-table scans, polling instead of events, unbounded retries, missing caching
    on hot paths, over-fetching/oversized payloads, synchronous calls that could be
    async, loading whole objects to read one field, per-request heavy initialization.

    IMPORTANT: All findings carry POSSIBLE confidence — these are static analysis
    leads.  Confirming them requires runtime or cost data (CloudWatch, query plans,
    profiler output) not available through code inspection alone.

    Attribution: the 7 Laws of Frugal Architecture are the intellectual property
    of Werner Vogels / The Frugal Architect (https://thefrugalarchitect.com/laws/).

    Args:
        repo_ref: Repository reference — same value passed to clone_or_read_repo.

    Returns:
        dict with 'status', 'findings_count', 'findings' (list of POSSIBLE findings),
        and 'summary' with scope metadata.
    """
    try:
        from agent.app_scanner import scan_app_antipatterns as _scan_app  # local import avoids circular

        rubric = _load_rubric()
        inventory = _build_app_inventory(repo_ref)
        result = _scan_app(inventory, rubric)

        # Merge with any existing firm findings already cached
        existing = get_cached_findings(repo_ref)
        firm = existing[0] if existing else []
        register_scan_results(repo_ref, firm, result.findings)

        return {
            "status": "ok",
            "findings_count": len(result.findings),
            "files_scanned": result.summary.files_scanned,
            "findings": [
                {
                    "finding_id": f.finding_id,
                    "law_id": f.law_id,
                    "law_title": f.law_title,
                    "anti_pattern_type": f.anti_pattern_type,
                    "file_path": f.file_path,
                    "line_number": f.line_number,
                    "evidence": f.evidence,
                    "confidence": f.confidence,
                    "runtime_caveat": f.runtime_caveat,
                    "cost_impact_explanation": f.cost_impact_explanation,
                }
                for f in result.findings
            ],
        }
    except Exception as exc:  # noqa: BLE001
        logger.error("scan_app_antipatterns_tool failed for %r: %s", repo_ref, exc)
        return {"status": "error", "message": str(exc)}


# ---------------------------------------------------------------------------
# Strands tool: get_law
# ---------------------------------------------------------------------------

@_tool_decorator
def get_law_tool(law_ref: str) -> dict[str, Any]:
    """
    Retrieve the full definition of one of the 7 Laws of Frugal Architecture.

    Use this for TEACH mode (explaining a law in depth) and DECISION CHECK
    (evaluating a specific architecture decision against relevant laws).
    Always present retrieved laws with the Werner Vogels attribution.

    The 7 Laws of Frugal Architecture are the intellectual property of
    Werner Vogels / The Frugal Architect (https://thefrugalarchitect.com/laws/).

    Args:
        law_ref: Law number 1-7, numeric string ("3"), or law title keyword
                 (exact or partial match, case-insensitive).

    Returns:
        dict with 'status', 'number', 'title', 'strong_statement',
        'strong_looks_like', 'anti_patterns', 'probing_questions', 'attribution'.
    """
    try:
        law = _get_law(law_ref)
        return {
            "status": "ok",
            "number": law.number,
            "title": law.title,
            "description": law.description,
            "strong_looks_like": law.strong_looks_like,
            "probing_questions": law.probing_questions,
            "attribution": (
                "Werner Vogels / The Frugal Architect "
                "(https://thefrugalarchitect.com/laws/)"
            ),
        }
    except Exception as exc:  # noqa: BLE001
        logger.error("get_law_tool failed for ref %r: %s", law_ref, exc)
        return {"status": "error", "message": str(exc)}


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are the Frugal Architect Agent, grounded in the 7 Laws of Frugal Architecture
by Werner Vogels (https://thefrugalarchitect.com/laws/).

IMPORTANT ATTRIBUTION: The 7 Laws of Frugal Architecture are the intellectual
property of Werner Vogels / The Frugal Architect. You MUST attribute them in every
scorecard, every TEACH response, and your opening introduction. Never present the
laws as your own work.

ADVISORY SCOPE: You are advisory-only in v1. You read and recommend; you NEVER
modify code, open pull requests, or call AWS account/cost APIs. If asked to fix or
modify code, decline gracefully and offer to walk through the remediation roadmap.

RUBRIC GROUNDING: You MUST reason exclusively from the rubric loaded by
load_rubric_tool. Never invent architectural principles, laws, or findings beyond
the 7 Laws defined in rubric.yaml. Every finding must cite a specific law by number
and title exactly as it appears in the rubric.

━━━ FOUR INTERACTION MODES ━━━

Classify each user message into one of four modes:

1. CODEBASE REVIEW — user provides a repo URL or path, or says "scan my repo" /
   "review my codebase" / "analyze this repo".
   → Full analysis pipeline: load_rubric_tool → Law II gate →
     clone_or_read_repo → scan_iac_tool → scan_app_antipatterns_tool →
     score_project → present Frugal Architecture Scorecard

2. DESIGN REVIEW — user says "design review", "no repo", "describe my system",
   or clone_or_read_repo raises an SCM connectivity error (SCMNotConnectedError).
   → Consultative: ask the rubric probing questions law-by-law, collect answers,
     then call score_project with design_description

3. DECISION CHECK — user asks "should I use X", "is X frugal",
   "evaluate this decision", "which laws apply to X".
   → Call get_law_tool for 1-3 most relevant laws, then call evaluate_decision
     with the decision text grounded in those laws.

4. TEACH — user asks "explain Law N", "teach me about Law X", "what is Law N".
   → Call get_law_tool and present the law with full attribution.

If the mode is ambiguous, ask the user to clarify — enumerate all 4 modes by name.
NEVER silently assume a mode on ambiguous input.

━━━ FIRST TURN ━━━

On the FIRST TURN of every new session (regardless of the user's first message),
emit the onboarding intro BEFORE doing anything else:

---
Hi! I'm the Frugal Architect Agent, grounded in the 7 Laws of Frugal Architecture
by Werner Vogels (https://thefrugalarchitect.com/laws/). These laws are the
intellectual property of Werner Vogels / The Frugal Architect — I help you apply
them to your codebase.

Advisory only in v1: I read and recommend; I do not modify code or open PRs.

I can help in four ways:
  1. CODEBASE REVIEW — point me at a repo; I'll score it against all 7 laws
  2. DESIGN REVIEW — describe your system; I'll ask probing questions
  3. DECISION CHECK — share an architecture decision; I'll evaluate it
  4. TEACH — ask me to explain any of the 7 laws in depth

How would you like to start?
---

━━━ CODEBASE REVIEW PIPELINE (MANDATORY SEQUENCE) ━━━

Follow this exact 7-step sequence. Emit one sentence of progress BEFORE each tool
call so the user never sees a frozen screen.

Step 1: "Verifying rubric…"
  → load_rubric_tool()
  Confirm 7 laws loaded; abort with an error message if load fails.

Step 2: LAW II GATE — if no business/revenue context is known for this session:
  → Ask the user: "Before I access the repo, I need one input for Law II scoring:
    What is the business model or revenue driver behind this system
    (e.g. SaaS per-seat, per-API-call, batch ETL, internal tooling)?"
  → If user provides context: store as revenue_model for score_project.
  → If user explicitly declines: mark Law II = UNKNOWN with a probing question;
    DO NOT fabricate a Law II score.
  → If business context was already collected this session: skip this gate.

Step 3: "Cloning repository / reading repo…"
  → clone_or_read_repo(repo_ref)
  → If SCM connectivity error (SCMNotConnectedError):
    Tell the user: "Your source-control isn't connected. To connect: open the
    AgentCore console → Integrations → Source Control and complete the one-time
    OAuth flow. Once connected, re-run your review.
    Meanwhile, let me offer a Design Review — describe your system and I'll
    review it against the 7 laws."
    → Switch to DESIGN REVIEW mode (do NOT return an error).
  → If scan cap was hit: emit scope-transparency BEFORE the scorecard:
    "Scanned {files_included} of {files_seen} files (cap: {cap_limit}).
    Scope: {layers_covered}. To narrow to a specific directory, re-run and
    provide a subdirectory path."

Step 4: "Scanning IaC and config files…"
  → scan_iac_tool(repo_ref)

Step 5: "Scanning application code for cost anti-patterns…"
  → scan_app_antipatterns_tool(repo_ref)

Step 6: "Scoring against the 7 Laws of Frugal Architecture…"
  → score_project(repo_ref, mode="CODEBASE_REVIEW",
                  revenue_model=<from Step 2 if provided>)

Step 7: Present the Frugal Architecture Scorecard:
  - One-line overall verdict + biggest opportunity
  - Per-law block (for each of the 7 laws):
      Law N — {title} | Verdict: {STRONG/PARTIAL/GAP/UNKNOWN}
      Confidence: {FIRM/POSSIBLE/N/A}
      Evidence: {file-cited evidence}
      Fix: {concrete, ordered, AWS-specific steps}
  - Prioritised top-N roadmap (highest-value, lowest-effort first)
  - Attribution footer:
      "Based on the 7 Laws of Frugal Architecture by Werner Vogels /
       The Frugal Architect (https://thefrugalarchitect.com/laws/).
       Advisory only — static analysis, not a verified runtime/cost audit."
  - Export offer:
      "Type 'export scorecard' to save as Markdown or
       'export scorecard json' for JSON."

━━━ CONFIDENCE TIER LANGUAGE ━━━

IaC/config findings (FIRM):
  Introduce as "IaC analysis confirms:" or tag [FIRM].
  These come from infrastructure-as-code — high confidence.

App-code findings (POSSIBLE):
  Introduce as "Static analysis lead — needs verification:" or tag [POSSIBLE].
  ALWAYS state: "Confirming this requires runtime/cost data (CloudWatch,
  query execution plans, profiler output) not available through code inspection."
  Present as leads to investigate, not verdicts.

Ranking rule: FIRM findings appear above POSSIBLE findings in recommendations.
Noise rule: prefer a few high-value findings over a long list — noise is itself
un-frugal.

━━━ UNKNOWN VERDICT DISCIPLINE ━━━

When critical information is missing, emit UNKNOWN + a specific probing question:
  - Law II: business/revenue context absent → UNKNOWN; ask the probing question
  - Any other dimension: if you genuinely cannot determine compliance → UNKNOWN
  NEVER fabricate a verdict. A clear UNKNOWN is more useful than a made-up score.

━━━ GUARDRAILS (ABSOLUTE — NEVER OVERRIDE) ━━━

GR-001: NEVER modify code, files, or repository content in any form.
GR-002: NEVER open pull requests or create issues in any source control system.
GR-003: NEVER call AWS account or cost APIs (Cost Explorer, Budgets,
         Compute Optimizer, AWS Cost Anomaly Detection, etc.).
GR-004: NEVER provide accounting, tax, or financial planning advice.
         Scope is AWS and software cost-architecture guidance only.
GR-005: NEVER invent laws, principles, or findings beyond the 7 Laws in rubric.yaml.

━━━ FIX / PR DECLINE ━━━

When a user asks you to fix code, auto-remediate, apply changes, or open a PR:

"This agent is advisory-only in v1. It reads and recommends but does not modify
code or open pull requests — that's intentional so you control every change.

What I can do: walk you through the prioritised remediation roadmap from the
scorecard, law by law, with the exact steps to implement each fix.
Would you like me to walk through the top recommendations in detail?"

Then offer to surface the top recommendations from the last scorecard.

━━━ EXPORT ━━━

Detect these commands (case-insensitive):
  "export scorecard"      → return the full scorecard as Markdown
  "export scorecard json" → return the scorecard as JSON

Both formats MUST include the attribution header.
If no scorecard exists this session:
  "No scorecard available yet — run a CODEBASE REVIEW or DESIGN REVIEW first."

━━━ MEMORY-AWARE FOLLOW-UPS ━━━

Support natural follow-ups without re-running analysis:
  "just show me Law V"            → present Law V verdict from the last scorecard
  "explain that N+1 finding"      → expand the finding from session context
  "what was Law III's verdict?"   → retrieve from last scorecard
  "what changed since last time?" → compare to prior scorecard from long-term memory

For cross-session recall: if a prior scorecard for this project exists in
long-term memory, on first turn offer:
  "Welcome back! Your last review of {project} found {N} findings including
  [{top 2 findings}]. Start fresh or explore prior results?"

━━━ DESIGN REVIEW FLOW ━━━

1. Call load_rubric_tool() to get the 7 laws.
2. For each law, ask the law's probing questions.
3. Collect all answers as design_description.
4. If Law II context is still absent, ask explicitly before scoring.
5. Call score_project(repo_ref="design-review", mode="DESIGN_REVIEW",
                      design_description=<answers>, revenue_model=<if provided>)
6. Present scorecard (all findings tagged POSSIBLE — no file evidence).

━━━ DECISION CHECK FLOW ━━━

1. Identify 1-3 most relevant laws for the decision.
2. Call get_law_tool for each relevant law.
3. Call evaluate_decision(decision_text=<decision>, revenue_model=<if known>)
4. Present: per-law verdict + rationale + overall recommendation + attribution.

━━━ TEACH FLOW ━━━

1. Call get_law_tool(law_ref=<requested law>).
2. Present:
   Attribution header: "Law N — {title} (Werner Vogels, The Frugal Architect)"
   Core principle: description verbatim from rubric
   What strong looks like: strong_looks_like
   Probing questions: probing_questions from rubric
"""


# ---------------------------------------------------------------------------
# Memory client factory (ADR-004)
# ---------------------------------------------------------------------------

def _create_memory_client():
    """
    Create an AgentCore Memory client if configured; fall back to InMemoryStub.

    Production: AGENTCORE_MEMORY_ID set → AgentCoreMemoryClient (boto3-backed).
    Development / test: ID absent → InMemoryStub (no-op, no external calls).

    Never raises — memory failures must not block the primary review flow.
    """
    from agent import config  # lazy import — avoids startup-time env var check in tests
    from agent.memory_manager.stub import InMemoryStub  # local import for testability

    memory_id = config.AGENTCORE_MEMORY_ID
    if not memory_id:
        logger.info(
            "AGENTCORE_MEMORY_ID not set — using InMemoryStub for session/long-term memory"
        )
        return InMemoryStub()

    try:
        from agent.memory_manager.agentcore_client import AgentCoreMemoryClient
        return AgentCoreMemoryClient(
            memory_id=memory_id,
            region=config.AGENTCORE_MEMORY_REGION,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Failed to create AgentCoreMemoryClient (%s); falling back to InMemoryStub", exc
        )
        from agent.memory_manager.stub import InMemoryStub
        return InMemoryStub()


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------

def create_agent():
    """
    Assemble and return the Frugal Architect Strands Agent.

    Called once by entrypoint.py (lazy singleton pattern).  Initialises
    observability, eagerly validates the rubric, instantiates BedrockModel,
    wires all tools, and returns the configured Agent.

    Tool list (ADR-001 — LLM choreographs the tool chain):
      load_rubric_tool         — rubric load/validation confirmation
      clone_or_read_repo       — read-only SCM access with scan caps
      scan_iac_tool            — exhaustive IaC/config scan (FIRM findings)
      scan_app_antipatterns_tool — targeted app-code anti-pattern search (POSSIBLE)
      score_project            — 7-law scorecard assembly + roadmap
      evaluate_decision        — targeted 1-3 law evaluation for DECISION CHECK
      get_law_tool             — single-law retrieval for TEACH / DECISION CHECK

    Raises:
        RuntimeError: if the Strands SDK is not installed.
        RuntimeError: if config validation fails (AGENT_MODEL_ID missing, etc.)
                      — raised at config import time, not here.
    """
    if Agent is None or BedrockModel is None:
        raise RuntimeError(
            "Strands SDK not installed. "
            "Add 'strands-agents' to requirements.txt and re-install."
        )

    from agent import config  # lazy import — avoids startup-time env var check in tests

    # Initialise OTEL before the Agent is instantiated so Strands picks up the
    # configuration when it activates its built-in telemetry provider (ADR-006).
    observability.setup(
        endpoint=config.OTEL_EXPORTER_OTLP_ENDPOINT,
        service_name=config.OTEL_SERVICE_NAME,
        model_price=config.MODEL_PRICE_PER_TOKEN,
    )

    # Eagerly load and validate the rubric at startup.
    # Raises RubricLoadError if rubric.yaml is missing or malformed.
    _load_rubric()

    model = BedrockModel(model_id=config.AGENT_MODEL_ID)  # ADR-005

    agent = Agent(
        model=model,
        tools=[
            load_rubric_tool,
            clone_or_read_repo,
            scan_iac_tool,
            scan_app_antipatterns_tool,
            score_project,
            evaluate_decision,
            get_law_tool,
        ],
        system_prompt=SYSTEM_PROMPT,
    )

    logger.info(
        "Frugal Architect Agent created. model=%r tools=7 rubric=loaded",
        config.AGENT_MODEL_ID,  # config is imported at the top of this function
    )
    return agent
