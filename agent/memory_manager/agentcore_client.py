"""Real AgentCore Memory API client using boto3 (production implementation).

Instantiated by ``create_memory_manager()`` when ``AGENTCORE_MEMORY_ID`` is
configured.  The AgentCore runtime's ambient IAM role provides all credentials;
no explicit credential material is passed (SEC-MEM-012, SEC-MEM-013).

NOTE: The exact boto3 service surface for AgentCore Memory should be verified
against the shipped AWS SDK version.  The method names below follow the
bedrock-agentcore-runtime API shape documented at release; update them if the
service introduces breaking changes.
"""

from __future__ import annotations

from typing import Any


class AgentCoreMemoryClient:
    """boto3-backed AgentCore Memory client.

    Args:
        memory_id: AgentCore Memory resource identifier (AGENTCORE_MEMORY_ID).
        region:    AWS region.  ``None`` defers to boto3 default chain.
    """

    def __init__(self, memory_id: str, region: str | None = None) -> None:
        import boto3  # local import keeps module loadable without boto3 in test envs

        self._memory_id = memory_id
        kwargs: dict[str, Any] = {}
        if region:
            kwargs["region_name"] = region
        # TLS is managed by the boto3 SDK; never pass verify=False (SEC-MEM-011)
        self._client = boto3.client("bedrock-agentcore-runtime", **kwargs)

    # ── AgentCoreMemoryProtocol implementation ────────────────────────────────

    def put(self, namespace: str, key: str, value: str) -> None:
        """Persist ``value`` under ``namespace:key`` in AgentCore Memory."""
        self._client.create_memory_record(
            memoryId=self._memory_id,
            namespace=namespace,
            key=key,
            value=value,
        )

    def get(self, namespace: str, key: str) -> str | None:
        """Return the stored string, or ``None`` if the key does not exist."""
        try:
            response: dict[str, Any] = self._client.get_memory_record(
                memoryId=self._memory_id,
                namespace=namespace,
                key=key,
            )
            result: str | None = response.get("value")
            return result
        except self._client.exceptions.ResourceNotFoundException:
            return None
