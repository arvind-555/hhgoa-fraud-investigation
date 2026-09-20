export const usd = (n: number | null | undefined) =>
  n == null ? "—" : n.toLocaleString("en-US", { style: "currency", currency: "USD", minimumFractionDigits: n % 1 ? 2 : 0 });
export const human = (s: string) => s.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
export const actionLabel = (a: string) => a.replace(/_/g, " ");
const TRIGGERS: Record<string, string> = { risk_score: "Model alert", customer_report: "Customer report", analyst_request: "Analyst request" };
export const triggerLabel = (t: string) => TRIGGERS[t] ?? human(t);
export const ms = (n: number) => (n < 1000 ? `${Math.round(n)} ms` : `${(n / 1000).toFixed(1)} s`);
export type Tone = "danger" | "warn" | "ok" | "info" | "neutral";
export const verdictTone = (v: string): Tone => (v === "fraud" ? "danger" : v === "uncertain" ? "warn" : v === "legitimate" ? "ok" : "neutral");
export const ratingTone = (r: string): Tone => (r === "HIGH" ? "danger" : r === "MEDIUM" ? "warn" : r === "LOW" ? "info" : "neutral");
export const routeTone = (r: string): Tone => (r === "L2" ? "danger" : r === "L1" ? "warn" : "neutral");
export const strengthTone = (s: string | null): Tone => (s === "strong" ? "danger" : s === "moderate" ? "warn" : s === "weak" ? "info" : "neutral");
export const patternLabel = (p: string) => (p === "none" ? "No pattern" : p === "undocumented" ? "Undocumented pattern" : human(p));
export const statusLabel = (s: string) => human(s);
