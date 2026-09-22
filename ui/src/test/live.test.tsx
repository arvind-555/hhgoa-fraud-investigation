import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { LiveInvestigationPage } from "../pages/LiveInvestigation";
import type { CaseDetail, LiveConsistency, LiveJob, LiveResult } from "../types";
import h014 from "./fixtures/HHG-014.json";

const D014 = h014 as unknown as CaseDetail;
const MATCH: LiveConsistency = { checked: true, match: true, level: "match", message: "Live result matches stored validated case.", live_sha256: "4dc7ad7719ea", stored_sha256: "4dc7ad7719ea", stored_revision: 3, differs: [], written_to_graph: false };
// What ui_api/live.py does to the stored fixture's shape: the record of a live preview has no graph write, and its "write" event says so.
const liveDetail = (): CaseDetail => ({
  ...D014, live: { preview: true, written_to_graph: false },
  case: { ...D014.case, written_to_graph: false, graph_case_id: "", revision: null },
  activity: { ...D014.activity, events: D014.activity.events.map((e) => (e.stage === "write" ? { ...e, label: "Case write not performed (live preview)", source: "Live preview", summary: "No FI_Case write; the result is a preview only" } : e)) },
});
const result = (c: Partial<LiveConsistency> = {}): LiveResult => ({
  live: true, executed_against: "live TigerGraph", written_to_graph: false, fi_case_write: "not performed",
  summary: { verdict: "fraud", status: "escalated", pattern: "undocumented", exposure_usd: 3778.14, evidence_strength: "strong", sar_file: true,
             final_actions: [{ action: "CREATE_CASE", route: "auto" }], tool_calls: 29, latency_s: 41.2, graph_queries: 28 },
  consistency: { ...MATCH, ...c }, detail: liveDetail(),
});
const job = (status: LiveJob["status"], extra: Partial<LiveJob> = {}): LiveJob => ({ job_id: "a".repeat(32), case_id: "HHG-014", status, elapsed_s: 12, error: null, error_code: null, ...extra });
const ok = (body: unknown, status = 200) => ({ ok: status < 400, status, json: async () => body });
const RUN = /Run Live Investigation/;

beforeEach(() => { window.location.hash = ""; });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("Live Investigation page", () => {
  it("renders the labelled page, the case selector and the run button", () => {
    vi.stubGlobal("fetch", vi.fn());
    render(<LiveInvestigationPage />);
    expect(screen.getByRole("heading", { name: "LIVE INVESTIGATION" })).toBeInTheDocument();
    expect(screen.getAllByText(/Executed against live TigerGraph\. No FI_Case write performed\./).length).toBeGreaterThan(0);
    const select = screen.getByLabelText("Case") as HTMLSelectElement;
    expect(select.value).toBe("HHG-014");
    expect(select.options).toHaveLength(20);
    expect(screen.getByRole("button", { name: RUN })).toBeEnabled();
  });

  it("starts a job with a POST that carries only the case id, then shows Queued, Running and Completed", async () => {
    const f = vi.fn()
      .mockResolvedValueOnce(ok(job("queued"), 202))
      .mockResolvedValueOnce(ok(job("running")))
      .mockResolvedValue(ok(job("completed", { result: result() })));
    vi.stubGlobal("fetch", f);
    render(<LiveInvestigationPage pollMs={20} />);
    fireEvent.change(screen.getByLabelText("Case"), { target: { value: "HHG-001" } });
    fireEvent.click(screen.getByRole("button", { name: RUN }));
    expect(await screen.findByText("Queued")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: RUN })).toBeDisabled();
    expect(await screen.findByText("Running")).toBeInTheDocument();
    expect(await screen.findByText("Completed")).toBeInTheDocument();
    expect(f.mock.calls[0]).toEqual(["/api/live/HHG-001", { method: "POST" }]);
    expect(String(f.mock.calls[1][0])).toBe(`/api/live/${"a".repeat(32)}`);
    expect(screen.getByLabelText("Elapsed time")).toHaveTextContent(/\d+ s/);
  });

  it("shows the completed result: verdict, actions with approval routes, SAR decision, consistency, and an honest trace label", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(ok(job("completed", { result: result() }), 200)));
    render(<LiveInvestigationPage pollMs={20} />);
    fireEvent.click(screen.getByRole("button", { name: RUN }));
    const live = await screen.findByText("Live result");
    const card = live.closest("section") as HTMLElement;
    expect(within(card).getByText("Fraud")).toBeInTheDocument();
    expect(within(card).getByText(/A suspicious-activity report is recommended/)).toBeInTheDocument();
    expect(within(card).getAllByText(/CREATE CASE/).length).toBeGreaterThan(0);
    expect(within(card).getByText(/None \(written_to_graph = false\)/)).toBeInTheDocument();
    const c = screen.getByRole("status", { name: "Consistency with the stored case" });
    expect(c).toHaveTextContent("Live result matches stored validated case.");
    expect(c).toHaveTextContent("stored FI_Case revision 3");
    expect(screen.getByRole("heading", { name: "Investigation trace" })).toBeInTheDocument();
    expect(screen.getByText(/steps were not streamed while it ran/)).toBeInTheDocument();
    expect(screen.getByText(/Live preview · no FI_Case write performed/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/streaming|streamed live|live stream|Written to TigerGraph/i);
  });

  it("shows a visible warning when the live result differs from the stored case", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(ok(job("completed", { result: result({ match: false, level: "differs", message: "Live result differs from stored case.", differs: ["final_actions", "sar_decision"] }) }))));
    render(<LiveInvestigationPage pollMs={20} />);
    fireEvent.click(screen.getByRole("button", { name: RUN }));
    const c = await screen.findByRole("status", { name: "Consistency with the stored case" });
    expect(c).toHaveTextContent("Live result differs from stored case.");
    expect(c).toHaveTextContent(/Differs in: Final actions, Sar decision\. Nothing was written or repaired\./);
  });

  it("shows a clear failure, substitutes nothing, and points to the stored replay as a separate thing", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(ok(job("failed", { error: "Live graph unavailable", error_code: "graph_unavailable" }))));
    render(<LiveInvestigationPage pollMs={20} />);
    fireEvent.click(screen.getByRole("button", { name: RUN }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Live investigation failed: Live graph unavailable.");
    expect(alert).toHaveTextContent(/nothing was substituted/);
    expect(alert).toHaveTextContent(/a recorded result, not a live run/);
    expect(within(alert).getByRole("link")).toHaveAttribute("href", "#/investigations/HHG-014");
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.queryByText("Live result")).toBeNull();
    expect(screen.queryByRole("heading", { name: "Investigation trace" })).toBeNull();
    expect(screen.getByRole("button", { name: RUN })).toBeEnabled();
  });

  it("tells the user when another live investigation is already running (429) and when the service is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(ok({ error: "busy" }, 429)));
    const { unmount } = render(<LiveInvestigationPage />);
    fireEvent.click(screen.getByRole("button", { name: RUN }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Another live investigation is running/);
    unmount();
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("network")));
    render(<LiveInvestigationPage />);
    fireEvent.click(screen.getByRole("button", { name: RUN }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/not reachable/));
  });

  it("is reachable from the navigation, and the replay routes are still there", () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(ok({ status: "ok" })));
    window.location.hash = "#/live";
    render(<App />);
    expect(screen.getByRole("heading", { name: "LIVE INVESTIGATION" })).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Primary" });
    expect(within(nav).getByRole("link", { name: /Live investigation/ })).toHaveAttribute("href", "#/live");
    expect(within(nav).getByRole("link", { name: /Investigations/ })).toHaveAttribute("href", "#/investigations");
    for (const n of ["Overview", "Cases", "Customers", "Graph", "Policies", "System status"]) expect(within(nav).getByRole("link", { name: new RegExp(n) })).toBeInTheDocument();
  });

  it("never claims to stream and never reveals credentials or as_of in its source", () => {
    const text = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "..", "pages", "LiveInvestigation.tsx"), "utf8");
    expect(text).not.toMatch(/streaming|live stream|as_of|TG_SECRET|OPENAI/i);
    expect(text).toMatch(/steps were not streamed/);
  });
});
