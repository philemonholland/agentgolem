"""Tests for the shared mycelium overlay store."""
from __future__ import annotations

from pathlib import Path

import pytest

from agentgolem.memory.mycelium import MemoryReference, MyceliumStore


@pytest.fixture
async def mycelium_store(tmp_path: Path) -> MyceliumStore:
    store = MyceliumStore(tmp_path / "shared_memory" / "mycelium.db")
    yield store
    await store.close()


async def test_upsert_entanglement_canonicalizes_and_reinforces(
    mycelium_store: MyceliumStore,
) -> None:
    ref_a = MemoryReference(agent_id="c2", node_id="local-node")
    ref_b = MemoryReference(agent_id="c1", node_id="peer-node")

    created = await mycelium_store.upsert_entanglement(
        ref_a,
        ref_b,
        weight_delta=0.3,
        confidence=0.4,
    )
    reinforced = await mycelium_store.upsert_entanglement(
        ref_b,
        ref_a,
        weight_delta=0.2,
        confidence=0.8,
    )

    assert created == 1
    assert reinforced == 1

    refs = await mycelium_store.get_entangled_refs_for_local_nodes(
        "c2",
        ["local-node"],
        limit=5,
    )
    assert len(refs) == 1
    assert refs[0].reference == MemoryReference(agent_id="c1", node_id="peer-node")
    assert refs[0].weight == pytest.approx(0.5)
    assert refs[0].confidence == pytest.approx(0.8)

    entanglements = await mycelium_store.get_entanglements_for_agent("c2")
    assert len(entanglements) == 1
    assert entanglements[0]["agent_a_id"] == "c1"
    assert entanglements[0]["node_a_id"] == "peer-node"
    assert entanglements[0]["agent_b_id"] == "c2"
    assert entanglements[0]["node_b_id"] == "local-node"


async def test_close_is_idempotent(mycelium_store: MyceliumStore) -> None:
    await mycelium_store.close()
    await mycelium_store.close()
