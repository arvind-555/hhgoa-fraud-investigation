import { useEffect, useState } from "react";
import { Icon, EmptyState } from "./components/ui";
import { href, useRoute } from "./router";
import { CasesPage, CustomersPage, GraphPage, InvestigationsPage } from "./pages/Lists";
import { InvestigationView } from "./pages/InvestigationView";
import { LiveInvestigationPage } from "./pages/LiveInvestigation";
import { OverviewPage, PoliciesPage, SystemPage } from "./pages/Other";

const NAV = [
  { to: "/", key: "", label: "Overview", icon: "home" }, { to: "/investigations", key: "investigations", label: "Investigations", icon: "pulse" },
  { to: "/live", key: "live", label: "Live investigation", icon: "play" },
  { to: "/cases", key: "cases", label: "Cases", icon: "folder" }, { to: "/customers", key: "customers", label: "Customers", icon: "users" },
  { to: "/graph", key: "graph", label: "Graph", icon: "graph" }, { to: "/policies", key: "policies", label: "Policies", icon: "book" }, { to: "/system", key: "system", label: "System status", icon: "db" },
];

function ApiPill() {
  const [ok, setOk] = useState<boolean | null>(null);
  useEffect(() => {
    let live = true;
    fetch("/api/health").then((r) => live && setOk(r.ok), () => live && setOk(false));
    return () => { live = false; };
  }, []);
  return <span className={`badge ${ok === null ? "neutral" : ok ? "ok" : "danger"}`}><span className="dot" />{ok === null ? "Checking" : ok ? "Service online" : "Service offline"}</span>;
}

export default function App() {
  const r = useRoute();
  const section = r.path[0] ?? "";
  const id = r.path[1];
  const demo = r.query.get("demo") === "1";
  let page;
  if (section === "") page = <OverviewPage />;
  else if (section === "investigations") page = id ? <InvestigationView id={id} demo={demo} key={id + String(demo)} /> : <InvestigationsPage />;
  else if (section === "live") page = <LiveInvestigationPage />;
  else if (section === "cases") page = <CasesPage />;
  else if (section === "customers") page = <CustomersPage />;
  else if (section === "graph") page = <GraphPage />;
  else if (section === "policies") page = <PoliciesPage />;
  else if (section === "system") page = <SystemPage />;
  else page = <EmptyState title="Page not found" body="That address does not exist." />;
  const cur = NAV.find((n) => n.key === section);

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark"><svg width="18" height="18" viewBox="0 0 32 32"><path d="M11 16L22 9M11 16l11 7" stroke="#6b7a90" strokeWidth="2" /><circle cx="11" cy="16" r="3.6" fill="#5b9dff" /><circle cx="22" cy="9" r="3.6" fill="#ef5b60" /><circle cx="22" cy="23" r="3.6" fill="#5b9dff" /></svg></span>
          <div><b>Fraud Investigation<br /> Command Center</b></div>
        </div>
        <nav className="nav" aria-label="Primary">
          <div className="nav-label">Workspace</div>
          {NAV.slice(0, 6).map((n) => <a key={n.key} href={href(n.to)} className={section === n.key ? "active" : ""} aria-current={section === n.key ? "page" : undefined}><Icon name={n.icon} />{n.label}</a>)}
          <div className="nav-label">Reference</div>
          {NAV.slice(6).map((n) => <a key={n.key} href={href(n.to)} className={section === n.key ? "active" : ""} aria-current={section === n.key ? "page" : undefined}><Icon name={n.icon} />{n.label}</a>)}
        </nav>
        <div className="sidebar-foot">Recommendations only.<br />Approvals stay with people.</div>
      </aside>
      <div className="main">
        <header className="topbar">
          <div className="crumbs"><span>{cur?.label ?? "Not found"}</span>{id && <><Icon name="chevron" size={14} /><b>{id}</b></>}</div>
          <span className="spacer" />
          <ApiPill />
        </header>
        <main className="content" id="main">{page}</main>
      </div>
    </div>
  );
}
