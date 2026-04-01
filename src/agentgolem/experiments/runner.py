"""Execution runner for fixed-budget self-improvement experiments."""
from __future__ import annotations

import asyncio
import inspect
import os
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from pydantic import BaseModel

from agentgolem.experiments.ledger import (
    ExperimentCommandOutcome,
    ExperimentCommandStatus,
    ExperimentLedger,
    ExperimentMetricObservation,
    ExperimentRunRecord,
)
from agentgolem.experiments.models import (
    ExperimentCommand,
    ExperimentStatus,
    ImprovementExperiment,
)
from agentgolem.logging.structured import get_logger

MetricCollector = Callable[
    [ImprovementExperiment, list[ExperimentCommandOutcome], Path],
    list[ExperimentMetricObservation] | Awaitable[list[ExperimentMetricObservation]],
]


class ExperimentRunResult(BaseModel):
    """Updated experiment snapshot plus the append-only run record."""

    experiment: ImprovementExperiment
    record: ExperimentRunRecord | None = None


class ExperimentRunner:
    """Run experiment evaluation commands within a bounded wall-clock budget."""

    def __init__(
        self,
        repo_root: Path,
        *,
        ledger: ExperimentLedger | None = None,
    ) -> None:
        self._repo_root = repo_root
        self._ledger = ledger
        self._logger = get_logger("experiments.runner")

    async def run(
        self,
        experiment: ImprovementExperiment,
        *,
        metric_collector: MetricCollector | None = None,
    ) -> ExperimentRunResult:
        """Execute the experiment's evaluation commands within the configured budget."""
        started_at = self._now_iso()
        running_experiment = experiment.model_copy(
            update={"status": ExperimentStatus.RUNNING, "started_at": started_at}
        )
        if self._ledger is not None:
            self._ledger.save_experiment(running_experiment)

        outcomes: list[ExperimentCommandOutcome] = []
        final_status = ExperimentStatus.EVALUATED
        overall_start = time.monotonic()

        for command in experiment.evaluation_commands:
            elapsed = time.monotonic() - overall_start
            remaining = experiment.budget.time_budget_seconds - elapsed
            if remaining <= 0:
                outcomes.append(
                    ExperimentCommandOutcome(
                        name=command.name,
                        status=ExperimentCommandStatus.TIMED_OUT,
                        duration_seconds=0.0,
                        summary="Experiment time budget exhausted before this command could start.",
                    )
                )
                final_status = ExperimentStatus.BLOCKED
                break

            outcome = await self._run_command(command, remaining, experiment)
            outcomes.append(outcome)

            if outcome.status == ExperimentCommandStatus.TIMED_OUT and command.required:
                final_status = ExperimentStatus.BLOCKED
                break
            if outcome.status == ExperimentCommandStatus.CRASHED and command.required:
                final_status = ExperimentStatus.CRASHED
                break
            if outcome.status == ExperimentCommandStatus.FAILED and command.required:
                final_status = ExperimentStatus.DISCARDED
                break

        metrics: list[ExperimentMetricObservation] = []
        if final_status == ExperimentStatus.EVALUATED and metric_collector is not None:
            metrics = await self._collect_metrics(metric_collector, experiment, outcomes)

        record = ExperimentRunRecord(
            experiment_id=experiment.id,
            recorded_by=experiment.proposed_by,
            status=final_status,
            baseline_ref=experiment.baseline_ref,
            candidate_ref=experiment.candidate_ref,
            metrics=metrics,
            command_outcomes=outcomes,
        )
        completed_experiment = experiment.model_copy(
            update={
                "status": final_status,
                "started_at": started_at,
                "completed_at": record.recorded_at,
            }
        )
        if self._ledger is not None:
            self._ledger.save_experiment(completed_experiment)
            self._ledger.append_record(record)

        self._logger.info(
            "experiment_run_complete",
            experiment_id=experiment.id,
            status=final_status.value,
            command_count=len(outcomes),
            metric_count=len(metrics),
        )
        return ExperimentRunResult(experiment=completed_experiment, record=record)

    async def _run_command(
        self,
        command: ExperimentCommand,
        remaining_budget_seconds: float,
        experiment: ImprovementExperiment,
    ) -> ExperimentCommandOutcome:
        timeout_seconds = min(
            command.timeout_seconds,
            experiment.budget.command_timeout_seconds,
            remaining_budget_seconds,
        )
        cwd = self._repo_root
        if command.working_directory:
            cwd = self._repo_root / command.working_directory

        start = time.monotonic()
        process = await asyncio.create_subprocess_shell(
            command.command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy(),
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        except TimeoutError:
            process.kill()
            stdout, stderr = await process.communicate()
            duration = time.monotonic() - start
            return ExperimentCommandOutcome(
                name=command.name,
                status=ExperimentCommandStatus.TIMED_OUT,
                duration_seconds=duration,
                summary=self._summarize_output(stdout, stderr, "Command timed out."),
            )
        except Exception as exc:
            process.kill()
            await process.communicate()
            duration = time.monotonic() - start
            return ExperimentCommandOutcome(
                name=command.name,
                status=ExperimentCommandStatus.CRASHED,
                duration_seconds=duration,
                summary=f"Command runner crashed: {exc!r}",
            )

        duration = time.monotonic() - start
        return_code = process.returncode
        if return_code == 0:
            status = ExperimentCommandStatus.PASSED
        else:
            status = ExperimentCommandStatus.FAILED
        return ExperimentCommandOutcome(
            name=command.name,
            status=status,
            duration_seconds=duration,
            exit_code=return_code,
            summary=self._summarize_output(stdout, stderr),
        )

    async def _collect_metrics(
        self,
        metric_collector: MetricCollector,
        experiment: ImprovementExperiment,
        outcomes: list[ExperimentCommandOutcome],
    ) -> list[ExperimentMetricObservation]:
        collected = metric_collector(experiment, outcomes, self._repo_root)
        if inspect.isawaitable(collected):
            return await collected
        return collected

    def _summarize_output(
        self,
        stdout: bytes | None,
        stderr: bytes | None,
        fallback: str = "Command completed.",
    ) -> str:
        chunks: list[str] = []
        stdout_text = (stdout or b"").decode("utf-8", errors="replace").strip()
        stderr_text = (stderr or b"").decode("utf-8", errors="replace").strip()
        if stdout_text:
            chunks.append(stdout_text.splitlines()[-1])
        if stderr_text:
            chunks.append(stderr_text.splitlines()[-1])
        if not chunks:
            return fallback
        return " | ".join(chunks)[:500]

    def _now_iso(self) -> str:
        from datetime import UTC, datetime

        return datetime.now(UTC).isoformat()
