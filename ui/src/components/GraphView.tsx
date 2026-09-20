import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { forceCenter, forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY, type SimulationLinkDatum, type SimulationNodeDatum } from "d3-force";
import type { GraphData, GraphEdge, GraphNode } from "../types";
import { Icon } from "./ui";

const W = 900;
const H = 560;
const KIND: Record<string, { color: string; r: number; label: string }> = {
  case: { color: "#5b9dff", r: 17, label: "Case" }, customer: { color: "#d5dbe8", r: 12, label: "Customer" }, card: { color: "#7d8fd1", r: 11, label: "Card" },
  txn: { color: "#8894a8", r: 7, label: "Transaction" }, device: { color: "#e9a23b", r: 14, label: "Device profile" }, cluster: { color: "#ef5b60", r: 19, label: "Connected cards" },
  prior: { color: "#a58bd6", r: 8, label: "Prior case" }, txncluster: { color: "#8894a8", r: 17, label: "Transactions" },
};
const isCluster = (kind: string) => kind === "cluster" || kind === "txncluster";

/** Presentation only: when a case has many affected transactions, fold them into one expandable node so the entities that matter (card, device, ring) stay readable. */
function prepare(data: GraphData): GraphData {
  const txns = data.nodes.filter((n) => n.kind === "txn" && !n.hidden);
  if (txns.length <= 6) return data;
  const id = "cluster:txns";
  const ids = new Set(txns.map((n) => n.id));
  const nodes: GraphNode[] = data.nodes.map((n) => (ids.has(n.id) ? { ...n, hidden: true, cluster: id } : n));
  nodes.push({ id, kind: "txncluster", label: `${txns.length} transactions`, detail: { count: txns.length, contains: "affected transactions" }, members: txns.map((n) => n.label) });
  const seen = new Set<string>();
  const extra: GraphEdge[] = [];
  for (const e of data.edges) {
    if (ids.has(e.target) && !ids.has(e.source) && !seen.has(e.source + e.rel)) {
      seen.add(e.source + e.rel);
      extra.push({ source: e.source, target: id, rel: e.rel });
    }
  }
  return { ...data, nodes, edges: [...data.edges, ...extra] };
}
const REL: Record<string, string> = { OWNS: "owns", MADE: "made", USED_DEVICE: "used device", SHARED_BY: "shared by", CASE_ON_CARD: "case on card", CASE_TXN: "case txn", CASE_CITES_DEVICE: "cites device", CASE_CONNECTED_TO: "connected to", SIMILAR_CASE: "similar case" };

interface P extends SimulationNodeDatum { id: string }

function layout(nodes: GraphNode[], edges: GraphEdge[], extra: [string, string][]): Map<string, { x: number; y: number }> {
  const pts: P[] = nodes.map((n) => ({ id: n.id }));
  const ids = new Set(nodes.map((n) => n.id));
  const links: SimulationLinkDatum<P>[] = [...edges.map((e) => [e.source, e.target] as [string, string]), ...extra].filter(([a, b]) => ids.has(a) && ids.has(b)).map(([source, target]) => ({ source, target }));
  const kind = new Map(nodes.map((n) => [n.id, n.kind]));
  const sim = forceSimulation(pts)
    .force("link", forceLink<P, SimulationLinkDatum<P>>(links).id((d) => d.id).distance((l) => {
      const a = kind.get((l.source as P).id), b = kind.get((l.target as P).id);
      return isCluster(a ?? "") || isCluster(b ?? "") ? 60 : a === "txn" || b === "txn" ? 46 : 74;
    }).strength(0.8))
    .force("charge", forceManyBody().strength(-150))
    .force("collide", forceCollide<P>().radius((d) => (KIND[kind.get(d.id) ?? "card"]?.r ?? 10) + 10))
    .force("x", forceX(0).strength(0.07)).force("y", forceY(0).strength(0.09)).force("center", forceCenter(0, 0)).stop();
  for (let i = 0; i < 320; i++) sim.tick();
  return new Map(pts.map((p) => [p.id, { x: p.x ?? 0, y: p.y ?? 0 }]));
}

export function GraphView({ data: raw, tall = false, title }: { data: GraphData; tall?: boolean; title?: string }) {
  const data = useMemo(() => prepare(raw), [raw]);
  // a shared-device ring is the point of the picture, so its connected cards start expanded (red links from the device)
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set(data.nodes.filter((n) => n.kind === "cluster" && data.edges.some((e) => e.ring && (e.source === n.id || e.target === n.id))).map((n) => n.id)));
  const [sel, setSel] = useState<string | null>(null);
  const [t, setT] = useState({ x: 0, y: 0, k: 1 });
  const svgRef = useRef<SVGSVGElement>(null);
  const drag = useRef<{ x: number; y: number; moved: boolean } | null>(null);
  const dragged = useRef(false);

  const visible = useMemo(() => data.nodes.filter((n) => !n.hidden || (n.cluster && expanded.has(n.cluster))), [data, expanded]);
  const ids = useMemo(() => new Set(visible.map((n) => n.id)), [visible]);
  const edges = useMemo(() => data.edges.filter((e) => ids.has(e.source) && ids.has(e.target)), [data, ids]);
  const extra = useMemo(() => visible.filter((n) => n.cluster).map((n) => [n.cluster as string, n.id] as [string, string]), [visible]);
  const pos = useMemo(() => layout(visible, edges, extra), [visible, edges, extra]);

  const fit = useCallback(() => {
    const xs = [...pos.values()].map((p) => p.x), ys = [...pos.values()].map((p) => p.y);
    if (!xs.length) return;
    const minX = Math.min(...xs) - 50, maxX = Math.max(...xs) + 50, minY = Math.min(...ys) - 50, maxY = Math.max(...ys) + 50;
    const k = Math.min(2.2, Math.min(W / (maxX - minX), H / (maxY - minY)));
    setT({ k, x: W / 2 - ((minX + maxX) / 2) * k, y: H / 2 - ((minY + maxY) / 2) * k });
  }, [pos]);
  useEffect(fit, [fit]);

  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    const wheel = (ev: WheelEvent) => {
      // the wheel zooms while the pointer is over the graph (Ctrl/Cmd pinch-zoom works too); scrolling anywhere else still scrolls the page
      ev.preventDefault();
      const r = el.getBoundingClientRect();
      const px = ((ev.clientX - r.left) / r.width) * W, py = ((ev.clientY - r.top) / r.height) * H;
      setT((c) => {
        const k = Math.max(0.25, Math.min(4, c.k * (ev.deltaY < 0 ? 1.12 : 1 / 1.12)));
        return { k, x: px - ((px - c.x) / c.k) * k, y: py - ((py - c.y) / c.k) * k };
      });
    };
    el.addEventListener("wheel", wheel, { passive: false });
    return () => el.removeEventListener("wheel", wheel);
  }, []);

  const zoom = (f: number) => setT((c) => { const k = Math.max(0.25, Math.min(4, c.k * f)); return { k, x: W / 2 - ((W / 2 - c.x) / c.k) * k, y: H / 2 - ((H / 2 - c.y) / c.k) * k }; });
  const down = (e: React.PointerEvent) => { drag.current = { x: e.clientX, y: e.clientY, moved: false }; (e.currentTarget as Element).setPointerCapture?.(e.pointerId); };
  const move = (e: React.PointerEvent) => {
    const d = drag.current, el = svgRef.current;
    if (!d || !el) return;
    const r = el.getBoundingClientRect(), dx = ((e.clientX - d.x) / r.width) * W, dy = ((e.clientY - d.y) / r.height) * H;
    if (Math.abs(dx) + Math.abs(dy) > 0.5) d.moved = true;
    d.x = e.clientX; d.y = e.clientY;
    setT((c) => ({ ...c, x: c.x + dx, y: c.y + dy }));
  };
  const up = () => { if (drag.current?.moved) dragged.current = true; drag.current = null; };

  const node = sel ? data.nodes.find((n) => n.id === sel) ?? null : null;
  const rels = node ? edges.filter((e) => e.source === node.id || e.target === node.id) : [];
  const label = (id: string) => data.nodes.find((n) => n.id === id)?.label ?? id;
  const toggleCluster = (id: string) => setExpanded((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  const txnCount = visible.filter((n) => n.kind === "txn").length;
  const labelled = (n: GraphNode) => n.id === sel || (n.kind !== "prior" && !(n.kind === "txn" && txnCount > 8) && !(n.kind === "card" && n.cluster));

  return (
    <div className={`graph-wrap ${tall ? "tall" : ""}`}>
      <div className="g-tools">
        <button className="btn" onClick={() => zoom(1.25)} aria-label="Zoom in"><Icon name="plus" /></button>
        <button className="btn" onClick={() => zoom(0.8)} aria-label="Zoom out"><Icon name="minus" /></button>
        <button className="btn" onClick={fit} aria-label="Fit graph to view"><Icon name="fit" /></button>
        {data.nodes.filter((n) => isCluster(n.kind)).map((c) => (
          <button className="btn" key={c.id} onClick={() => toggleCluster(c.id)} aria-pressed={expanded.has(c.id)}>
            {expanded.has(c.id) ? "Collapse" : "Expand"} {c.label}
          </button>
        ))}
      </div>
      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={title ?? "Relationship graph"} onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerLeave={up}
        onClick={() => { if (dragged.current) dragged.current = false; else setSel(null); }}>
        <g transform={`translate(${t.x} ${t.y}) scale(${t.k})`}>
          {edges.map((e, i) => {
            const a = pos.get(e.source), b = pos.get(e.target);
            if (!a || !b) return null;
            const dim = node && e.source !== node.id && e.target !== node.id;
            return (
              <g key={i}>
                <line className={`g-edge ${e.ring ? "ring" : ""} ${dim ? "dim" : ""}`} x1={a.x} y1={a.y} x2={b.x} y2={b.y} />
                {node && !dim && <text className="g-edge-label" x={(a.x + b.x) / 2} y={(a.y + b.y) / 2} textAnchor="middle">{REL[e.rel] ?? e.rel}</text>}
              </g>
            );
          })}
          {visible.map((n) => {
            const p = pos.get(n.id);
            if (!p) return null;
            const k = KIND[n.kind] ?? KIND.card;
            const ring = n.ring || n.kind === "cluster";
            return (
              <g key={n.id} className={`g-node ${n.id === sel ? "sel" : ""}`} transform={`translate(${p.x} ${p.y})`} data-testid={`node-${n.id}`} role="button" tabIndex={0} aria-label={`${k.label} ${n.label}`}
                onClick={(ev) => { ev.stopPropagation(); setSel(n.id); }} onDoubleClick={() => isCluster(n.kind) && toggleCluster(n.id)}
                onKeyDown={(ev) => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); setSel(n.id); } }}>
                {ring && <circle className="ring-pulse" r={k.r} fill="none" stroke="#ef5b60" strokeWidth={1.5} />}
                <circle r={k.r} fill={n.kind === "device" && !n.ring ? "#e9a23b" : k.color} fillOpacity={n.focus || n.kind === "cluster" ? 1 : 0.85} stroke="#0b0e14" strokeWidth={2} />
                {labelled(n) && <text y={k.r + 12} textAnchor="middle">{isCluster(n.kind) || n.kind === "device" ? n.label.slice(0, 22) : n.label}</text>}
              </g>
            );
          })}
        </g>
      </svg>
      {node && (
        <aside className="g-panel" aria-label="Node details">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <span className="faint" style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: ".07em" }}>{KIND[node.kind]?.label ?? node.kind}</span>
            <button className="btn ghost" style={{ height: 22, padding: "0 6px" }} onClick={() => setSel(null)} aria-label="Close details"><Icon name="x" size={12} /></button>
          </div>
          <h4>{node.label}</h4>
          <dl className="kv" style={{ gridTemplateColumns: "84px 1fr", marginTop: 8 }}>
            {Object.entries(node.detail ?? {}).map(([k, v]) => v == null ? null : <Fragment key={k}><dt>{k.replace(/_/g, " ")}</dt><dd>{String(v)}</dd></Fragment>)}
            <dt>Links</dt><dd>{rels.length} visible</dd>
          </dl>
          {isCluster(node.kind) && <button className="btn" style={{ marginTop: 10 }} onClick={() => toggleCluster(node.id)}>{expanded.has(node.id) ? "Collapse" : "Expand"} {node.label}</button>}
          {isCluster(node.kind) && node.members && <div className="row" style={{ gap: 4, marginTop: 8 }}>{node.members.slice(0, 6).map((m) => <span className="chip" key={m}>{m}</span>)}{node.members.length > 6 && <span className="faint">+{node.members.length - 6}</span>}</div>}
          {rels.length > 0 && (
            <ul className="list" style={{ paddingLeft: 16 }}>
              {rels.slice(0, 8).map((e, i) => <li key={i}>{REL[e.rel] ?? e.rel} · {label(e.source === node.id ? e.target : e.source)}</li>)}
              {rels.length > 8 && <li>+{rels.length - 8} more</li>}
            </ul>
          )}
        </aside>
      )}
      <div className="g-legend" aria-label="Legend">
        {data.legend.map((l) => <span key={l.kind}><i style={{ background: KIND[l.kind]?.color }} />{l.label}</span>)}
        <span><i style={{ background: "#ef5b60" }} />Shared-device link</span>
        <span className="faint">Scroll to zoom · drag to pan</span>
      </div>
    </div>
  );
}
