"""AgentGolem entry point."""

from __future__ import annotations


def main() -> None:
    """CLI entry point — delegates to the typer app."""
    from agentgolem.interaction.cli import app

    app()


if __name__ == "__main__":
    main()
