# Production multi-stage Dockerfile for the Frugal Architect Agent.
#
# Security controls enforced here (see security-design-project-scaffold):
#   CR-01  Non-root runtime user (UID 1000)
#   CR-02  Minimal base — python:3.12-slim only; no apt-get in final stage
#   CR-03  Multi-stage build — only installed packages copied to final image
#   CR-04  No secrets in layers — AGENT_MODEL_ID and all config via env vars
#   CR-06  Digest-pinned base image
#
# IMPORTANT: Replace <PINNED_DIGEST> with the current sha256 digest before
# publishing.  Obtain it with:
#   docker pull python:3.12-slim
#   docker inspect --format='{{index .RepoDigests 0}}' python:3.12-slim
#
# For multi-arch builds:
#   docker buildx build --platform linux/amd64,linux/arm64 \
#     -t <ecr-uri>/frugal-architect-agent:<git-sha> --push .

ARG PYTHON_DIGEST=sha256:REPLACE_WITH_PINNED_DIGEST

# ── Stage 1: builder ─────────────────────────────────────────────────────────
FROM python:3.12-slim@${PYTHON_DIGEST} AS builder

WORKDIR /build

# Install pip-tools so the lock file can be validated, then install runtime deps.
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install --require-hashes -r requirements.txt

# ── Stage 2: final runtime image ─────────────────────────────────────────────
FROM python:3.12-slim@${PYTHON_DIGEST}

# CR-01: dedicated non-root user
RUN groupadd -r agentcore && useradd -r -g agentcore -u 1000 agentcore

WORKDIR /app

# CR-03: copy only the installed packages from builder — no build tools in final layer
COPY --from=builder /install /usr/local

# Copy application source and the bundled rubric
COPY agent/ ./agent/
COPY entrypoint.py .
COPY agentcore.json .

# CR-01: switch to non-root before ENTRYPOINT
USER agentcore

# Expose no ports — AgentCore Runtime handles all networking
ENTRYPOINT ["python", "entrypoint.py"]
