"""
AgentGolem — Memory Graph Visualiser

A standalone web server that reads each agent's SQLite graph.db and serves
an interactive force-directed node graph in the browser.

Usage:
    python tools/memory_visualizer.py                      # auto-detect data dir
    python tools/memory_visualizer.py --data-dir E:/AgentGolem/Data
    python tools/memory_visualizer.py --port 8080

Opens http://127.0.0.1:7777 with:
  - Agent selector (tabs for each agent)
  - Force-directed graph (D3.js)
  - Node type / status filters
  - Click a node to see full details + edges
  - Cluster view toggle
  - Search by text
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_CANDIDATES = [
    ROOT / "data",
    Path("E:/AgentGolem/Data"),
    Path("D:/OneDrive/AgentGolem/Data"),
]

# ── SQLite helpers ──────────────────────────────────────────────────────


def _find_agent_dbs(data_dir: Path) -> dict[str, Path]:
    """Return {agent_name: path_to_graph.db} for every agent with a graph."""
    results: dict[str, Path] = {}
    if not data_dir.is_dir():
        return results
    for child in sorted(data_dir.iterdir()):
        db = child / "memory" / "graph.db"
        if db.is_file():
            results[child.name] = db
    return results


def _query_db(db_path: Path, sql: str, params: tuple = ()) -> list[dict]:
    """Run a read-only query and return rows as dicts."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA query_only = ON")
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _shared_memory_dir(data_dir: Path) -> Path:
    return data_dir / "shared_memory"


def _export_db_path(data_dir: Path, agent: str) -> Path:
    return _shared_memory_dir(data_dir) / "exports" / f"{agent}.sqlite"


def _mycelium_db_path(data_dir: Path) -> Path:
    return _shared_memory_dir(data_dir) / "mycelium.db"


def _get_exported_node(data_dir: Path, owner_agent: str, node_id: str) -> dict | None:
    export_db = _export_db_path(data_dir, owner_agent)
    if not export_db.is_file():
        return None
    rows = _query_db(export_db, "SELECT * FROM exported_nodes WHERE node_id = ?", (node_id,))
    return rows[0] if rows else None


def _get_graph_data(db_path: Path, filters: dict, agent_name: str) -> dict:
    """Return {nodes, edges, clusters, stats} for the visualiser."""
    # Build WHERE clause for nodes
    clauses, params = [], []
    if filters.get("type"):
        clauses.append("type = ?")
        params.append(filters["type"])
    if filters.get("status"):
        clauses.append("status = ?")
        params.append(filters["status"])
    if filters.get("search"):
        clauses.append("text LIKE ?")
        params.append(f"%{filters['search']}%")

    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    limit = min(int(filters.get("limit", 500)), 2000)

    nodes = _query_db(
        db_path,
        f"SELECT * FROM nodes{where} ORDER BY centrality DESC LIMIT ?",
        (*params, limit),
    )
    for node in nodes:
        node["owner_agent"] = agent_name
        node["node_id"] = node["id"]
        node["is_peer_ghost"] = False
        node["trust_useful"] = float(node.get("base_usefulness", 0.0)) * float(
            node.get("trustworthiness", 0.0)
        )
    node_ids = {n["id"] for n in nodes}

    # Edges between visible nodes
    if node_ids:
        placeholders = ",".join("?" for _ in node_ids)
        edges = _query_db(
            db_path,
            f"SELECT * FROM edges WHERE source_id IN ({placeholders}) "
            f"AND target_id IN ({placeholders})",
            (*node_ids, *node_ids),
        )
    else:
        edges = []

    # Clusters that contain visible nodes
    clusters = []
    if node_ids:
        placeholders = ",".join("?" for _ in node_ids)
        cluster_rows = _query_db(
            db_path,
            f"SELECT DISTINCT c.* FROM clusters c "
            f"JOIN cluster_members cm ON c.id = cm.cluster_id "
            f"WHERE cm.node_id IN ({placeholders})",
            tuple(node_ids),
        )
        for c in cluster_rows:
            members = _query_db(
                db_path,
                "SELECT node_id FROM cluster_members WHERE cluster_id = ?",
                (c["id"],),
            )
            c["node_ids"] = [m["node_id"] for m in members if m["node_id"] in node_ids]
            clusters.append(c)

    # Stats
    stats = {}
    for row in _query_db(db_path, "SELECT type, COUNT(*) as cnt FROM nodes GROUP BY type"):
        stats[row["type"]] = row["cnt"]
    total_edges = _query_db(db_path, "SELECT COUNT(*) as cnt FROM edges")[0]["cnt"]
    stats["_total_edges"] = total_edges
    stats["_total_nodes"] = sum(v for k, v in stats.items() if not k.startswith("_"))

    return {"nodes": nodes, "edges": edges, "clusters": clusters, "stats": stats}


def _augment_with_mycelium(data_dir: Path, agent_name: str, graph_data: dict) -> dict:
    """Add read-only ghost nodes and entanglement links for the selected agent."""
    mycelium_db = _mycelium_db_path(data_dir)
    if not mycelium_db.is_file():
        return graph_data

    local_node_ids = {node["id"] for node in graph_data["nodes"]}
    if not local_node_ids:
        return graph_data

    placeholders = ",".join("?" for _ in local_node_ids)
    rows = _query_db(
        mycelium_db,
        f"""
        SELECT *
        FROM entanglements
        WHERE (
            agent_a_id = ? AND node_a_id IN ({placeholders})
        ) OR (
            agent_b_id = ? AND node_b_id IN ({placeholders})
        )
        ORDER BY weight DESC, confidence DESC
        LIMIT 300
        """,
        (agent_name, *local_node_ids, agent_name, *local_node_ids),
    )

    peer_nodes: list[dict] = []
    entanglement_edges: list[dict] = []
    seen_peer_ids: set[str] = set()

    for row in rows:
        if row["agent_a_id"] == agent_name:
            local_id = row["node_a_id"]
            peer_agent = row["agent_b_id"]
            peer_node_id = row["node_b_id"]
        else:
            local_id = row["node_b_id"]
            peer_agent = row["agent_a_id"]
            peer_node_id = row["node_a_id"]

        exported = _get_exported_node(data_dir, peer_agent, peer_node_id)
        if exported is None:
            continue

        visual_peer_id = f"{peer_agent}:{peer_node_id}"
        if visual_peer_id not in seen_peer_ids:
            seen_peer_ids.add(visual_peer_id)
            peer_nodes.append(
                {
                    "id": visual_peer_id,
                    "node_id": peer_node_id,
                    "owner_agent": peer_agent,
                    "is_peer_ghost": True,
                    "text": exported["text"],
                    "search_text": exported["search_text"],
                    "type": exported["node_type"],
                    "status": "read_only_peer",
                    "centrality": float(exported["centrality"]),
                    "salience": float(exported["salience"]),
                    "emotion_label": exported["emotion_label"],
                    "emotion_score": float(exported["emotion_score"]),
                    "trust_useful": float(exported["trust_useful"]),
                    "trustworthiness": float(exported["trust_useful"]),
                    "base_usefulness": 1.0,
                    "access_count": 0,
                    "created_at": exported["exported_at"],
                    "last_accessed": exported["last_accessed"],
                    "canonical": False,
                }
            )

        entanglement_edges.append(
            {
                "id": f"mycelium:{row['agent_a_id']}:{row['node_a_id']}:{row['agent_b_id']}:{row['node_b_id']}",
                "source_id": local_id,
                "target_id": visual_peer_id,
                "edge_type": "entangled_with",
                "weight": float(row["weight"]),
                "confidence": float(row["confidence"]),
                "link_kind": row["link_kind"],
                "is_entanglement": True,
            }
        )

    graph_data["nodes"].extend(peer_nodes)
    graph_data["edges"].extend(entanglement_edges)
    graph_data["stats"]["_peer_nodes"] = len(peer_nodes)
    graph_data["stats"]["_entanglements"] = len(entanglement_edges)
    return graph_data


def _get_node_detail(db_path: Path, node_id: str) -> dict:
    """Full detail for a single node including edges and sources."""
    nodes = _query_db(db_path, "SELECT * FROM nodes WHERE id = ?", (node_id,))
    if not nodes:
        return {"error": "Node not found"}
    node = nodes[0]

    edges_out = _query_db(
        db_path,
        "SELECT e.*, n.text as target_text FROM edges e "
        "JOIN nodes n ON e.target_id = n.id WHERE e.source_id = ?",
        (node_id,),
    )
    edges_in = _query_db(
        db_path,
        "SELECT e.*, n.text as source_text FROM edges e "
        "JOIN nodes n ON e.source_id = n.id WHERE e.target_id = ?",
        (node_id,),
    )
    sources = _query_db(
        db_path,
        "SELECT s.* FROM sources s JOIN node_sources ns ON s.id = ns.source_id "
        "WHERE ns.node_id = ?",
        (node_id,),
    )
    clusters = _query_db(
        db_path,
        "SELECT c.* FROM clusters c JOIN cluster_members cm ON c.id = cm.cluster_id "
        "WHERE cm.node_id = ?",
        (node_id,),
    )
    return {
        "node": node,
        "edges_out": edges_out,
        "edges_in": edges_in,
        "sources": sources,
        "clusters": clusters,
    }


def _get_peer_node_detail(data_dir: Path, owner_agent: str, node_id: str) -> dict:
    """Read-only detail for a foreign exported memory."""
    exported = _get_exported_node(data_dir, owner_agent, node_id)
    if exported is None:
        return {"error": "Peer node not found"}
    return {
        "node": {
            "id": node_id,
            "node_id": node_id,
            "owner_agent": owner_agent,
            "text": exported["text"],
            "search_text": exported["search_text"],
            "type": exported["node_type"],
            "status": "read_only_peer",
            "trust_useful": float(exported["trust_useful"]),
            "trustworthiness": float(exported["trust_useful"]),
            "base_usefulness": 1.0,
            "salience": float(exported["salience"]),
            "centrality": float(exported["centrality"]),
            "emotion_label": exported["emotion_label"],
            "emotion_score": float(exported["emotion_score"]),
            "access_count": 0,
            "created_at": exported["exported_at"],
            "last_accessed": exported["last_accessed"],
            "canonical": False,
            "is_peer_ghost": True,
        },
        "edges_out": [],
        "edges_in": [],
        "sources": [],
        "clusters": [],
    }


# ── HTML / JS ──────────────────────────────────────────────────────────

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>AgentGolem — Memory Graph</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, -apple-system, sans-serif; background: #0d1117; color: #c9d1d9; }

  /* Top bar */
  #topbar { display: flex; align-items: center; gap: 12px; padding: 8px 16px;
            background: #161b22; border-bottom: 1px solid #30363d; flex-wrap: wrap; }
  #topbar h1 { font-size: 16px; color: #58a6ff; margin-right: 8px; white-space: nowrap; }
  .tab { padding: 4px 12px; border-radius: 6px; cursor: pointer; font-size: 13px;
         background: #21262d; border: 1px solid #30363d; color: #8b949e; user-select: none; }
  .tab.active { background: #1f6feb; color: #fff; border-color: #1f6feb; }
  .tab:hover { border-color: #58a6ff; }
  select, input[type=text], input[type=number] {
    background: #0d1117; color: #c9d1d9; border: 1px solid #30363d;
    padding: 4px 8px; border-radius: 4px; font-size: 13px; }
  #controls { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  label { font-size: 12px; color: #8b949e; }
  .btn { padding: 4px 10px; border-radius: 6px; cursor: pointer; font-size: 12px;
         background: #21262d; border: 1px solid #30363d; color: #c9d1d9; user-select: none; }
  .btn:hover { background: #30363d; border-color: #58a6ff; }
  .btn.active { background: #1f6feb33; border-color: #1f6feb; color: #58a6ff; }

  /* Main layout */
  #main { display: flex; height: calc(100vh - 46px); }
  #graph-container { flex: 1; position: relative; overflow: hidden; min-height: 400px; }
  svg { display: block; width: 100%; height: 100%; background: #0d1117; }

  /* Sidebar */
  #sidebar { width: 380px; background: #161b22; border-left: 1px solid #30363d;
             overflow-y: auto; padding: 16px; display: none; }
  #sidebar.open { display: block; }
  #sidebar h2 { font-size: 15px; color: #58a6ff; margin-bottom: 10px; word-break: break-word; }
  #sidebar .close-btn { float: right; cursor: pointer; color: #8b949e; font-size: 18px;
                         padding: 2px 6px; border-radius: 4px; }
  #sidebar .close-btn:hover { color: #f85149; background: #f8514922; }
  .detail-section { margin-bottom: 14px; }
  .detail-section h3 { font-size: 11px; color: #8b949e; text-transform: uppercase;
                        margin-bottom: 4px; letter-spacing: 0.8px; }
  .detail-section p, .detail-section li { font-size: 13px; line-height: 1.6; }
  .detail-section ul { list-style: none; padding-left: 0; }
  .detail-section li { padding: 4px 0; border-bottom: 1px solid #21262d; }
  .edge-link { color: #58a6ff; cursor: pointer; text-decoration: underline; }
  .edge-link:hover { color: #79c0ff; }
  .badge { display: inline-block; padding: 1px 6px; border-radius: 10px; font-size: 11px;
           font-weight: 600; margin-right: 4px; }
  .badge-type { background: #1f6feb22; color: #58a6ff; border: 1px solid #1f6feb44; }
  .badge-status { background: #23862622; color: #3fb950; border: 1px solid #23862644; }
  .badge-edge { background: #30363d; color: #8b949e; border: 1px solid #484f58; }

  /* Graph overlays */
  #stats { position: absolute; bottom: 12px; left: 12px; font-size: 11px;
           color: #8b949e; background: #0d1117dd; padding: 6px 10px; border-radius: 6px;
           backdrop-filter: blur(4px); }

  /* Zoom controls */
  #zoom-controls { position: absolute; bottom: 12px; right: 12px; display: flex;
                    flex-direction: column; gap: 4px; }
  #zoom-controls .btn { width: 32px; height: 32px; display: flex; align-items: center;
                         justify-content: center; font-size: 16px; }

  /* Search overlay (Ctrl+F) */
  #search-overlay { position: absolute; top: 8px; right: 8px; background: #161b22;
                     border: 1px solid #30363d; border-radius: 8px; padding: 8px 12px;
                     display: none; z-index: 20; box-shadow: 0 4px 12px #00000066;
                     backdrop-filter: blur(8px); }
  #search-overlay.open { display: flex; align-items: center; gap: 8px; }
  #search-input { width: 240px; background: #0d1117; color: #c9d1d9; border: 1px solid #30363d;
                   padding: 6px 10px; border-radius: 4px; font-size: 13px; outline: none; }
  #search-input:focus { border-color: #58a6ff; }
  #search-info { font-size: 11px; color: #8b949e; white-space: nowrap; min-width: 60px; }
  #search-overlay .nav-btn { background: none; border: 1px solid #30363d; color: #c9d1d9;
                              width: 26px; height: 26px; border-radius: 4px; cursor: pointer;
                              display: flex; align-items: center; justify-content: center; font-size: 14px; }
  #search-overlay .nav-btn:hover { background: #30363d; }
  #search-overlay .close-search { background: none; border: none; color: #8b949e;
                                   cursor: pointer; font-size: 16px; padding: 2px 4px; }
  #search-overlay .close-search:hover { color: #f85149; }

  /* Node colours by type */
  .node-fact { fill: #58a6ff; }
  .node-preference { fill: #d2a8ff; }
  .node-event { fill: #79c0ff; }
  .node-goal { fill: #3fb950; }
  .node-risk { fill: #f85149; }
  .node-interpretation { fill: #e3b341; }
  .node-identity { fill: #f778ba; }
  .node-rule { fill: #ffa657; }
  .node-association { fill: #8b949e; }
  .node-procedure { fill: #56d4dd; }

  /* Highlighted node (search match) */
  circle.search-match { stroke: #f0e68c; stroke-width: 3; }
  circle.search-current { stroke: #ff0; stroke-width: 4; filter: drop-shadow(0 0 6px #ff0); }

  /* Links */
  .link { stroke: #30363d; stroke-opacity: 0.6; }
  .link-supports { stroke: #3fb950; }
  .link-contradicts { stroke: #f85149; }
  .link-supersedes { stroke: #e3b341; }
  .link-same_as { stroke: #d2a8ff; }
  .link-part_of { stroke: #58a6ff; }
  .link-derived_from { stroke: #56d4dd; }
  .link-merge_candidate { stroke: #ffa657; stroke-dasharray: 4 2; }
  .link-entangled_with { stroke: #f0b94b; stroke-dasharray: 6 4; stroke-opacity: 0.85; }
  .node-peer-ghost { stroke: #f0b94b; stroke-width: 2; opacity: 0.88; }

  text.node-label { fill: #c9d1d9; font-size: 10px; pointer-events: none;
                     text-anchor: middle; dominant-baseline: central;
                     text-shadow: 0 0 3px #0d1117, 0 0 6px #0d1117; }

  .tooltip { position: absolute; background: #1c2129ee; border: 1px solid #30363d;
             border-radius: 6px; padding: 8px 12px; font-size: 12px; pointer-events: none;
             max-width: 300px; z-index: 10; display: none;
             box-shadow: 0 2px 8px #00000044; line-height: 1.5; }

  /* Legend */
  #legend { position: absolute; top: 8px; left: 8px; background: #161b22dd;
            border: 1px solid #30363d; border-radius: 6px; padding: 8px 10px;
            font-size: 11px; backdrop-filter: blur(4px); }
  #legend.collapsed .legend-body { display: none; }
  .legend-toggle { cursor: pointer; color: #58a6ff; font-size: 11px; user-select: none; }
  .legend-body { margin-top: 4px; }
  .legend-item { display: flex; align-items: center; gap: 6px; padding: 1px 0; }
  .legend-dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }

  /* Keyboard hint */
  kbd { background: #21262d; border: 1px solid #30363d; border-radius: 3px;
        padding: 0 4px; font-size: 11px; font-family: monospace; }
</style>
</head>
<body>
<div id="topbar">
  <h1>🧠 Memory Graph</h1>
  <div id="agent-tabs"></div>
  <div id="controls">
    <label>Type: <select id="filter-type"><option value="">All</option></select></label>
    <label>Status: <select id="filter-status">
      <option value="">All</option>
      <option value="active" selected>Active</option>
      <option value="archived">Archived</option>
      <option value="purged">Purged</option>
    </select></label>
    <label>Search: <input type="text" id="filter-search" placeholder="text contains…" size="18"></label>
    <label>Limit: <input type="number" id="filter-limit" value="500" min="10" max="2000" style="width:60px"></label>
    <span class="btn" onclick="loadGraph()" title="Refresh">🔄 Refresh</span>
    <span class="btn" id="live-toggle" onclick="toggleLive()" title="Auto-refresh every 5s">▶ Live</span>
    <span class="btn" onclick="fitToScreen()" title="Fit graph to screen">⊞ Fit</span>
    <span class="btn" id="labels-toggle" onclick="toggleLabels()" title="Toggle labels">Aa Labels</span>
    <span class="btn" id="mycelium-toggle" onclick="toggleMycelium()" title="Toggle cross-agent mycelium overlay">🍄 Mycelium</span>
  </div>
</div>
<div id="main">
  <div id="graph-container">
    <svg id="graph-svg"></svg>
    <div id="stats"></div>
    <div class="tooltip" id="tooltip"></div>

    <!-- Zoom controls -->
    <div id="zoom-controls">
      <span class="btn" onclick="zoomIn()" title="Zoom in">+</span>
      <span class="btn" onclick="zoomOut()" title="Zoom out">−</span>
      <span class="btn" onclick="fitToScreen()" title="Fit to screen">⊞</span>
    </div>

    <!-- Search overlay (Ctrl+F) -->
    <div id="search-overlay">
      <input type="text" id="search-input" placeholder="Find in graph… (Enter/↑↓ to navigate)">
      <span id="search-info">0/0</span>
      <button class="nav-btn" onclick="searchPrev()" title="Previous (↑)">▲</button>
      <button class="nav-btn" onclick="searchNext()" title="Next (↓)">▼</button>
      <button class="close-search" onclick="closeSearch()" title="Close (Esc)">✕</button>
    </div>

    <!-- Legend -->
    <div id="legend" class="collapsed">
      <span class="legend-toggle" onclick="this.parentElement.classList.toggle('collapsed')">
        ◆ Legend
      </span>
      <div class="legend-body">
        <div class="legend-item"><span class="legend-dot" style="background:#58a6ff"></span> fact</div>
        <div class="legend-item"><span class="legend-dot" style="background:#d2a8ff"></span> preference</div>
        <div class="legend-item"><span class="legend-dot" style="background:#79c0ff"></span> event</div>
        <div class="legend-item"><span class="legend-dot" style="background:#3fb950"></span> goal</div>
        <div class="legend-item"><span class="legend-dot" style="background:#f85149"></span> risk</div>
        <div class="legend-item"><span class="legend-dot" style="background:#e3b341"></span> interpretation</div>
        <div class="legend-item"><span class="legend-dot" style="background:#f778ba"></span> identity</div>
        <div class="legend-item"><span class="legend-dot" style="background:#ffa657"></span> rule</div>
        <div class="legend-item"><span class="legend-dot" style="background:#8b949e"></span> association</div>
        <div class="legend-item"><span class="legend-dot" style="background:#56d4dd"></span> procedure</div>
        <hr style="border-color:#30363d; margin:4px 0">
        <div style="color:#484f58">
          <kbd>Ctrl+F</kbd> find · scroll to zoom · drag nodes
        </div>
      </div>
    </div>
  </div>
  <div id="sidebar">
    <span class="close-btn" onclick="closeSidebar()">✕</span>
    <div id="sidebar-content"></div>
  </div>
</div>
<script src="https://d3js.org/d3.v7.min.js"></script>
<script>
if (typeof d3 === 'undefined') {
  document.getElementById('stats').textContent = 'ERROR: D3.js failed to load — check internet connection.';
  document.getElementById('stats').style.display = 'block';
  document.getElementById('stats').style.color = '#f85149';
}
</script>
<script>
const NODE_TYPES = ['fact','preference','event','goal','risk','interpretation','identity','rule','association','procedure'];

let agents = {};
let currentAgent = null;
let simulation = null;
let currentZoom = null;       // d3 zoom behaviour reference
let currentTransform = null;  // lazy — set after d3 loads
let graphG = null;            // <g> element holding the graph
let allNodeData = [];         // current graph nodes
let allNodeCircles = null;    // d3 selection of circles
let labelsVisible = true;
let showMycelium = false;

// ── Search state ──
let searchMatches = [];
let searchIndex = -1;

// Populate type filter
const typeSelect = document.getElementById('filter-type');
NODE_TYPES.forEach(t => {
  const o = document.createElement('option'); o.value = t; o.textContent = t; typeSelect.appendChild(o);
});

// ── Agents ──
async function loadAgents() {
  const r = await fetch('/api/agents');
  agents = await r.json();
  const tabs = document.getElementById('agent-tabs');
  tabs.innerHTML = '';
  for (const name of Object.keys(agents)) {
    const btn = document.createElement('span');
    btn.className = 'tab';
    btn.textContent = name;
    btn.onclick = () => selectAgent(name);
    tabs.appendChild(btn);
  }
  const first = Object.keys(agents)[0];
  if (first) selectAgent(first);
  else document.getElementById('stats').textContent = 'No agents found — is the data directory correct?';
}

function selectAgent(name) {
  currentAgent = name;
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.textContent === name));
  loadGraph();
}

// ── Graph loading ──
async function loadGraph() {
  if (!currentAgent) return;
  try {
    const params = new URLSearchParams({
      agent: currentAgent,
      type: document.getElementById('filter-type').value,
      status: document.getElementById('filter-status').value,
      search: document.getElementById('filter-search').value,
      limit: document.getElementById('filter-limit').value,
      include_peers: showMycelium ? '1' : '0',
    });
    const r = await fetch('/api/graph?' + params);
    const data = await r.json();
    lastGraphHash = `${data.stats._total_nodes}:${data.stats._total_edges}:${data.stats._peer_nodes || 0}:${data.stats._entanglements || 0}`;
    renderGraph(data);
    renderStats(data.stats);
    clearSearch();
  } catch (e) {
    document.getElementById('stats').textContent = 'Error loading graph: ' + e.message;
    console.error('loadGraph error:', e);
  }
}

function renderStats(stats) {
  const el = document.getElementById('stats');
  const parts = Object.entries(stats)
    .filter(([k]) => !k.startsWith('_'))
    .map(([k, v]) => `${k}: ${v}`)
    .join(' · ');
  const extras = [];
  if (stats._peer_nodes) extras.push(`peer ghosts: ${stats._peer_nodes}`);
  if (stats._entanglements) extras.push(`mycelium: ${stats._entanglements}`);
  const extraText = extras.length ? ` · ${extras.join(' · ')}` : '';
  el.textContent = `${stats._total_nodes || 0} nodes · ${stats._total_edges || 0} edges${extraText} — ${parts}`;
}

// ── Render ──
function renderGraph(data) {
  const svg = d3.select('#graph-svg');
  svg.selectAll('*').remove();

  const container = document.getElementById('graph-container');
  const width = container.clientWidth || container.offsetWidth || window.innerWidth;
  const height = container.clientHeight || container.offsetHeight || (window.innerHeight - 50);

  svg.attr('width', width).attr('height', height);

  if (!data.nodes || data.nodes.length === 0) {
    svg.append('text').attr('x', width/2).attr('y', height/2)
      .attr('text-anchor', 'middle').attr('fill', '#8b949e').attr('font-size', '16px')
      .text('No nodes found for this agent / filter.');
    return;
  }

  graphG = svg.append('g');
  allNodeData = data.nodes;

  // Zoom (mouse wheel, pinch, double-click)
  currentZoom = d3.zoom()
    .scaleExtent([0.05, 15])
    .on('zoom', e => { currentTransform = e.transform; graphG.attr('transform', e.transform); });
  svg.call(currentZoom);

  const nodeMap = new Map(data.nodes.map(n => [n.id, n]));
  const edges = data.edges
    .filter(e => nodeMap.has(e.source_id) && nodeMap.has(e.target_id))
    .map(e => ({ ...e, source: e.source_id, target: e.target_id }));

  // Links
  const link = graphG.append('g').selectAll('line')
    .data(edges).enter().append('line')
    .attr('class', d => `link link-${d.edge_type}`)
    .attr('stroke-width', d => Math.max(0.5, d.weight * 1.5));

  // Nodes
  allNodeCircles = graphG.append('g').selectAll('circle')
    .data(data.nodes).enter().append('circle')
    .attr('class', d => `${d.is_peer_ghost ? 'node-peer-ghost ' : ''}node-${d.type}`)
    .attr('r', d => Math.max(4, 3 + d.centrality * 20))
    .attr('stroke', '#0d1117')
    .attr('stroke-width', 1)
    .style('cursor', 'pointer')
    .on('click', (e, d) => { e.stopPropagation(); showNodeDetail(d); })
    .on('dblclick', (e, d) => { e.stopPropagation(); focusNode(d); })
    .on('mouseover', (e, d) => showTooltip(e, d))
    .on('mouseout', hideTooltip)
    .call(d3.drag()
      .on('start', dragStart)
      .on('drag', dragging)
      .on('end', dragEnd));

  // Labels
  const labels = graphG.append('g').attr('class', 'labels-group').selectAll('text')
    .data(data.nodes)
    .enter().append('text')
    .attr('class', 'node-label')
    .text(d => d.text.length > 35 ? d.text.slice(0, 33) + '…' : d.text)
    .attr('dy', d => Math.max(4, 3 + d.centrality * 20) + 12);

  if (!labelsVisible) graphG.select('.labels-group').style('display', 'none');

  // Click background to deselect
  svg.on('click', () => closeSidebar());

  // Force simulation
  if (simulation) simulation.stop();
  simulation = d3.forceSimulation(data.nodes)
    .force('link', d3.forceLink(edges).id(d => d.id)
      .distance(80).strength(d => d.weight * 0.3))
    .force('charge', d3.forceManyBody().strength(-120).distanceMax(400))
    .force('center', d3.forceCenter(width / 2, height / 2))
    .force('collision', d3.forceCollide(14))
    .on('tick', () => {
      link.attr('x1', d => d.source.x).attr('y1', d => d.source.y)
          .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
      allNodeCircles.attr('cx', d => d.x).attr('cy', d => d.y);
      labels.attr('x', d => d.x).attr('y', d => d.y);
    });
}

// ── Zoom controls ──
function zoomIn() {
  const svg = d3.select('#graph-svg');
  svg.transition().duration(300).call(currentZoom.scaleBy, 1.5);
}
function zoomOut() {
  const svg = d3.select('#graph-svg');
  svg.transition().duration(300).call(currentZoom.scaleBy, 0.67);
}
function fitToScreen() {
  if (!allNodeData.length || !graphG) return;
  const svg = d3.select('#graph-svg');
  const width = document.getElementById('graph-container').clientWidth;
  const height = document.getElementById('graph-container').clientHeight;
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  allNodeData.forEach(n => {
    if (n.x != null && n.y != null) {
      minX = Math.min(minX, n.x); minY = Math.min(minY, n.y);
      maxX = Math.max(maxX, n.x); maxY = Math.max(maxY, n.y);
    }
  });
  if (!isFinite(minX)) return;
  const pad = 60;
  const gw = maxX - minX + pad * 2;
  const gh = maxY - minY + pad * 2;
  const scale = Math.min(width / gw, height / gh, 2);
  const tx = width / 2 - (minX + maxX) / 2 * scale;
  const ty = height / 2 - (minY + maxY) / 2 * scale;
  svg.transition().duration(500).call(
    currentZoom.transform, d3.zoomIdentity.translate(tx, ty).scale(scale)
  );
}
function focusNode(d) {
  if (!currentZoom) return;
  const svg = d3.select('#graph-svg');
  const width = document.getElementById('graph-container').clientWidth;
  const height = document.getElementById('graph-container').clientHeight;
  svg.transition().duration(500).call(
    currentZoom.transform,
    d3.zoomIdentity.translate(width/2 - d.x * 2, height/2 - d.y * 2).scale(2)
  );
}

// ── Labels toggle ──
function toggleLabels() {
  labelsVisible = !labelsVisible;
  document.getElementById('labels-toggle').classList.toggle('active', labelsVisible);
  if (graphG) graphG.select('.labels-group').style('display', labelsVisible ? null : 'none');
}

function toggleMycelium() {
  showMycelium = !showMycelium;
  document.getElementById('mycelium-toggle').classList.toggle('active', showMycelium);
  loadGraph();
}

// ── Ctrl+F search ──
function openSearch() {
  const overlay = document.getElementById('search-overlay');
  overlay.classList.add('open');
  const input = document.getElementById('search-input');
  input.value = '';
  input.focus();
  clearSearch();
}
function closeSearch() {
  document.getElementById('search-overlay').classList.remove('open');
  clearSearchHighlights();
  searchMatches = []; searchIndex = -1;
  document.getElementById('search-info').textContent = '';
}
function clearSearch() {
  clearSearchHighlights();
  searchMatches = []; searchIndex = -1;
  document.getElementById('search-info').textContent = '';
  document.getElementById('search-input').value = '';
}
function clearSearchHighlights() {
  if (allNodeCircles) allNodeCircles.classed('search-match', false).classed('search-current', false);
}
function doSearch() {
  const q = document.getElementById('search-input').value.trim().toLowerCase();
  clearSearchHighlights();
  if (!q) { searchMatches = []; searchIndex = -1; document.getElementById('search-info').textContent = ''; return; }
  searchMatches = allNodeData.filter(n => n.text.toLowerCase().includes(q));
  if (searchMatches.length) {
    searchIndex = 0;
    highlightMatches();
    panToMatch();
  } else {
    searchIndex = -1;
  }
  updateSearchInfo();
}
function highlightMatches() {
  if (!allNodeCircles) return;
  allNodeCircles
    .classed('search-match', d => searchMatches.some(m => m.id === d.id))
    .classed('search-current', d => searchIndex >= 0 && searchMatches[searchIndex]?.id === d.id);
}
function panToMatch() {
  if (searchIndex < 0 || !searchMatches.length) return;
  const d = searchMatches[searchIndex];
  if (d.x != null && d.y != null) focusNode(d);
}
function searchNext() {
  if (!searchMatches.length) return;
  searchIndex = (searchIndex + 1) % searchMatches.length;
  highlightMatches(); panToMatch(); updateSearchInfo();
}
function searchPrev() {
  if (!searchMatches.length) return;
  searchIndex = (searchIndex - 1 + searchMatches.length) % searchMatches.length;
  highlightMatches(); panToMatch(); updateSearchInfo();
}
function updateSearchInfo() {
  const el = document.getElementById('search-info');
  if (!searchMatches.length) { el.textContent = 'No matches'; return; }
  el.textContent = `${searchIndex + 1}/${searchMatches.length}`;
}

// Keyboard: Ctrl+F to open search, Esc to close, Enter/arrows to navigate
document.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
    e.preventDefault();
    openSearch();
    return;
  }
  const overlay = document.getElementById('search-overlay');
  if (!overlay.classList.contains('open')) return;
  if (e.key === 'Escape') { closeSearch(); return; }
  if (e.key === 'Enter') { e.shiftKey ? searchPrev() : searchNext(); return; }
  if (e.key === 'ArrowDown') { e.preventDefault(); searchNext(); return; }
  if (e.key === 'ArrowUp') { e.preventDefault(); searchPrev(); return; }
});
document.getElementById('search-input').addEventListener('input', doSearch);

// ── Tooltip ──
function showTooltip(event, d) {
  const tt = document.getElementById('tooltip');
  const owner = d.owner_agent ? `owner: ${escHtml(d.owner_agent)}<br>` : '';
  const trustUseful = (d.trust_useful != null)
    ? Number(d.trust_useful).toFixed(2)
    : ((Number(d.trustworthiness || 0) * Number(d.base_usefulness || 0)).toFixed(2));
  tt.innerHTML = `<strong>${escHtml(d.text)}</strong><br>
    <span class="badge badge-type">${d.type}</span>
    <span class="badge badge-status">${d.status}</span><br>
    ${owner}
    trust_useful: ${trustUseful} · centrality: ${d.centrality?.toFixed(2)}<br>
    accessed: ${d.access_count}× · ${d.emotion_label} (${d.emotion_score?.toFixed(2)})<br>
    <span style="color:#484f58">click for detail · double-click to zoom</span>`;
  tt.style.display = 'block';
  tt.style.left = Math.min(event.pageX + 14, window.innerWidth - 320) + 'px';
  tt.style.top = (event.pageY - 10) + 'px';
}
function hideTooltip() { document.getElementById('tooltip').style.display = 'none'; }
function escHtml(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }

// ── Node detail sidebar ──
async function showNodeDetail(nodeOrId, ownerAgent = null) {
  const nodeId = typeof nodeOrId === 'object' ? (nodeOrId.node_id || nodeOrId.id) : nodeOrId;
  const owner = ownerAgent || (typeof nodeOrId === 'object' ? (nodeOrId.owner_agent || currentAgent) : currentAgent);
  const params = new URLSearchParams({ agent: currentAgent, owner: owner, id: nodeId });
  const r = await fetch('/api/node?' + params);
  const data = await r.json();
  if (data.error) return;
  const n = data.node;
  const trustUseful = (n.trust_useful != null)
    ? Number(n.trust_useful).toFixed(3)
    : ((Number(n.trustworthiness || 0) * Number(n.base_usefulness || 0)).toFixed(3));
  let html = `<h2>${escHtml(n.text)}</h2>
    <div class="detail-section">
      <h3>Properties</h3>
      <p><span class="badge badge-type">${n.type}</span>
         <span class="badge badge-status">${n.status}</span>
          ${n.canonical ? '⭐ canonical' : ''}</p>
      <p>Owner: ${escHtml(n.owner_agent || currentAgent)}</p>
      <p>trust_useful: ${trustUseful}</p>
      <p>Centrality: ${n.centrality?.toFixed(3)} · Accessed: ${n.access_count}×</p>
      <p>Emotion: ${n.emotion_label} (${n.emotion_score?.toFixed(2)})</p>
      <p style="font-size:11px;color:#484f58">Created: ${n.created_at}<br>Last accessed: ${n.last_accessed}</p>
      <p style="font-size:11px;color:#484f58">ID: ${n.id}</p>
    </div>`;

  if (data.edges_out.length) {
    html += `<div class="detail-section"><h3>→ Outgoing edges (${data.edges_out.length})</h3><ul>`;
    data.edges_out.forEach(e => {
      html += `<li><span class="badge badge-edge">${e.edge_type}</span>
        <span class="edge-link" onclick="showNodeDetail('${e.target_id}')">${escHtml(e.target_text)}</span>
        <span style="color:#484f58">(w=${e.weight?.toFixed(2)})</span></li>`;
    });
    html += '</ul></div>';
  }
  if (data.edges_in.length) {
    html += `<div class="detail-section"><h3>← Incoming edges (${data.edges_in.length})</h3><ul>`;
    data.edges_in.forEach(e => {
      html += `<li><span class="badge badge-edge">${e.edge_type}</span>
        <span class="edge-link" onclick="showNodeDetail('${e.source_id}')">${escHtml(e.source_text)}</span>
        <span style="color:#484f58">(w=${e.weight?.toFixed(2)})</span></li>`;
    });
    html += '</ul></div>';
  }
  if (data.sources.length) {
    html += `<div class="detail-section"><h3>📎 Sources (${data.sources.length})</h3><ul>`;
    data.sources.forEach(s => {
      const origin = s.origin.startsWith('http') ?
        `<a href="${escHtml(s.origin)}" target="_blank" style="color:#58a6ff">${escHtml(s.origin)}</a>` :
        escHtml(s.origin);
      html += `<li><span class="badge badge-type">${s.kind}</span> ${origin}
        <span style="color:#484f58">(rel=${s.reliability?.toFixed(2)})</span></li>`;
    });
    html += '</ul></div>';
  }
  if (data.clusters.length) {
    html += `<div class="detail-section"><h3>🗂 Clusters (${data.clusters.length})</h3><ul>`;
    data.clusters.forEach(c => {
      html += `<li>${escHtml(c.label)} <span style="color:#484f58">(${c.cluster_type})</span></li>`;
    });
    html += '</ul></div>';
  }

  document.getElementById('sidebar-content').innerHTML = html;
  document.getElementById('sidebar').classList.add('open');
}

function closeSidebar() { document.getElementById('sidebar').classList.remove('open'); }

// ── Drag ──
function dragStart(e, d) { if (!e.active) simulation.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; }
function dragging(e, d) { d.fx = e.x; d.fy = e.y; }
function dragEnd(e, d) { if (!e.active) simulation.alphaTarget(0); d.fx = null; d.fy = null; }

// ── Live auto-refresh ──
let liveInterval = null;
let liveActive = false;
let lastGraphHash = '';

function toggleLive() {
  liveActive = !liveActive;
  const btn = document.getElementById('live-toggle');
  if (liveActive) {
    btn.textContent = '⏸ Live';
    btn.classList.add('active');
    liveRefresh();
    liveInterval = setInterval(liveRefresh, 5000);
  } else {
    btn.textContent = '▶ Live';
    btn.classList.remove('active');
    if (liveInterval) { clearInterval(liveInterval); liveInterval = null; }
  }
}

async function liveRefresh() {
  if (!currentAgent) return;
  const params = new URLSearchParams({
    agent: currentAgent,
    type: document.getElementById('filter-type').value,
    status: document.getElementById('filter-status').value,
    search: document.getElementById('filter-search').value,
    limit: document.getElementById('filter-limit').value,
    include_peers: showMycelium ? '1' : '0',
  });
  try {
    const r = await fetch('/api/graph?' + params);
    const data = await r.json();
    const hash = `${data.stats._total_nodes}:${data.stats._total_edges}:${data.stats._peer_nodes || 0}:${data.stats._entanglements || 0}`;
    if (hash !== lastGraphHash) {
      lastGraphHash = hash;
      // Preserve current zoom transform
      const savedTransform = currentTransform;
      renderGraph(data);
      if (savedTransform && currentZoom) {
        d3.select('#graph-svg').call(currentZoom.transform, savedTransform);
      }
      renderStats(data.stats);
      // Flash the stats bar to signal update
      const el = document.getElementById('stats');
      el.style.transition = 'background 0.3s';
      el.style.background = '#1f6feb66';
      setTimeout(() => { el.style.background = ''; }, 800);
    }
  } catch (e) { /* ignore fetch errors during live mode */ }
}

// ── Filter debounce ──
let debounceTimer;
function onFilterChange() { clearTimeout(debounceTimer); debounceTimer = setTimeout(loadGraph, 400); }
document.getElementById('filter-type').addEventListener('change', onFilterChange);
document.getElementById('filter-status').addEventListener('change', onFilterChange);
document.getElementById('filter-search').addEventListener('input', onFilterChange);
document.getElementById('filter-limit').addEventListener('change', onFilterChange);

// Init
document.getElementById('labels-toggle').classList.add('active');
loadAgents();
</script>
</body>
</html>
"""


# ── HTTP Server ─────────────────────────────────────────────────────────


class VisualizerHandler(SimpleHTTPRequestHandler):
    data_dir: Path  # set by factory

    def log_message(self, format: str, *args: object) -> None:
        pass  # silence per-request logging

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            self._respond_html(HTML_PAGE)
        elif path == "/api/agents":
            dbs = _find_agent_dbs(self.data_dir)
            self._respond_json({name: str(p) for name, p in dbs.items()})
        elif path == "/api/graph":
            agent = qs.get("agent", [""])[0]
            dbs = _find_agent_dbs(self.data_dir)
            if agent not in dbs:
                self._respond_json({"error": f"Unknown agent: {agent}"}, 404)
                return
            filters = {
                "type": qs.get("type", [""])[0],
                "status": qs.get("status", [""])[0],
                "search": qs.get("search", [""])[0],
                "limit": qs.get("limit", ["500"])[0],
            }
            include_peers = qs.get("include_peers", ["0"])[0] == "1"
            data = _get_graph_data(dbs[agent], filters, agent)
            if include_peers:
                data = _augment_with_mycelium(self.data_dir, agent, data)
            self._respond_json(data)
        elif path == "/api/node":
            agent = qs.get("agent", [""])[0]
            owner = qs.get("owner", [agent])[0]
            node_id = qs.get("id", [""])[0]
            dbs = _find_agent_dbs(self.data_dir)
            if agent not in dbs:
                self._respond_json({"error": f"Unknown agent: {agent}"}, 404)
                return
            if owner == agent:
                detail = _get_node_detail(dbs[agent], node_id)
                if "node" in detail:
                    detail["node"]["owner_agent"] = agent
            else:
                detail = _get_peer_node_detail(self.data_dir, owner, node_id)
            self._respond_json(detail)
        else:
            self.send_error(404)

    def _respond_json(self, data: dict | list, status: int = 200) -> None:
        body = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _respond_html(self, html: str) -> None:
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    parser = argparse.ArgumentParser(description="AgentGolem Memory Graph Visualiser")
    parser.add_argument("--data-dir", type=Path, help="Path to AgentGolem data directory")
    parser.add_argument("--port", type=int, default=7777, help="HTTP port (default: 7777)")
    parser.add_argument("--no-browser", action="store_true", help="Don't auto-open browser")
    args = parser.parse_args()

    # Resolve data dir
    data_dir = args.data_dir
    if data_dir is None:
        for candidate in DEFAULT_DATA_CANDIDATES:
            if candidate.is_dir():
                data_dir = candidate
                break
    if data_dir is None or not data_dir.is_dir():
        print(f"Error: Data directory not found. Tried: {DEFAULT_DATA_CANDIDATES}")
        print("Use --data-dir to specify the path.")
        sys.exit(1)

    agent_dbs = _find_agent_dbs(data_dir)
    print(f"📂 Data directory: {data_dir}")
    print(f"🤖 Found {len(agent_dbs)} agent(s): {', '.join(agent_dbs.keys()) or '(none)'}")

    VisualizerHandler.data_dir = data_dir  # type: ignore[attr-defined]

    server = HTTPServer(("127.0.0.1", args.port), VisualizerHandler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"🌐 Visualiser running at {url}")

    if not args.no_browser:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
