import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ActionPanel, UncertaintyPanel } from "../components/Decision";
import { EvidenceCenter } from "../components/Evidence";
import { GraphView } from "../components/GraphView";
import { CasePanel } from "../components/CasePanel";
import { OutcomeStrip } from "../components/Hero";
import { CaseTable } from "../pages/Lists";
import { Workspace, InvestigationView } from "../pages/InvestigationView";
import { ms, usd, verdictTone } from "../format";
import type { CaseDetail, CaseSummary } from "../types";
import h014 from "./fixtures/HHG-014.json";
import h006 from "./fixtures/HHG-006.json";
import h001 from "./fixtures/HHG-001.json";
import cases from "./fixtures/cases.json";

// Fixtures are real backend output (src/ui_api/service.py over the validated cases/ and stored records), not hand-written results.
const D014 = h014 as unknown as CaseDetail;
const D006 = h006 as unknown as CaseDetail;
const D001 = h001 as unknown as CaseDetail;
const ROWS = cases as unknown as CaseSummary[];

beforeEach(() => { window.location.hash = ""; });
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe("formatting", () => {
  it("formats money, durations and tones without inventing values", () => {
    expect(usd(1906.07)).toBe("$1,906.07");
    expect(usd(null)).toBe("—");
    expect(ms(15762.6)).toBe("15.8 s");
    expect(verdictTone("fraud")).toBe("danger");
    expect(verdictTone("legitimate")).toBe("ok");
  });
});

describe("next best action (authoritative backend value)", () => {
  it("renders the backend action and approval route for a blocking case", () => {
    render(<ActionPanel d={D006} />);
    const p = screen.getByLabelText("Recommended next action");
    expect(within(p).getByText("BLOCK CARD")).toBeInTheDocument();
    expect(within(p).getByText("Level 1 approval (team lead)")).toBeInTheDocument();
    expect(within(p).getByText("Requires approval")).toBeInTheDocument();
    expect(within(p).getByText(/Nothing has been executed/)).toBeInTheDocument();
    expect(within(p).getByText("FILE REPORT")).toBeInTheDocument();   // later action with its own L2 route
  });
  it("shows an automatic action as needing no approval", () => {
    render(<ActionPanel d={D014} />);
    const p = screen.getByLabelText("Recommended next action");
    expect(p.querySelector(".action-name")).toHaveTextContent("ESCALATE TO ANALYST");
    expect(within(p).getByText("No approval needed")).toBeInTheDocument();
  });
  it("shows the initial and final recommendations", () => {
    render(<ActionPanel d={D006} />);
    expect(screen.getByText("Before additional evidence")).toBeInTheDocument();
    expect(screen.getByText(D006.actions.what_changed)).toBeInTheDocument();
  });
});

describe("evidence center", () => {
  it("opens on the strongest evidence and labels strengths from the backend", () => {
    render(<EvidenceCenter d={D014} />);
    expect(screen.getByRole("tab", { name: /Device/, selected: true })).toBeInTheDocument();
    expect(screen.getAllByText("Strong").length).toBeGreaterThan(0);
    expect(screen.getByText(/S01 Shared-origin device ring/)).toBeInTheDocument();
  });
  it("marks simulated evidence as simulated, never as customer evidence", () => {
    render(<EvidenceCenter d={D014} />);
    fireEvent.click(screen.getByRole("tab", { name: /Additional evidence/ }));
    expect(screen.getByText("Simulated")).toBeInTheDocument();
    expect(screen.getByText(/Simulated response: not real customer evidence/)).toBeInTheDocument();
  });
  it("shows an empty state when a case has no evidence", () => {
    render(<EvidenceCenter d={{ ...D014, evidence: [] }} />);
    expect(screen.getByText("No evidence recorded")).toBeInTheDocument();
  });
});

describe("uncertainty transition", () => {
  it("shows pending before the response and the backend verdict after", () => {
    const { rerender } = render(<UncertaintyPanel d={D006} resolved={false} />);
    expect(screen.getAllByText("Pending").length).toBeGreaterThan(0);
    rerender(<UncertaintyPanel d={D006} resolved />);
    expect(screen.getByText(/Denied \/ not recognized/)).toBeInTheDocument();
    expect(screen.getAllByText("Simulated").length).toBeGreaterThan(0);
    expect(screen.getByText(/not from a real customer/)).toBeInTheDocument();
  });
  it("does not imply a request when none was made", () => {
    render(<UncertaintyPanel d={{ ...D001, requests: [] }} resolved />);
    expect(screen.getByText(/No additional evidence was requested/)).toBeInTheDocument();
  });
});

describe("case record", () => {
  it("shows the graph write, revision and that no probability is stated", () => {
    render(<CasePanel d={D014} />);
    expect(screen.getByText("Written to TigerGraph")).toBeInTheDocument();
    expect(screen.getByText(/revision 1/)).toBeInTheDocument();
    expect(screen.getByText("Not stated")).toBeInTheDocument();
  });
  it("degrades gracefully when the live check cannot reach the graph", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ reachable: false, error: "graph unavailable (URLError)" }) }));
    render(<CasePanel d={D014} />);
    fireEvent.click(screen.getByRole("button", { name: /Verify live/ }));
    expect(await screen.findByText(/Live check unavailable/)).toBeInTheDocument();
    expect(screen.getByText("Written to TigerGraph")).toBeInTheDocument();   // stored result stays
  });
});

describe("relationship graph", () => {
  it("draws the case, card and the shared device, and opens details on click", () => {
    render(<GraphView data={D014.graph} />);
    expect(screen.getByTestId("node-CASE-HHG-014")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("node-D_2c2006f554"));
    const panel = screen.getByLabelText("Node details");
    expect(within(panel).getByText("D_2c2006f554")).toBeInTheDocument();
    expect(within(panel).getByText("shared by cards")).toBeInTheDocument();
  });
  it("starts a shared-device ring expanded so the fan-out of shared cards is visible, and can collapse it", () => {
    render(<GraphView data={D014.graph} />);
    const withRing = document.querySelectorAll(".g-node").length;
    expect(document.querySelectorAll(".g-edge.ring").length).toBeGreaterThan(19);      // device -> each connected card, drawn as ring links
    fireEvent.click(screen.getByRole("button", { name: /Collapse 19 connected cards/ }));
    expect(document.querySelectorAll(".g-node").length).toBe(withRing - 19);
    fireEvent.click(screen.getByRole("button", { name: /Expand 19 connected cards/ }));
    expect(document.querySelectorAll(".g-node").length).toBe(withRing);
  });
  it("folds many affected transactions into one expandable node without changing the data", () => {
    render(<GraphView data={D014.graph} />);
    expect(document.querySelectorAll('[data-testid^="node-txn:"]').length).toBe(0);
    fireEvent.click(screen.getByRole("button", { name: /Expand 26 transactions/ }));
    expect(document.querySelectorAll('[data-testid^="node-txn:"]').length).toBe(26);
  });
});

describe("graph wheel zoom", () => {
  it("zooms with the plain mouse wheel while over the graph", () => {
    render(<GraphView data={D014.graph} />);
    const svg = screen.getByRole("img", { name: /relationship graph/i });
    const scale = () => Number(/scale\(([\d.]+)\)/.exec(svg.querySelector("g")!.getAttribute("transform") ?? "")?.[1]);
    const before = scale();
    const ev = new WheelEvent("wheel", { deltaY: -100, bubbles: true, cancelable: true });
    act(() => { svg.dispatchEvent(ev); });
    expect(ev.defaultPrevented).toBe(true);          // the page does not scroll while the pointer is over the graph
    expect(scale()).toBeGreaterThan(before);
    act(() => { svg.dispatchEvent(new WheelEvent("wheel", { deltaY: 300, bubbles: true, cancelable: true })); });
    expect(scale()).toBeLessThan(before * 1.12);
  });
  it("leaves scrolling outside the graph alone", () => {
    render(<div><GraphView data={D014.graph} /><p data-testid="outside">page</p></div>);
    const ev = new WheelEvent("wheel", { deltaY: 100, bubbles: true, cancelable: true });
    screen.getByTestId("outside").dispatchEvent(ev);
    expect(ev.defaultPrevented).toBe(false);
  });
});

describe("outcome strip and hero", () => {
  it("summarises finding, additional evidence, action and case from backend fields only", () => {
    render(<OutcomeStrip d={D014} />);
    const s = screen.getByLabelText("Investigation outcome");
    expect(s).toHaveTextContent(/S01 Shared-origin device ring/);
    expect(s).toHaveTextContent(/Step-up authentication/);
    expect(s).toHaveTextContent(/passed · simulated/);
    expect(s).toHaveTextContent(/ESCALATE TO ANALYST/);
    expect(s).toHaveTextContent(/Automatic \(no approval\)/);
    expect(s).toHaveTextContent(/26 transactions · \$3,778\.14/);
    expect(s).toHaveTextContent(/CASE-HHG-014 · revision 1/);
  });
  it("says so when nothing was requested or found", () => {
    render(<OutcomeStrip d={{ ...D001, requests: [], evidence: [] }} />);
    expect(screen.getByLabelText("Investigation outcome")).toHaveTextContent(/No validated fraud signal/);
    expect(screen.getByLabelText("Investigation outcome")).toHaveTextContent(/None requested/);
  });
  it("does not show empty facts for an analyst-request trigger", () => {
    render(<Workspace d={D014} demo={false} />);
    expect(screen.queryByText("Not stated in trigger")).not.toBeInTheDocument();
    expect(screen.queryByText("Not stated in the trigger")).not.toBeInTheDocument();
  });
  it("keeps the outcome hidden until the case step during a demo replay, then shows it with the ring callout", () => {
    vi.useFakeTimers();
    render(<Workspace d={D014} demo />);
    expect(screen.queryByLabelText("Investigation outcome")).not.toBeInTheDocument();
    for (let i = 0; i < D014.activity.events.length + 2; i++) act(() => { vi.advanceTimersByTime(1100); });
    expect(screen.getByLabelText("Investigation outcome")).toBeInTheDocument();
    expect(screen.getByText(/links this case to/)).toBeInTheDocument();
  });
});

describe("case list", () => {
  it("lists all 20 benchmark cases and filters, searches and sorts", () => {
    render(<CaseTable rows={ROWS} />);
    expect(screen.getAllByRole("row").length).toBe(21);
    fireEvent.change(screen.getByLabelText("Filter by verdict"), { target: { value: "fraud" } });
    expect(screen.getAllByRole("row").length).toBe(1 + ROWS.filter((r) => r.verdict === "fraud").length);
    fireEvent.change(screen.getByLabelText("Filter by verdict"), { target: { value: "all" } });
    fireEvent.change(screen.getByLabelText("Search investigations"), { target: { value: "HHG-014" } });
    expect(screen.getAllByRole("row").length).toBe(2);
    fireEvent.change(screen.getByLabelText("Search investigations"), { target: { value: "zzz-no-match" } });
    expect(screen.getByText("No investigations match")).toBeInTheDocument();
  });
  it("sorts by exposure and opens an investigation", () => {
    render(<CaseTable rows={ROWS} />);
    const btn = screen.getByRole("button", { name: /Exposure/ });
    fireEvent.click(btn);
    fireEvent.click(btn);
    const first = screen.getAllByRole("row")[1];
    expect(first).toHaveTextContent(/HHG-014/);        // the largest exposure in the benchmark
    fireEvent.click(first);
    expect(window.location.hash).toBe("#/investigations/HHG-014");
  });
});

describe("investigation workspace", () => {
  it("shows the whole backend result immediately outside demo mode", () => {
    render(<Workspace d={D014} demo={false} />);
    expect(screen.getByLabelText("Recommended next action").querySelector(".action-name")).toHaveTextContent("ESCALATE TO ANALYST");
    expect(screen.getByLabelText("Relationship graph for HHG-014")).toBeInTheDocument();
    expect(screen.queryByText(/Demo mode\./)).not.toBeInTheDocument();
  });
  it("demo mode replays step by step, labels simulated responses and reveals the action last", () => {
    vi.useFakeTimers();
    render(<Workspace d={D014} demo />);
    expect(screen.getByText(/Demo mode\./)).toBeInTheDocument();
    expect(screen.getByText(/Waiting for the next-best action/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Recommended next action")).not.toBeInTheDocument();
    expect(screen.getAllByText("Investigating").length).toBeGreaterThan(0);
    for (let i = 0; i < 4; i++) act(() => { vi.advanceTimersByTime(1100); });
    expect(screen.getAllByText("Investigation triggered").length).toBeGreaterThan(0);
    expect(screen.queryByLabelText("Recommended next action")).not.toBeInTheDocument();
    for (let i = 0; i < D014.activity.events.length + 2; i++) act(() => { vi.advanceTimersByTime(1100); });
    expect(screen.getByLabelText("Recommended next action")).toBeInTheDocument();
    expect(screen.getAllByText("Simulated").length).toBeGreaterThan(0);
    expect(screen.getByText("Investigation complete")).toBeInTheDocument();
  });
  it("can skip to the result and replay again", () => {
    vi.useFakeTimers();
    render(<Workspace d={D014} demo />);
    fireEvent.click(screen.getByRole("button", { name: "Skip to result" }));
    expect(screen.getByLabelText("Recommended next action")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Replay investigation/ }));
    expect(screen.getByText(/Waiting for the next-best action/)).toBeInTheDocument();
  });
  it("warns when the stored record disagrees with the submitted answer", () => {
    render(<Workspace d={{ ...D014, integrity: { ok: false, checks: [{ name: "final actions", match: false }], note: "" } }} demo={false} />);
    expect(screen.getByRole("alert")).toHaveTextContent(/final actions/);
  });
});

describe("loading, error and not-found states", () => {
  it("shows a loading state, then an error with retry when the service is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("network")));
    render(<InvestigationView id="HHG-014" demo={false} />);
    expect(screen.getByRole("status", { name: "Loading" })).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent(/not reachable/);
    expect(screen.getByRole("button", { name: /Try again/ })).toBeInTheDocument();
  });
  it("reports an unknown case as not found", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 404, json: async () => ({}) }));
    render(<InvestigationView id="HHG-999" demo={false} />);
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/Not found/));
  });
  it("loads an investigation through the read-only API", async () => {
    const f = vi.fn().mockResolvedValue({ ok: true, json: async () => D001 });
    vi.stubGlobal("fetch", f);
    render(<InvestigationView id="HHG-001" demo={false} />);
    expect((await screen.findByLabelText("Recommended next action")).querySelector(".action-name")).toHaveTextContent("CLOSE NO FRAUD");
    expect(f).toHaveBeenCalledWith("/api/cases/HHG-001");
    expect(f.mock.calls.every((c) => c.length === 1)).toBe(true);   // GET only: no method, body or headers were ever passed
  });
});

describe("safety boundaries of the frontend bundle", () => {
  const files = (dir: string): string[] => readdirSync(dir).flatMap((f: string) => (statSync(join(dir, f)).isDirectory() ? (f === "test" ? [] : files(join(dir, f))) : [join(dir, f)]));
  const src = files(join(dirname(fileURLToPath(import.meta.url)), "..")).map((f) => [f, readFileSync(f, "utf8")] as const);
  it("contains no credentials, keys, GSQL execution or client-chosen as_of", () => {
    for (const [f, text] of src) {
      expect(text, f).not.toMatch(/TG_SECRET|TG_HOST|OPENROUTER|ANTHROPIC_API_KEY|sk-or-|Bearer |run_gsql|interpreted_query|[?&]as_of=/);
    }
  });
  it("only ever issues GET requests", () => {
    for (const [f, text] of src) expect(text, f).not.toMatch(/method:\s*["'](POST|PUT|PATCH|DELETE)/i);
  });
});
