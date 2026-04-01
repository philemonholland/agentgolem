"""Safety policy and resource locks for self-improvement experiments."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, field_validator

from agentgolem.experiments.models import ImprovementExperiment, normalize_repo_relative_path

if TYPE_CHECKING:
    from pathlib import Path

_DEFAULT_ALLOWED_COMMAND_PREFIXES = [
    ".venv\\Scripts\\python.exe -m pytest",
    ".venv\\Scripts\\python.exe -m ruff check",
    ".venv\\Scripts\\python.exe -m agentgolem.benchmarks",
    "python -m pytest",
    "python -m ruff check",
    "python -m agentgolem.benchmarks",
    "pytest",
    "ruff check",
]
_DEFAULT_PROTECTED_PATHS = [
    ".env",
    ".git",
    ".venv",
    "__pycache__",
    "autoresearch",
    "data",
    "tfv",
]
_DEFAULT_EXCLUSIVE_RESOURCES = ["experiment-runner"]


def _dedupe_strings(values: object) -> list[str]:
    if values is None:
        return []
    raw_values = [values] if isinstance(values, str) else [str(item) for item in values]

    normalized: list[str] = []
    for raw in raw_values:
        clean = raw.strip()
        if clean and clean not in normalized:
            normalized.append(clean)
    return normalized


def _normalize_command(command: str) -> str:
    return " ".join(command.strip().lower().split())


def _resource_lock_name(resource: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", resource.strip()).strip("-") or "resource"


class ExperimentGuardrailViolation(BaseModel):
    """One safety-policy violation detected during experiment preparation."""

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)

    @field_validator("code", "message")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return value.strip()


class ExperimentPolicy(BaseModel):
    """Guardrails for experiment commands, paths, and shared resources."""

    allowed_command_prefixes: list[str] = Field(
        default_factory=lambda: list(_DEFAULT_ALLOWED_COMMAND_PREFIXES)
    )
    protected_paths: list[str] = Field(default_factory=lambda: list(_DEFAULT_PROTECTED_PATHS))
    default_exclusive_resources: list[str] = Field(
        default_factory=lambda: list(_DEFAULT_EXCLUSIVE_RESOURCES)
    )

    @field_validator("allowed_command_prefixes", "default_exclusive_resources", mode="before")
    @classmethod
    def _normalize_string_lists(cls, value: object) -> list[str]:
        return _dedupe_strings(value)

    @field_validator("protected_paths", mode="before")
    @classmethod
    def _normalize_paths(cls, value: object) -> list[str]:
        raw_paths = _dedupe_strings(value)
        return [normalize_repo_relative_path(path) for path in raw_paths]

    def apply_defaults(self, experiment: ImprovementExperiment) -> ImprovementExperiment:
        """Inject default exclusive resources into an experiment budget."""
        merged_resources = list(self.default_exclusive_resources)
        for resource in experiment.budget.exclusive_resources:
            if resource not in merged_resources:
                merged_resources.append(resource)
        if merged_resources == experiment.budget.exclusive_resources:
            return experiment
        return experiment.model_copy(
            update={
                "budget": experiment.budget.model_copy(
                    update={"exclusive_resources": merged_resources}
                )
            }
        )

    def validate(self, experiment: ImprovementExperiment) -> list[ExperimentGuardrailViolation]:
        """Return all policy violations for *experiment*."""
        violations: list[ExperimentGuardrailViolation] = []
        prepared = self.apply_defaults(experiment)

        for command in prepared.evaluation_commands:
            if not self._command_allowed(command.command):
                allowed = ", ".join(self.allowed_command_prefixes)
                violations.append(
                    ExperimentGuardrailViolation(
                        code="command_not_allowlisted",
                        message=(
                            f"Evaluation command '{command.name}' is not allowlisted. "
                            f"Allowed prefixes: {allowed}"
                        ),
                    )
                )
            if command.working_directory and self._is_protected_path(command.working_directory):
                violations.append(
                    ExperimentGuardrailViolation(
                        code="protected_working_directory",
                        message=(
                            f"Evaluation command '{command.name}' uses protected working directory "
                            f"'{command.working_directory}'."
                        ),
                    )
                )

        for change in prepared.candidate_changes:
            if not prepared.scope.allows(change.file_path, protected_paths=self.protected_paths):
                violations.append(
                    ExperimentGuardrailViolation(
                        code="change_outside_scope",
                        message=(
                            f"Candidate change '{change.file_path}' is outside "
                            "the allowed scope or touches a protected path."
                        ),
                    )
                )

        return violations

    def _command_allowed(self, command: str) -> bool:
        normalized = _normalize_command(command)
        return any(
            normalized.startswith(_normalize_command(prefix))
            for prefix in self.allowed_command_prefixes
        )

    def _is_protected_path(self, rel_path: str) -> bool:
        clean = normalize_repo_relative_path(rel_path)
        return any(clean == path or clean.startswith(f"{path}/") for path in self.protected_paths)


@dataclass(frozen=True, slots=True)
class ExperimentResourceLease:
    """Exclusive resource lock lease for a running experiment."""

    experiment_id: str
    resources: tuple[str, ...]
    lock_paths: tuple[Path, ...]


class ExperimentResourceManager:
    """File-based exclusive locking for shared experiment resources."""

    def __init__(self, data_dir: Path) -> None:
        self._locks_dir = data_dir / "experiments" / "locks"
        self._locks_dir.mkdir(parents=True, exist_ok=True)

    def acquire(self, experiment_id: str, resources: list[str]) -> ExperimentResourceLease | None:
        """Acquire all requested resources, or return ``None`` if any are busy."""
        normalized = tuple(_dedupe_strings(resources))
        acquired: list[Path] = []
        for resource in normalized:
            lock_path = self._locks_dir / f"{_resource_lock_name(resource)}.json"
            payload = {"experiment_id": experiment_id, "resource": resource}
            try:
                with lock_path.open("x", encoding="utf-8") as handle:
                    json.dump(payload, handle, indent=2)
            except FileExistsError:
                self.release(ExperimentResourceLease(experiment_id, normalized, tuple(acquired)))
                return None
            acquired.append(lock_path)
        return ExperimentResourceLease(experiment_id, normalized, tuple(acquired))

    def release(self, lease: ExperimentResourceLease | None) -> None:
        """Release all files associated with a prior lease."""
        if lease is None:
            return
        for path in lease.lock_paths:
            path.unlink(missing_ok=True)
