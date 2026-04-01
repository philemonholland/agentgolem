"""Federated read-only retrieval over exported memory snapshots."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import aiosqlite

from agentgolem.memory.shared_exports import (
    ExportedMemory,
    _row_to_exported_memory,
    find_export_paths,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from agentgolem.memory.models import ConceptualNode
    from agentgolem.memory.mycelium import EntangledReference


@dataclass(frozen=True)
class FederatedMemory:
    """Hydrated foreign memory plus overlay metadata for prompt use."""

    agent_id: str
    agent_label: str
    node_id: str
    text: str
    search_text: str
    node_type: str
    trust_useful: float
    salience: float
    centrality: float
    emotion_label: str
    emotion_score: float
    source_hint: str = ""
    overlay_weight: float = 0.0


class FederatedMemoryRetriever:
    """Read-only search and hydration across foreign exported snapshots."""

    def __init__(self, exports_dir) -> None:
        self._exports_dir = exports_dir

    async def search_external(
        self,
        query: str,
        *,
        current_agent_id: str,
        top_k: int = 10,
    ) -> list[ExportedMemory]:
        """Search foreign exports for query-relevant memories."""
        paths = find_export_paths(self._exports_dir)
        combined: dict[tuple[str, str], ExportedMemory] = {}
        for agent_id, path in paths.items():
            if agent_id == current_agent_id:
                continue
            rows = await self._search_export(path, query, limit=top_k * 3)
            for row in rows:
                combined[(row.agent_id, row.node_id)] = row

        ranked = self._rank_exports(list(combined.values()), query)
        return ranked[:top_k]

    async def hydrate_entangled_refs(
        self,
        refs: Iterable[EntangledReference],
        *,
        query: str = "",
        top_k: int = 5,
    ) -> list[FederatedMemory]:
        """Hydrate overlay references from exported snapshots and rank them."""
        grouped: dict[str, list[EntangledReference]] = {}
        for ref in refs:
            grouped.setdefault(ref.reference.agent_id, []).append(ref)

        hydrated: list[FederatedMemory] = []
        paths = find_export_paths(self._exports_dir)
        for agent_id, entangled_refs in grouped.items():
            path = paths.get(agent_id)
            if path is None:
                continue
            node_ids = [ref.reference.node_id for ref in entangled_refs]
            placeholders = ", ".join("?" for _ in node_ids)
            async with aiosqlite.connect(path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    f"SELECT * FROM exported_nodes WHERE node_id IN ({placeholders})",
                    tuple(node_ids),
                ) as cur:
                    rows = await cur.fetchall()

            weights = {
                ref.reference.node_id: ref.weight
                for ref in entangled_refs
            }
            for row in rows:
                exported = _row_to_exported_memory(row)
                hydrated.append(
                    FederatedMemory(
                        agent_id=exported.agent_id,
                        agent_label=exported.agent_label,
                        node_id=exported.node_id,
                        text=exported.text,
                        search_text=exported.search_text,
                        node_type=exported.node_type,
                        trust_useful=exported.trust_useful,
                        salience=exported.salience,
                        centrality=exported.centrality,
                        emotion_label=exported.emotion_label,
                        emotion_score=exported.emotion_score,
                        source_hint=exported.source_hint,
                        overlay_weight=weights.get(exported.node_id, 0.0),
                    )
                )

        ranked = self._rank_federated_memories(hydrated, query)
        return ranked[:top_k]

    def build_query_from_local_nodes(
        self,
        local_nodes: list[ConceptualNode],
        *,
        max_nodes: int = 3,
        max_terms: int = 16,
    ) -> str:
        """Derive a bounded cross-agent query signature from local nodes."""
        chosen = sorted(
            local_nodes,
            key=lambda node: (
                node.salience,
                node.centrality,
                node.trust_useful,
                abs(node.emotion_score),
            ),
            reverse=True,
        )[:max_nodes]

        terms: list[str] = []
        for node in chosen:
            source = node.search_text or node.text
            for word in source.replace("\n", " ").split():
                cleaned = "".join(ch for ch in word.lower() if ch.isalnum())
                if len(cleaned) >= 4 and cleaned not in terms:
                    terms.append(cleaned)
                if len(terms) >= max_terms:
                    break
            if len(terms) >= max_terms:
                break
        return " ".join(terms)

    async def _search_export(
        self,
        export_path,
        query: str,
        *,
        limit: int,
    ) -> list[ExportedMemory]:
        words = [word.strip() for word in query.split() if len(word.strip()) >= 3]
        if not words:
            return []

        clauses = " OR ".join(
            "(text LIKE ? OR search_text LIKE ? OR source_hint LIKE ?)" for _ in words
        )
        params: list[str | int] = []
        for word in words:
            params.extend([f"%{word}%", f"%{word}%", f"%{word}%"])
        params.append(limit)

        async with aiosqlite.connect(export_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"""
                SELECT *
                FROM exported_nodes
                WHERE {clauses}
                ORDER BY trust_useful DESC, salience DESC, centrality DESC
                LIMIT ?
                """,
                tuple(params),
            ) as cur:
                rows = await cur.fetchall()
        return [_row_to_exported_memory(row) for row in rows]

    def _rank_exports(
        self,
        rows: list[ExportedMemory],
        query: str,
    ) -> list[ExportedMemory]:
        query_words = {word.lower() for word in query.split() if len(word) >= 3}
        scored: list[tuple[float, ExportedMemory]] = []
        for row in rows:
            searchable = f"{row.text} {row.search_text} {row.source_hint}".lower()
            match_score = sum(1 for word in query_words if word in searchable) / max(
                len(query_words), 1
            )
            score = (
                (0.45 * match_score)
                + (0.20 * row.trust_useful)
                + (0.15 * row.salience)
                + (0.10 * row.centrality)
                + (0.10 * abs(row.emotion_score))
            )
            scored.append((score, row))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [row for _, row in scored]

    def _rank_federated_memories(
        self,
        rows: list[FederatedMemory],
        query: str,
    ) -> list[FederatedMemory]:
        query_words = {word.lower() for word in query.split() if len(word) >= 3}
        scored: list[tuple[float, FederatedMemory]] = []
        for row in rows:
            searchable = f"{row.text} {row.search_text} {row.source_hint}".lower()
            match_score = sum(1 for word in query_words if word in searchable) / max(
                len(query_words), 1
            )
            overlay_score = min(row.overlay_weight / 2.0, 1.0)
            score = (
                (0.30 * match_score)
                + (0.30 * overlay_score)
                + (0.15 * row.trust_useful)
                + (0.10 * row.salience)
                + (0.05 * row.centrality)
                + (0.10 * abs(row.emotion_score))
            )
            scored.append((score, row))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [row for _, row in scored]
