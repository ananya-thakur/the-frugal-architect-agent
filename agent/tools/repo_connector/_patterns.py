"""
IaC tier classification for repo-connector.

File paths are classified as 'iac' or 'app' using deterministic,
path-only pattern matching (BR-RC-011 — no LLM, no content inspection).

Pattern groups match the specification in business-logic-model-repo-connector
§prioritise_files algorithm.  Evaluation order: filename patterns first,
then directory-segment patterns for YAML/YML files.
"""

from __future__ import annotations

import re
from typing import Literal

# ---------------------------------------------------------------------------
# Filename-level patterns — applied to the basename component of the path.
# Each pattern uses re.IGNORECASE; first match wins.
# ---------------------------------------------------------------------------
_FILENAME_PATTERNS: list[re.Pattern[str]] = [
    # Terraform: *.tf, *.tfvars
    re.compile(r"^.+\.(tf|tfvars)$", re.IGNORECASE),
    # CloudFormation / SAM: template.yaml or template.json
    re.compile(r"^template\.(yaml|json)$", re.IGNORECASE),
    # CDK project config
    re.compile(r"^cdk\.json$", re.IGNORECASE),
    re.compile(r"^cdk\.context\.json$", re.IGNORECASE),
    # CDK stacks: *_stack.py or *_stack.ts  (in any directory)
    re.compile(r"^.+_stack\.(py|ts)$", re.IGNORECASE),
    # CDK app entry point
    re.compile(r"^app\.py$", re.IGNORECASE),
    # Container config: Dockerfile* and docker-compose*.yml/yaml
    re.compile(r"^Dockerfile[^/]*$", re.IGNORECASE),
    re.compile(r"^docker-compose[^/]*\.(yml|yaml)$", re.IGNORECASE),
    # Serverless Framework
    re.compile(r"^serverless\.(yml|yaml)$", re.IGNORECASE),
]

# ---------------------------------------------------------------------------
# Directory-segment names that make *.yaml / *.yml files IaC.
# Checked only for files whose basename ends in .yaml or .yml.
# ---------------------------------------------------------------------------
_IAC_DIRECTORY_SEGMENTS: frozenset[str] = frozenset({
    # General IaC directories
    "infra",
    "infrastructure",
    "deploy",
    "k8s",
    "kubernetes",
    # Named IaC technology directories
    "cdk",
    "terraform",
    "cloudformation",
    "sam",
})


def classify_tier(path: str) -> Literal["iac", "app"]:
    """
    Classify a repository-relative file path as 'iac' or 'app'.

    The function is pure, deterministic, and side-effect-free.
    Same path always produces the same result (BR-RC-011).

    Args:
        path: Repository-relative file path (forward-slash or backslash).

    Returns:
        'iac' if any IaC pattern matches; 'app' otherwise.
    """
    # Normalise separators and case for matching
    normalised = path.replace("\\", "/")
    lower = normalised.lower()

    # Basename extraction (everything after the last '/')
    slash_idx = lower.rfind("/")
    basename = lower[slash_idx + 1:]

    # 1. Filename-level patterns (case-insensitive via re.IGNORECASE on basename)
    for pattern in _FILENAME_PATTERNS:
        if pattern.match(basename):
            return "iac"

    # 2. Directory-segment patterns — only for YAML/YML files
    if basename.endswith(".yaml") or basename.endswith(".yml"):
        # Extract directory segments (all path components except the filename)
        dir_part = lower[: slash_idx] if slash_idx >= 0 else ""
        segments = set(dir_part.split("/")) if dir_part else set()
        if segments & _IAC_DIRECTORY_SEGMENTS:
            return "iac"

    return "app"
