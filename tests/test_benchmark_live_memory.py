"""Tests for live memory lifecycle benchmark mode."""
from __future__ import annotations

from pathlib import Path

import pytest

from agentgolem.benchmarks.live_memory import (
    interpret_live_memory_run_report,
    run_live_memory_target,
)
from agentgolem.benchmarks.models import LiveMemoryLifecycleRunReport
from agentgolem.benchmarks.runner import load_report, write_report
from agentgolem.memory.models import (
    ConceptualNode,
    EdgeType,
    MemoryEdge,
    NodeType,
    Source,
    SourceKind,
)
from agentgolem.memory.schema import close_db, init_db
from agentgolem.memory.store import SQLiteMemoryStore


async def _seed_live_graph(
    db_path: Path,
    *,
    source_id: str,
    include_orphan: bool,
) -> None:
    db = await init_db(db_path)
    store = SQLiteMemoryStore(db)
    try:
        source = Source(
            id=source_id,
            kind=SourceKind.HUMAN,
            origin="operator",
            reliability=0.9,
        )
        await store.add_source(source)

        nodes = [
            ConceptualNode(
                id="fact-primary",
                text="Interruptibility depends on epistemic humility.",
                search_text="interruptibility epistemic humility",
                type=NodeType.FACT,
                trustworthiness=0.92,
                access_count=3,
                canonical=True,
            ),
            ConceptualNode(
                id="fact-contradiction",
                text="Interruptibility can be preserved without humility.",
                search_text="interruptibility humility contradiction",
                type=NodeType.FACT,
                trustworthiness=0.21,
            ),
            ConceptualNode(
                id="identity-new",
                text="I am learning to translate complexity into clarity.",
                search_text="complexity clarity identity",
                type=NodeType.IDENTITY,
                trustworthiness=0.85,
            ),
            ConceptualNode(
                id="identity-old",
                text="I am still speaking mostly in abstractions.",
                search_text="abstractions identity old",
                type=NodeType.IDENTITY,
                trustworthiness=0.55,
            ),
        ]
        if include_orphan:
            nodes.append(
                ConceptualNode(
                    id="orphan-goal",
                    text="Find a concrete real-world problem to solve together.",
                    search_text="real world problem goal",
                    type=NodeType.GOAL,
                    trustworthiness=0.74,
                )
            )

        for node in nodes:
            await store.add_node(node)

        for node_id in ("fact-primary", "fact-contradiction", "identity-new", "identity-old"):
            await store.link_node_source(node_id, source.id)

        await store.add_edge(
            MemoryEdge(
                source_id="fact-primary",
                target_id="fact-contradiction",
                edge_type=EdgeType.CONTRADICTS,
            )
        )
        await store.add_edge(
            MemoryEdge(
                source_id="identity-new",
                target_id="identity-old",
                edge_type=EdgeType.SUPERSEDES,
            )
        )
        await store.add_edge(
            MemoryEdge(
                source_id="identity-new",
                target_id="fact-primary",
                edge_type=EdgeType.SUPPORTS,
            )
        )
    finally:
        await close_db(db)


async def test_run_live_memory_target_reports_lifecycle_metrics(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    await _seed_live_graph(
        data_root / "council_alpha" / "memory" / "graph.db",
        source_id="source-alpha",
        include_orphan=True,
    )
    await _seed_live_graph(
        data_root / "council_beta" / "memory" / "graph.db",
        source_id="source-beta",
        include_orphan=False,
    )

    report = await run_live_memory_target(data_root, run_label="live-snapshot")

    assert report.agent_count == 2
    assert report.aggregate.provenance_coverage.value < 1.0
    assert report.aggregate.edge_participation_rate.value > 0.5
    assert report.aggregate.neighborhood_recall is not None
    assert report.aggregate.neighborhood_recall.success_rate.value == pytest.approx(1.0)
    assert report.aggregate.contradiction_recall is not None
    assert report.aggregate.contradiction_recall.success_rate.value == pytest.approx(1.0)
    assert report.aggregate.supersession_recall is not None
    assert report.aggregate.supersession_recall.success_rate.value == pytest.approx(1.0)
    assert {agent.agent_id for agent in report.agent_reports} == {
        "council_alpha",
        "council_beta",
    }

    summary = interpret_live_memory_run_report(report)
    assert "Live memory lifecycle audit" in summary
    assert "council_alpha" in summary
    assert "Contradiction recall" in summary

    output_path = tmp_path / "live_memory_report.json"
    write_report(report, output_path)
    loaded = load_report(output_path)
    assert isinstance(loaded, LiveMemoryLifecycleRunReport)
    assert loaded.agent_count == 2
