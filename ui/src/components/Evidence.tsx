import { useMemo, useState } from "react";
import type { CaseDetail, EvidenceItem } from "../types";
import { ratingTone } from "../format";
import { Badge, Card, EmptyState } from "./ui";

const ORDER = { HIGH: 0, MEDIUM: 1, LOW: 2, CONTEXT: 3 } as const;

export function EvidenceCenter({ d }: { d: CaseDetail }) {
  const groups = d.evidence;
  const initial = useMemo(() => {
    const strong = groups.find((g) => g.items.some((i) => i.rating === "HIGH")) ?? groups.find((g) => g.items.some((i) => i.rating === "MEDIUM")) ?? groups[0];
    return strong?.category ?? "";
  }, [groups]);
  const [tab, setTab] = useState(initial);
  const active = groups.find((g) => g.category === tab) ?? groups[0];
  if (!groups.length) return <Card title="Evidence center"><EmptyState title="No evidence recorded" body="The investigation did not record evidence items for this case." /></Card>;
  const items = [...active.items].sort((a, b) => ORDER[a.rating] - ORDER[b.rating]);
  return (
    <Card title="Evidence center" hint={`${groups.reduce((n, g) => n + g.items.length, 0)} items · ${d.uncertainty.independent_sources ?? 0} independent source(s)`} flush>
      <div className="tabs" role="tablist">
        {groups.map((g) => (
          <button key={g.category} role="tab" className="tab" aria-selected={g.category === active.category} onClick={() => setTab(g.category)}>
            {g.category}
            <span className="count">{g.items.length}</span>
          </button>
        ))}
      </div>
      <div role="tabpanel" style={{ maxHeight: 460, overflow: "auto" }}>
        {items.map((i) => <EvidenceRow key={i.id} i={i} />)}
      </div>
    </Card>
  );
}

function EvidenceRow({ i }: { i: EvidenceItem }) {
  return (
    <div className={`ev ${i.rating === "HIGH" ? "strong" : ""}`}>
      <div><Badge tone={i.simulated ? "sim" : ratingTone(i.rating)}>{i.simulated ? "Simulated" : i.strength}</Badge></div>
      <div>
        <div className="title">{i.title}</div>
        <div className="why">{i.why}</div>
        <div className="meta">
          <span>Source: {i.source_label}</span>
          <span className="chip">{i.id}</span>
          {i.entities.slice(0, 3).map((e) => <span key={e} className="chip">{e}</span>)}
          {i.entities.length > 3 && <span>+{i.entities.length - 3} more</span>}
        </div>
      </div>
    </div>
  );
}
