"""Rubric engine: load, cache, and query the Frugal Architecture rubric."""

from __future__ import annotations

import importlib.resources
import os
from pathlib import Path

import yaml
from pydantic import ValidationError

from agent.rubric_core.models import (
    AntiPatternSignal,
    LawDefinition,
    Rubric,
    RubricLoadError,
    UnknownLawError,
)

MAX_RUBRIC_SIZE_BYTES: int = 1_048_576  # 1 MB — SR-RC-002

# ── Module-level state ────────────────────────────────────────────────────────
_loaded_rubric: Rubric | None = None
_loaded_path: Path | None = None
_rubric_mtime: float | None = None
_resolved_path: Path | None = None  # cached env/package path (SR-RC-003)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_resolved_path() -> Path:
    """Return the env/package-resolved path, computed once and cached (SR-RC-003)."""
    global _resolved_path
    if _resolved_path is None:
        env = os.environ.get("RUBRIC_PATH")
        if env:
            _resolved_path = Path(env).resolve()
        else:
            pkg_ref = importlib.resources.files(__package__).joinpath("rubric.yaml")
            _resolved_path = Path(str(pkg_ref))
    return _resolved_path


def _ensure_loaded() -> None:
    """Guard: ensure the rubric is loaded before any query call."""
    if _loaded_rubric is None:
        load_rubric()


def _reset_cache() -> None:
    """Reset all module-level state. Called between tests for isolation."""
    global _loaded_rubric, _loaded_path, _rubric_mtime, _resolved_path
    _loaded_rubric = None
    _loaded_path = None
    _rubric_mtime = None
    _resolved_path = None


# ── Public API ────────────────────────────────────────────────────────────────

def load_rubric(path: str | Path | None = None) -> Rubric:
    """
    Load, validate, and cache the Frugal Architecture rubric.

    Called once at agent startup before any user interaction. Subsequent
    calls return the cached instance unless the file's mtime changed,
    enabling live rubric updates without a code change (RULE-RUB-014).

    Args:
        path: explicit path override (useful in tests and for RUBRIC_PATH
              resolution). When None, resolves from RUBRIC_PATH env var or
              the package-bundled default.

    Returns:
        The loaded, validated, frozen Rubric instance.

    Raises:
        RubricLoadError: file missing, malformed YAML, or schema violation
                         (RULE-RUB-005 — hard startup gate).
    """
    global _loaded_rubric, _loaded_path, _rubric_mtime

    effective_path: Path = Path(path).resolve() if path is not None else _get_resolved_path()

    # Cache hit: same path, mtime unchanged (RULE-RUB-014)
    if _loaded_rubric is not None and _loaded_path == effective_path:
        try:
            current_mtime = effective_path.stat().st_mtime
        except OSError:
            current_mtime = None
        if current_mtime is not None and current_mtime == _rubric_mtime:
            return _loaded_rubric

    # ── Gate 1: file existence ───────────────────────────────────────────────
    if not effective_path.exists():
        raise RubricLoadError(f"rubric.yaml not found at {effective_path}")

    # ── Gate 2: file size cap (SR-RC-002) ────────────────────────────────────
    size = effective_path.stat().st_size
    if size > MAX_RUBRIC_SIZE_BYTES:
        raise RubricLoadError(
            f"rubric.yaml exceeds maximum allowed size of 1 MB ({size} bytes)"
        )

    # ── Gate 3: safe YAML parse (SR-RC-001) ──────────────────────────────────
    raw = effective_path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise RubricLoadError(f"rubric.yaml is not valid YAML: {exc}") from exc

    if not isinstance(data, dict):
        raise RubricLoadError("rubric.yaml must be a YAML mapping at the top level")

    # ── Gate 4: Pydantic schema validation (SR-RC-004 / SR-RC-005) ───────────
    try:
        rubric = Rubric.model_validate(data)
    except ValidationError as exc:
        raise RubricLoadError(str(exc)) from exc

    # ── Update cache ─────────────────────────────────────────────────────────
    _loaded_rubric = rubric
    _loaded_path = effective_path
    _rubric_mtime = effective_path.stat().st_mtime

    return rubric


def get_law(law_ref: int | str) -> LawDefinition:
    """
    Return the LawDefinition for the given reference.

    Args:
        law_ref: int (1–7), numeric string ("3"), or law title (exact or
                 substring match, case-insensitive).

    Returns:
        The matching LawDefinition.

    Raises:
        UnknownLawError: no law matches the reference (RULE-RUB-007).
    """
    _ensure_loaded()
    assert _loaded_rubric is not None  # narrow type for checker
    laws = _loaded_rubric.laws

    if isinstance(law_ref, int):
        for law in laws:
            if law.number == law_ref:
                return law
        raise UnknownLawError(f"No law matches {law_ref!r}. Valid law numbers are 1–7.")

    ref_str = str(law_ref).strip()

    if not ref_str:
        raise UnknownLawError(f"No law matches {law_ref!r}. Valid law numbers are 1–7.")

    # Purely numeric string → match by number
    if ref_str.isdigit():
        return get_law(int(ref_str))

    ref_lower = ref_str.lower()

    # Exact title match (case-insensitive)
    for law in laws:
        if law.title.strip().lower() == ref_lower:
            return law

    # Prefix / substring match ordered by law number
    for law in laws:
        if ref_lower in law.title.lower():
            return law

    raise UnknownLawError(f"No law matches {law_ref!r}. Valid law numbers are 1–7.")


def get_anti_pattern_signals() -> list[AntiPatternSignal]:
    """
    Return a shallow copy of all 10 anti-pattern signal definitions.

    The app-scanner calls this at scan initialisation to obtain the signal
    list. A shallow copy prevents callers from mutating the cached list;
    each AntiPatternSignal is frozen so field-level mutation is also blocked
    (SR-RC-005, RULE-RUB-011).
    """
    _ensure_loaded()
    assert _loaded_rubric is not None  # narrow type for checker
    return list(_loaded_rubric.anti_pattern_signals)


def validate_citation(law_number: int, law_title: str) -> bool:
    """
    Return True iff (law_number, law_title) exactly matches a rubric entry.

    Both number and lowercased/stripped title must match. A correct number
    with a wrong title, or vice versa, returns False (RULE-RUB-008).
    The scoring-engine is responsible for discarding the finding and raising
    RubricCitationError when this returns False.
    """
    _ensure_loaded()
    assert _loaded_rubric is not None  # narrow type for checker

    for law in _loaded_rubric.laws:
        if law.number == law_number:
            return law.title.strip().lower() == law_title.strip().lower()

    return False
