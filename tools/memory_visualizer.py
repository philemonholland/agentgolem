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
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _get_graph_data(db_path: Path, filters: dict) -> dict:
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


# ── HTML / JS ──────────────────────────────────────────────────────────

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>AgentGolem — Memory Graph</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: system-ui, -apple-system, sans-serif; background: #0d1117; color: #c9d1d9; }
  #topbar { display: flex; align-items: center; gap: 12px; padding: 8px 16px;
            background: #161b22; border-bottom: 1px solid #30363d; flex-wrap: wrap; }
  #topbar h1 { font-size: 16px; color: #58a6ff; margin-right: 12px; white-space: nowrap; }
  .tab { padding: 4px 12px; border-radius: 6px; cursor: pointer; font-size: 13px;
         background: #21262d; border: 1px solid #30363d; color: #8b949e; }
  .tab.active { background: #1f6feb; color: #fff; border-color: #1f6feb; }
  .tab:hover { border-color: #58a6ff; }
  select, input[type=text], input[type=number] {
    background: #0d1117; color: #c9d1d9; border: 1px solid #30363d;
    padding: 4px 8px; border-radius: 4px; font-size: 13px; }
  #controls { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  label { font-size: 12px; color: #8b949e; }
  #main { display: flex; height: calc(100vh - 46px); }
  #graph-container { flex: 1; position: relative; overflow: hidden; }
  svg { width: 100%; height: 100%; }
  #sidebar { width: 360px; background: #161b22; border-left: 1px solid #30363d;
             overflow-y: auto; padding: 12px; display: none; }
  #sidebar.open { display: block; }
  #sidebar h2 { font-size: 14px; color: #58a6ff; margin-bottom: 8px; }
  #sidebar .close-btn { float: right; cursor: pointer; color: #8b949e; font-size: 18px; }
  #sidebar .close-btn:hover { color: #f85149; }
  .detail-section { margin-bottom: 12px; }
  .detail-section h3 { font-size: 12px; color: #8b949e; text-transform: uppercase;
                        margin-bottom: 4px; letter-spacing: 0.5px; }
  .detail-section p, .detail-section li { font-size: 13px; line-height: 1.5; }
  .detail-section ul { list-style: none; padding-left: 0; }
  .detail-section li { padding: 2px 0; border-bottom: 1px solid #21262d; }
  .edge-link { color: #58a6ff; cursor: pointer; text-decoration: underline; }
  .badge { display: inline-block; padding: 1px 6px; border-radius: 10px; font-size: 11px;
           font-weight: 600; margin-right: 4px; }
  .badge-type { background: #1f6feb22; color: #58a6ff; border: 1px solid #1f6feb44; }
  .badge-status { background: #23862622; color: #3fb950; border: 1px solid #23862644; }
  #stats { position: absolute; bottom: 12px; left: 12px; font-size: 11px;
           color: #484f58; background: #0d1117cc; padding: 6px 10px; border-radius: 6px; }

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

  .link { stroke: #30363d; stroke-opacity: 0.6; }
  .link-supports { stroke: #3fb950; }
  .link-contradicts { stroke: #f85149; }
  .link-supersedes { stroke: #e3b341; }
  .link-same_as { stroke: #d2a8ff; }
  .link-part_of { stroke: #58a6ff; }
  .link-derived_from { stroke: #56d4dd; }
  .link-merge_candidate { stroke: #ffa657; stroke-dasharray: 4 2; }

  text.node-label { fill: #c9d1d9; font-size: 10px; pointer-events: none;
                     text-anchor: middle; dominant-baseline: central; }

  .tooltip { position: absolute; background: #1c2129; border: 1px solid #30363d;
             border-radius: 6px; padding: 8px 10px; font-size: 12px; pointer-events: none;
             max-width: 280px; z-index: 10; display: none; }
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
  </div>
</div>
<div id="main">
  <div id="graph-container">
    <svg id="graph-svg"></svg>
    <div id="stats"></div>
    <div class="tooltip" id="tooltip"></div>
  </div>
  <div id="sidebar">
    <span class="close-btn" onclick="closeSidebar()">✕</span>
    <div id="sidebar-content"></div>
  </div>
</div>
<script src="https://d3js.org/d3.v7.min.js"></script>
<script>
const NODE_TYPES = ['fact','preference','event','goal','risk','interpretation','identity','rule','association','procedure'];
const EDGE_TYPES = ['related_to','part_of','supports','contradicts','supersedes','same_as','merge_candidate','derived_from'];

let agents = {};
let currentAgent = null;
let simulation = null;

// Populate type filter
const typeSelect = document.getElementById('filter-type');
NODE_TYPES.forEach(t => {
  const o = document.createElement('option'); o.value = t; o.textContent = t; typeSelect.appendChild(o);
});

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
}

function selectAgent(name) {
  currentAgent = name;
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.textContent === name));
  loadGraph();
}

async function loadGraph() {
  if (!currentAgent) return;
  const params = new URLSearchParams({
    agent: currentAgent,
    type: document.getElementById('filter-type').value,
    status: document.getElementById('filter-status').value,
    search: document.getElementById('filter-search').value,
    limit: document.getElementById('filter-limit').value,
  });
  const r = await fetch('/api/graph?' + params);
  const data = await r.json();
  renderGraph(data);
  renderStats(data.stats);
}

function renderStats(stats) {
  const el = document.getElementById('stats');
  const parts = Object.entries(stats)
    .filter(([k]) => !k.startsWith('_'))
    .map(([k, v]) => `${k}: ${v}`)
    .join(' · ');
  el.textContent = `${stats._total_nodes || 0} nodes · ${stats._total_edges || 0} edges — ${parts}`;
}

function renderGraph(data) {
  const svg = d3.select('#graph-svg');
  svg.selectAll('*').remove();

  const width = document.getElementById('graph-container').clientWidth;
  const height = document.getElementById('graph-container').clientHeight;

  const g = svg.append('g');

  // Zoom
  svg.call(d3.zoom().scaleExtent([0.1, 8]).on('zoom', e => g.attr('transform', e.transform)));

  const nodeMap = new Map(data.nodes.map(n => [n.id, n]));

  // Filter edges to only those with both endpoints present
  const edges = data.edges.filter(e => nodeMap.has(e.source_id) && nodeMap.has(e.target_id));

  // Links
  const link = g.append('g').selectAll('line')
    .data(edges).enter().append('line')
    .attr('class', d => `link link-${d.edge_type}`)
    .attr('stroke-width', d => Math.max(0.5, d.weight * 1.5));

  // Nodes
  const node = g.append('g').selectAll('circle')
    .data(data.nodes).enter().append('circle')
    .attr('class', d => `node-${d.type}`)
    .attr('r', d => Math.max(4, 3 + d.centrality * 20))
    .attr('stroke', '#0d1117')
    .attr('stroke-width', 1)
    .style('cursor', 'pointer')
    .on('click', (e, d) => showNodeDetail(d.id))
    .on('mouseover', (e, d) => showTooltip(e, d))
    .on('mouseout', hideTooltip)
    .call(d3.drag()
      .on('start', dragStart)
      .on('drag', dragging)
      .on('end', dragEnd));

  // Labels for high-centrality nodes
  const labels = g.append('g').selectAll('text')
    .data(data.nodes.filter(n => n.centrality > 0.3 || data.nodes.length < 40))
    .enter().append('text')
    .attr('class', 'node-label')
    .text(d => d.text.length > 30 ? d.text.slice(0, 28) + '…' : d.text)
    .attr('dy', d => Math.max(4, 3 + d.centrality * 20) + 12);

  // Force simulation
  if (simulation) simulation.stop();
  simulation = d3.forceSimulation(data.nodes)
    .force('link', d3.forceLink(edges).id(d => d.id)
      .distance(80).strength(d => d.weight * 0.3))
    .force('charge', d3.forceManyBody().strength(-120))
    .force('center', d3.forceCenter(width / 2, height / 2))
    .force('collision', d3.forceCollide(12))
    .on('tick', () => {
      link.attr('x1', d => d.source.x).attr('y1', d => d.source.y)
          .attr('x2', d => d.target.x).attr('y2', d => d.target.y);
      node.attr('cx', d => d.x).attr('cy', d => d.y);
      labels.attr('x', d => d.x).attr('y', d => d.y);
    });

  // Map source_id/target_id to d3 node references
  edges.forEach(e => { e.source = e.source_id; e.target = e.target_id; });
  simulation.nodes(data.nodes);
  simulation.force('link').links(edges);
}

function showTooltip(event, d) {
  const tt = document.getElementById('tooltip');
  tt.innerHTML = `<strong>${d.text}</strong><br>
    <span class="badge badge-type">${d.type}</span>
    <span class="badge badge-status">${d.status}</span><br>
    trust: ${d.trustworthiness?.toFixed(2)} · useful: ${d.base_usefulness?.toFixed(2)} · centrality: ${d.centrality?.toFixed(2)}<br>
    accessed: ${d.access_count}× · ${d.emotion_label} (${d.emotion_score?.toFixed(2)})`;
  tt.style.display = 'block';
  tt.style.left = (event.pageX + 12) + 'px';
  tt.style.top = (event.pageY - 10) + 'px';
}
function hideTooltip() { document.getElementById('tooltip').style.display = 'none'; }

async function showNodeDetail(nodeId) {
  const r = await fetch(`/api/node?agent=${currentAgent}&id=${nodeId}`);
  const data = await r.json();
  if (data.error) return;
  const n = data.node;
  let html = `<h2>${n.text}</h2>
    <div class="detail-section">
      <h3>Properties</h3>
      <p><span class="badge badge-type">${n.type}</span>
         <span class="badge badge-status">${n.status}</span>
         ${n.canonical ? '⭐ canonical' : ''}</p>
      <p>Trust: ${n.trustworthiness?.toFixed(3)} · Usefulness: ${n.base_usefulness?.toFixed(3)}</p>
      <p>Centrality: ${n.centrality?.toFixed(3)} · Accessed: ${n.access_count}×</p>
      <p>Emotion: ${n.emotion_label} (${n.emotion_score?.toFixed(2)})</p>
      <p>Created: ${n.created_at}<br>Last accessed: ${n.last_accessed}</p>
    </div>`;

  if (data.edges_out.length) {
    html += `<div class="detail-section"><h3>Outgoing edges (${data.edges_out.length})</h3><ul>`;
    data.edges_out.forEach(e => {
      html += `<li><span class="badge badge-type">${e.edge_type}</span> →
        <span class="edge-link" onclick="showNodeDetail('${e.target_id}')">${e.target_text}</span>
        (w=${e.weight?.toFixed(2)})</li>`;
    });
    html += '</ul></div>';
  }
  if (data.edges_in.length) {
    html += `<div class="detail-section"><h3>Incoming edges (${data.edges_in.length})</h3><ul>`;
    data.edges_in.forEach(e => {
      html += `<li><span class="badge badge-type">${e.edge_type}</span> ←
        <span class="edge-link" onclick="showNodeDetail('${e.source_id}')">${e.source_text}</span>
        (w=${e.weight?.toFixed(2)})</li>`;
    });
    html += '</ul></div>';
  }
  if (data.sources.length) {
    html += `<div class="detail-section"><h3>Sources (${data.sources.length})</h3><ul>`;
    data.sources.forEach(s => {
      html += `<li>${s.kind}: ${s.origin} (rel=${s.reliability?.toFixed(2)})</li>`;
    });
    html += '</ul></div>';
  }
  if (data.clusters.length) {
    html += `<div class="detail-section"><h3>Clusters (${data.clusters.length})</h3><ul>`;
    data.clusters.forEach(c => {
      html += `<li>${c.label} (${c.cluster_type})</li>`;
    });
    html += '</ul></div>';
  }

  document.getElementById('sidebar-content').innerHTML = html;
  document.getElementById('sidebar').classList.add('open');
}

function closeSidebar() { document.getElementById('sidebar').classList.remove('open'); }

function dragStart(e, d) { if (!e.active) simulation.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; }
function dragging(e, d) { d.fx = e.x; d.fy = e.y; }
function dragEnd(e, d) { if (!e.active) simulation.alphaTarget(0); d.fx = null; d.fy = null; }

// Debounced reload on filter change
let debounceTimer;
function onFilterChange() { clearTimeout(debounceTimer); debounceTimer = setTimeout(loadGraph, 400); }
document.getElementById('filter-type').addEventListener('change', onFilterChange);
document.getElementById('filter-status').addEventListener('change', onFilterChange);
document.getElementById('filter-search').addEventListener('input', onFilterChange);
document.getElementById('filter-limit').addEventListener('change', onFilterChange);

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
            data = _get_graph_data(dbs[agent], filters)
            self._respond_json(data)
        elif path == "/api/node":
            agent = qs.get("agent", [""])[0]
            node_id = qs.get("id", [""])[0]
            dbs = _find_agent_dbs(self.data_dir)
            if agent not in dbs:
                self._respond_json({"error": f"Unknown agent: {agent}"}, 404)
                return
            detail = _get_node_detail(dbs[agent], node_id)
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
