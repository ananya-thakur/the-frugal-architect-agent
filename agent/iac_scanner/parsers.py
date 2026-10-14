"""
Format-specific IaC parsers.

Each parser returns list[ParsedResource]. Per BR-06 parse errors are caught
per-file and recorded; they never propagate to abort sibling files.

Security controls applied throughout:
- yaml.safe_load() / yaml.safe_load_all() — no arbitrary code execution
- Regex patterns are anchored and length-capped (ReDoS mitigation)
- No subprocess calls; no path traversal (file.content is trusted pre-sanitized input)
- Attribute values that look like credentials are redacted in evidence strings
  (redaction happens at evidence rendering, not here — parsers return raw values)
"""

from __future__ import annotations

import ast
import json
import logging
import re
from typing import Any

import yaml

from agent.iac_scanner.models import File, IaCFormat, ParsedResource

log = logging.getLogger(__name__)

# ── Shared utilities ──────────────────────────────────────────────────────────

def _flatten(obj: Any, prefix: str = "", sep: str = ".") -> dict[str, Any]:
    """
    Recursively flatten a nested dict/list into dot-notation keys.

    Lists are flattened by numeric index: ``{"a": [1, 2]}`` → ``{"a.0": 1, "a.1": 2}``.
    Non-container leaf values are stored as-is.
    """
    result: dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            full_key = f"{prefix}{sep}{k}" if prefix else str(k)
            if isinstance(v, (dict, list)):
                result.update(_flatten(v, full_key, sep))
            result[full_key] = v
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            full_key = f"{prefix}{sep}{i}" if prefix else str(i)
            if isinstance(v, (dict, list)):
                result.update(_flatten(v, full_key, sep))
            result[full_key] = v
    return result


def _safe_parse_yaml(content: str, file_path: str) -> Any:
    """Attempt yaml.safe_load; raise ValueError with a clean message on failure."""
    try:
        return yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML parse error in {file_path}: {exc}") from exc


def _safe_parse_json(content: str, file_path: str) -> Any:
    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON parse error in {file_path}: {exc}") from exc


# ── CloudFormation / SAM parser ───────────────────────────────────────────────

def parse_cloudformation(file: File) -> list[ParsedResource]:
    """
    Parse a CloudFormation or SAM template (YAML or JSON).

    Returns one ParsedResource per entry in the Resources section.
    Raises ValueError on parse failure (caller records and continues per BR-06).
    """
    content = file.content.strip()
    if not content:
        return []

    if content.startswith("{"):
        data = _safe_parse_json(content, file.path)
    else:
        data = _safe_parse_yaml(content, file.path)

    if not isinstance(data, dict):
        raise ValueError(f"CloudFormation template root must be a mapping: {file.path}")

    resources = data.get("Resources")
    if not isinstance(resources, dict):
        return []

    result: list[ParsedResource] = []
    for logical_id, resource_def in resources.items():
        if not isinstance(resource_def, dict):
            continue
        resource_type = resource_def.get("Type", "")
        properties = resource_def.get("Properties") or {}
        attributes: dict[str, Any] = {}
        if isinstance(properties, dict):
            attributes = _flatten(properties, prefix="Properties")
        result.append(
            ParsedResource(
                id=logical_id,
                resource_type=resource_type,
                attributes=attributes,
                file_path=file.path,
                format=file.format,
                line_number=None,
            )
        )
    return result


# ── Terraform (regex-based HCL) parser ───────────────────────────────────────

# Matches: resource "TYPE" "NAME" { ... }
# We capture the block header; content is parsed separately.
_RE_TF_RESOURCE = re.compile(
    r'resource\s+"([^"]{1,256})"\s+"([^"]{1,256})"\s*\{',
    re.MULTILINE,
)
# Simple key = "value" or key = value or key = ["v"] patterns inside a block
_RE_TF_ATTR_STR = re.compile(r'^\s*(\w{1,128})\s*=\s*"([^"]{0,1024})"', re.MULTILINE)
_RE_TF_ATTR_NUM = re.compile(r'^\s*(\w{1,128})\s*=\s*(-?\d+(?:\.\d+)?)', re.MULTILINE)
_RE_TF_ATTR_BOOL = re.compile(r'^\s*(\w{1,128})\s*=\s*(true|false)', re.MULTILINE)
_RE_TF_ATTR_LIST = re.compile(
    r'^\s*(\w{1,128})\s*=\s*\[([^\]]{0,512})\]', re.MULTILINE
)
# Nested block: name { ... } (depth-1 only)
_RE_TF_NESTED_BLOCK = re.compile(
    r'^\s*(\w{1,128})\s*\{([^}]{0,2048})\}', re.MULTILINE | re.DOTALL
)


def _extract_tf_block_content(content: str, start_brace_pos: int) -> str:
    """Extract the text inside the outermost braces starting at start_brace_pos."""
    depth = 0
    i = start_brace_pos
    while i < len(content):
        if content[i] == "{":
            depth += 1
        elif content[i] == "}":
            depth -= 1
            if depth == 0:
                return content[start_brace_pos + 1 : i]
        i += 1
    return content[start_brace_pos + 1 :]


def _parse_tf_attributes(block_content: str) -> dict[str, Any]:
    """Extract key-value attributes from a Terraform block body."""
    attrs: dict[str, Any] = {}

    # String attrs
    for m in _RE_TF_ATTR_STR.finditer(block_content):
        attrs[m.group(1)] = m.group(2)

    # Numeric attrs (override string if same key - numeric is more specific)
    for m in _RE_TF_ATTR_NUM.finditer(block_content):
        key = m.group(1)
        if key not in attrs:
            val_str = m.group(2)
            val: int | float = float(val_str) if "." in val_str else int(val_str)
            attrs[key] = val

    # Bool attrs
    for m in _RE_TF_ATTR_BOOL.finditer(block_content):
        key = m.group(1)
        if key not in attrs:
            attrs[key] = m.group(2) == "true"

    # List attrs (extract first element as scalar if single value)
    for m in _RE_TF_ATTR_LIST.finditer(block_content):
        key = m.group(1)
        if key not in attrs:
            items_str = m.group(2).strip()
            items = [
                s.strip().strip('"').strip("'")
                for s in items_str.split(",")
                if s.strip()
            ]
            if len(items) == 1:
                attrs[key] = items[0]
            elif items:
                attrs[key] = items
                attrs[f"{key}.0"] = items[0]

    # Nested blocks (depth-1): produce key.sub_attr dot-notation
    for m in _RE_TF_NESTED_BLOCK.finditer(block_content):
        block_name = m.group(1)
        if block_name in attrs:
            continue
        nested_attrs = _parse_tf_attributes(m.group(2))
        for sub_k, sub_v in nested_attrs.items():
            attrs[f"{block_name}.{sub_k}"] = sub_v
        attrs[block_name] = nested_attrs

    return attrs


def parse_terraform(file: File) -> list[ParsedResource]:
    """
    Parse a .tf file using regex-based HCL extraction.

    Returns one ParsedResource per resource block.
    Raises ValueError on catastrophic parse failure.
    """
    content = file.content
    result: list[ParsedResource] = []

    for m in _RE_TF_RESOURCE.finditer(content):
        resource_type = m.group(1)
        resource_name = m.group(2)
        block_start = m.end() - 1  # position of the opening '{'
        block_content = _extract_tf_block_content(content, block_start)
        attrs = _parse_tf_attributes(block_content)
        resource_id = f"{resource_type}.{resource_name}"
        result.append(
            ParsedResource(
                id=resource_id,
                resource_type=resource_type,
                attributes=attrs,
                file_path=file.path,
                format=IaCFormat.TERRAFORM,
                line_number=content[: m.start()].count("\n") + 1,
            )
        )

    return result


# ── serverless.yml parser ─────────────────────────────────────────────────────

def parse_serverless(file: File) -> list[ParsedResource]:
    """
    Parse a serverless.yml/serverless.yaml file.

    Returns one ParsedResource per function, merging provider-level defaults.
    Raises ValueError on parse failure.
    """
    data = _safe_parse_yaml(file.content, file.path)
    if not isinstance(data, dict):
        raise ValueError(f"serverless.yml root must be a mapping: {file.path}")

    provider = data.get("provider") or {}
    functions = data.get("functions") or {}
    if not isinstance(functions, dict):
        return []

    # Provider-level defaults that apply to all functions
    provider_defaults: dict[str, Any] = {}
    for key in ("memorySize", "timeout", "runtime", "tracing", "region"):
        if key in provider:
            provider_defaults[key] = provider[key]

    result: list[ParsedResource] = []
    for fn_name, fn_config in functions.items():
        if not isinstance(fn_config, dict):
            fn_config = {}
        # Merge: function-level overrides provider defaults
        attrs: dict[str, Any] = {**provider_defaults, **fn_config}
        result.append(
            ParsedResource(
                id=fn_name,
                resource_type="serverless::function",
                attributes=attrs,
                file_path=file.path,
                format=IaCFormat.SERVERLESS_YML,
            )
        )
    return result


# ── CDK Python parser ─────────────────────────────────────────────────────────

def parse_cdk_python(file: File) -> list[ParsedResource]:
    """
    Parse a CDK Python file using the ast module.

    Extracts class instantiations of CDK constructs and their keyword arguments.
    Falls back gracefully when AST parsing fails.
    Raises ValueError on unrecoverable parse failures.
    """
    try:
        tree = ast.parse(file.content, filename=file.path)
    except SyntaxError as exc:
        raise ValueError(f"CDK Python AST parse error in {file.path}: {exc}") from exc

    result: list[ParsedResource] = []

    class _Visitor(ast.NodeVisitor):
        def visit_Call(self, node: ast.Call) -> None:
            # Look for: SomeClass(self, "LogicalId", kwarg=value, ...)
            class_name = _get_call_name(node)
            if not class_name:
                self.generic_visit(node)
                return

            # Try to extract the construct ID (second positional arg, usually a string)
            construct_id: str | None = None
            if len(node.args) >= 2:
                second = node.args[1]
                if isinstance(second, ast.Constant) and isinstance(second.value, str):
                    construct_id = second.value

            if construct_id is None:
                self.generic_visit(node)
                return

            # Extract keyword arguments as attributes
            attrs: dict[str, Any] = {}
            for kw in node.keywords:
                if kw.arg and isinstance(kw.value, ast.Constant):
                    attrs[kw.arg] = kw.value.value
                elif kw.arg and isinstance(kw.value, ast.Attribute):
                    # e.g. aws_lambda.Tracing.ACTIVE → "ACTIVE"
                    attrs[kw.arg] = kw.value.attr
                elif kw.arg:
                    # Store a placeholder to indicate the attribute is set
                    attrs[kw.arg] = _ast_to_repr(kw.value)

            result.append(
                ParsedResource(
                    id=construct_id,
                    resource_type=class_name,
                    attributes=attrs,
                    file_path=file.path,
                    format=IaCFormat.CDK_PYTHON,
                    line_number=node.lineno,
                )
            )
            self.generic_visit(node)

    _Visitor().visit(tree)
    return result


def _get_call_name(node: ast.Call) -> str | None:
    """Extract the callable name string from a Call node."""
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    if isinstance(node.func, ast.Name):
        return node.func.id
    return None


def _ast_to_repr(node: ast.expr) -> Any:
    """Convert an AST expression to a simple Python value or repr string."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple)):
        items = [_ast_to_repr(e) for e in node.elts]
        return items[0] if len(items) == 1 else items
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


# ── CDK TypeScript parser ─────────────────────────────────────────────────────

# Match: new SomeConstruct(scope, 'LogicalId', { key: value, ... })
_RE_CDK_TS_CONSTRUCT = re.compile(
    r"new\s+[\w.]{1,128}\(([^)]{0,2048})\)",
    re.DOTALL,
)
_RE_CDK_TS_STRING_PROP = re.compile(
    r"""(\w{1,128})\s*:\s*['"]([^'"]{0,512})['"]"""
)
_RE_CDK_TS_NUM_PROP = re.compile(r"(\w{1,128})\s*:\s*(-?\d+(?:\.\d+)?)")
_RE_CDK_TS_BOOL_PROP = re.compile(r"(\w{1,128})\s*:\s*(true|false)")
_RE_CDK_TS_ENUM_PROP = re.compile(r"(\w{1,128})\s*:\s*[\w.]{1,128}\.(\w{1,64})")
_RE_CDK_TS_LOGICAL_ID = re.compile(r"""['"]([A-Za-z0-9_\-]{1,128})['"]""")


def parse_cdk_typescript(file: File) -> list[ParsedResource]:
    """
    Parse CDK TypeScript constructs using regex extraction.

    Extracts 'new Construct(scope, id, props)' patterns.
    Raises ValueError on catastrophic failures.
    """
    result: list[ParsedResource] = []

    for m in _RE_CDK_TS_CONSTRUCT.finditer(file.content):
        args_str = m.group(1)
        # Attempt to find a string literal that looks like a construct ID
        logical_id_match = _RE_CDK_TS_LOGICAL_ID.search(args_str)
        if not logical_id_match:
            continue
        logical_id = logical_id_match.group(1)

        # Extract the class name from the match context
        before = file.content[: m.start()]
        # Look for the class name just before 'new'
        class_match = re.search(r"([\w.]{1,128})\s*$", before)
        class_name = class_match.group(1).split(".")[-1] if class_match else "Unknown"

        attrs: dict[str, Any] = {}
        for pm in _RE_CDK_TS_ENUM_PROP.finditer(args_str):
            attrs[pm.group(1)] = pm.group(2)
        for pm in _RE_CDK_TS_STRING_PROP.finditer(args_str):
            if pm.group(1) not in attrs:
                attrs[pm.group(1)] = pm.group(2)
        for pm in _RE_CDK_TS_NUM_PROP.finditer(args_str):
            key = pm.group(1)
            if key not in attrs:
                val_str = pm.group(2)
                attrs[key] = float(val_str) if "." in val_str else int(val_str)
        for pm in _RE_CDK_TS_BOOL_PROP.finditer(args_str):
            if pm.group(1) not in attrs:
                attrs[pm.group(1)] = pm.group(2) == "true"

        line_number = file.content[: m.start()].count("\n") + 1
        result.append(
            ParsedResource(
                id=logical_id,
                resource_type=class_name,
                attributes=attrs,
                file_path=file.path,
                format=IaCFormat.CDK_TYPESCRIPT,
                line_number=line_number,
            )
        )
    return result


# ── Kubernetes manifest parser ────────────────────────────────────────────────

_K8S_WORKLOAD_KINDS = frozenset(
    {"Deployment", "StatefulSet", "DaemonSet", "CronJob", "Job", "HorizontalPodAutoscaler"}
)


def parse_kubernetes(file: File) -> list[ParsedResource]:
    """
    Parse Kubernetes YAML manifests (single or multi-document).

    Returns one ParsedResource per workload resource. For Deployments/StatefulSets/
    DaemonSets the spec is flattened including container-level attributes.
    Raises ValueError on parse failure.
    """
    try:
        docs = list(yaml.safe_load_all(file.content))
    except yaml.YAMLError as exc:
        raise ValueError(f"Kubernetes YAML parse error in {file.path}: {exc}") from exc

    result: list[ParsedResource] = []
    for doc in docs:
        if not isinstance(doc, dict):
            continue
        kind = doc.get("kind", "")
        if kind not in _K8S_WORKLOAD_KINDS:
            continue
        metadata = doc.get("metadata") or {}
        name = metadata.get("name") or kind.lower()
        spec = doc.get("spec") or {}
        attrs = _flatten(spec, prefix="spec")

        # Also emit per-container resources if containers are present
        containers = (
            spec.get("template", {}).get("spec", {}).get("containers") or []
        )
        for i, container in enumerate(containers):
            if isinstance(container, dict):
                container_attrs = _flatten(container, prefix=f"spec.template.spec.containers.{i}")
                attrs.update(container_attrs)

        result.append(
            ParsedResource(
                id=name,
                resource_type=kind,
                attributes=attrs,
                file_path=file.path,
                format=IaCFormat.KUBERNETES,
            )
        )
    return result


# ── Dockerfile parser ─────────────────────────────────────────────────────────

_RE_DOCKERFILE_DIRECTIVE = re.compile(
    r"^(FROM|RUN|COPY|ADD|ENV|ARG|EXPOSE|ENTRYPOINT|CMD|WORKDIR|USER)\s+(.+)$",
    re.IGNORECASE | re.MULTILINE,
)
_RE_DOCKERFILE_FROM_AS = re.compile(r"^(.+?)\s+AS\s+\S+", re.IGNORECASE)


def parse_dockerfile(file: File) -> list[ParsedResource]:
    """
    Parse a Dockerfile and return a single ParsedResource representing the image.

    Raises ValueError on catastrophic parse failures.
    """
    directives: list[tuple[str, str]] = []
    for m in _RE_DOCKERFILE_DIRECTIVE.finditer(file.content):
        directives.append((m.group(1).upper(), m.group(2).strip()))

    if not directives:
        return []

    from_directives = [v for k, v in directives if k == "FROM"]
    from_count = len(from_directives)
    has_multistage = from_count > 1

    # Final stage base image (last FROM, strip AS clause)
    final_from = from_directives[-1] if from_directives else ""
    m = _RE_DOCKERFILE_FROM_AS.match(final_from)
    final_base = m.group(1).strip() if m else final_from

    attrs: dict[str, Any] = {
        "from_count": from_count,
        "from_final": final_base,
        "has_multistage": has_multistage,
    }
    # Add ENV directives as env.KEY
    for _, v in directives:
        if v.startswith("ENV ") or "=" in v:
            pass  # skip env details — they may contain secrets

    result = ParsedResource(
        id="image",
        resource_type="dockerfile::image",
        attributes=attrs,
        file_path=file.path,
        format=IaCFormat.DOCKERFILE,
    )
    return [result]


# ── Dispatcher ────────────────────────────────────────────────────────────────

def parse_file(file: File) -> list[ParsedResource]:
    """Dispatch to the correct parser based on file.format."""
    fmt = file.format
    if fmt in (IaCFormat.CLOUDFORMATION, IaCFormat.SAM):
        return parse_cloudformation(file)
    if fmt == IaCFormat.TERRAFORM:
        return parse_terraform(file)
    if fmt == IaCFormat.SERVERLESS_YML:
        return parse_serverless(file)
    if fmt == IaCFormat.CDK_PYTHON:
        return parse_cdk_python(file)
    if fmt == IaCFormat.CDK_TYPESCRIPT:
        return parse_cdk_typescript(file)
    if fmt == IaCFormat.KUBERNETES:
        return parse_kubernetes(file)
    if fmt == IaCFormat.DOCKERFILE:
        return parse_dockerfile(file)
    return []
