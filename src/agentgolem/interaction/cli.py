"""CLI control surface for AgentGolem."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from agentgolem.runtime.state import AgentMode

app = typer.Typer(name="agentgolem", help="AgentGolem — persistent autonomous agent")
console = Console()


def _get_data_dir() -> Path:
    """Get data directory from settings."""
    from agentgolem.config import get_settings

    return get_settings().data_dir


def _get_runtime_state() -> "RuntimeState":  # noqa: F821
    from agentgolem.runtime.state import RuntimeState

    return RuntimeState(_get_data_dir())


@app.command()
def run() -> None:
    """Start the agent main loop."""
    from agentgolem.config import get_settings, get_secrets
    from agentgolem.logging import setup_logging

    settings = get_settings()
    secrets = get_secrets()
    setup_logging(settings.log_level, settings.data_dir, secrets)

    console.print("[bold green]AgentGolem starting...[/bold green]")

    from agentgolem.runtime.loop import MainLoop

    loop = MainLoop(settings=settings, secrets=secrets)
    try:
        asyncio.run(loop.run())
    except KeyboardInterrupt:
        console.print("\n[bold yellow]Shutting down...[/bold yellow]")


@app.command()
def status() -> None:
    """Show agent status."""
    state = _get_runtime_state()
    table = Table(title="Agent Status")
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="green")
    info = state.to_dict()
    for key, value in info.items():
        table.add_row(key, str(value))
    console.print(table)


@app.command()
def wake() -> None:
    """Wake the agent."""
    state = _get_runtime_state()
    asyncio.run(state.transition(AgentMode.AWAKE))
    console.print("[green]Agent set to AWAKE[/green]")


@app.command()
def sleep_cmd() -> None:
    """Put the agent to sleep."""
    state = _get_runtime_state()
    asyncio.run(state.transition(AgentMode.ASLEEP))
    console.print("[yellow]Agent set to ASLEEP[/yellow]")


@app.command()
def pause() -> None:
    """Pause the agent."""
    state = _get_runtime_state()
    asyncio.run(state.transition(AgentMode.PAUSED))
    console.print("[yellow]Agent set to PAUSED[/yellow]")


@app.command()
def resume() -> None:
    """Resume the agent (set to AWAKE)."""
    state = _get_runtime_state()
    asyncio.run(state.transition(AgentMode.AWAKE))
    console.print("[green]Agent RESUMED (AWAKE)[/green]")


@app.command(name="inspect-soul")
def inspect_soul() -> None:
    """Display current soul.md."""
    soul_path = Path("soul.md")
    if soul_path.exists():
        console.print(soul_path.read_text(encoding="utf-8"))
    else:
        console.print("[red]soul.md not found[/red]")


@app.command(name="inspect-heartbeat")
def inspect_heartbeat() -> None:
    """Display current heartbeat.md."""
    hb_path = Path("heartbeat.md")
    if hb_path.exists():
        console.print(hb_path.read_text(encoding="utf-8"))
    else:
        console.print("[red]heartbeat.md not found[/red]")


@app.command(name="inspect-logs")
def inspect_logs(tail: int = typer.Option(20, help="Number of recent entries")) -> None:
    """Show recent activity log entries."""
    log_path = _get_data_dir() / "logs" / "activity.jsonl"
    if not log_path.exists():
        console.print("[yellow]No activity log yet.[/yellow]")
        return
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    for line in lines[-tail:]:
        try:
            entry = json.loads(line)
            console.print(json.dumps(entry, indent=2))
        except json.JSONDecodeError:
            console.print(line)


@app.command(name="inspect-pending")
def inspect_pending() -> None:
    """Show pending tasks."""
    state = _get_runtime_state()
    if state.pending_tasks:
        for i, task in enumerate(state.pending_tasks, 1):
            console.print(f"  {i}. {task}")
    else:
        console.print("[dim]No pending tasks.[/dim]")


@app.command()
def message(text: str = typer.Argument(..., help="Message to send to the agent")) -> None:
    """Send a message to the agent."""
    msg_dir = _get_data_dir() / "inbox"
    msg_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    msg_file = msg_dir / f"human_{timestamp}.json"
    msg_file.write_text(json.dumps({"text": text, "timestamp": timestamp}))
    console.print("[green]Message queued.[/green]")
