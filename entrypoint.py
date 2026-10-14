"""
AgentCore Runtime entrypoint for the Frugal Architect Agent.

The AgentCore Runtime calls handler(event, context) for each invocation.
Config loading happens at module import time (startup), so a misconfigured
deployment fails immediately with a clear error rather than silently
at first user request.

Reference layout: event-driven-claims-agent (AgentCore + Strands SDK pattern).
"""

# Config is imported first — raises RuntimeError if required env vars are missing.
from agent import config as _config  # noqa: F401 (side-effect: startup validation)

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Lazy import of the assembled agent so that unit tests for config/entrypoint
# don't require the full Strands + Bedrock SDK to be installed.
_agent = None


def _get_agent():
    """Return the singleton Strands agent, initialising it on first call."""
    global _agent
    if _agent is None:
        # Populated by unit-10 (agent-assembly).  Import deferred so the
        # packaging scaffold tests remain lightweight.
        from agent.agent_assembly import create_agent  # type: ignore[import]
        _agent = create_agent()
    return _agent


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    AgentCore Runtime invocation handler.

    event schema (AgentCore standard):
      {
        "session_id": str,          # unique conversation session
        "input_text":  str,         # user message
        "user_id":     str | None,  # resolved by Cognito/OIDC at platform layer
      }

    Returns:
      {
        "output_text": str,   # agent response
        "session_id":  str,
      }
    """
    session_id = event.get("session_id", "")
    input_text = event.get("input_text", "")

    logger.info(
        "Invocation received",
        extra={"session_id": session_id, "input_length": len(input_text)},
    )

    agent = _get_agent()
    result = agent.invoke(input_text, session_id=session_id)

    return {
        "output_text": result,
        "session_id": session_id,
    }
