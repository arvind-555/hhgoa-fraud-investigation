// Mirrors the read-only API in src/ui_api/service.py. The UI renders these values as given; it never derives verdicts, routes or evidence strength.
export type Rating = "HIGH" | "MEDIUM" | "LOW" | "CONTEXT";

export interface CaseSummary {
  case_id: string; trigger_type: string; opened_at: string; customer_id: string; card_id: string; pattern: string; verdict: string; status: string;
  evidence_strength: string | null; final_actions: { action: string; route: string }[]; top_action: string | null; exposure_usd: number; affected_txn_count: number;
  sar_filed: boolean; written_to_graph: boolean; graph_case_id: string; revision: number | null; simulated_evidence: boolean; requested: string[];
}
export interface ActionView { action: string; route: string; route_label: string; reason: string; rules: string[]; requires_approval: boolean; executed: boolean }
export interface EvidenceItem { id: string; title: string; source: string; source_label: string; rating: Rating; strength: string; why: string; entities: string[]; simulated: boolean }
export interface EvidenceGroup { category: string; items: EvidenceItem[] }
export interface ReplayEvent { i: number; stage: string; label: string; tool: string | null; source: string; status: string; summary: string; detail: string[]; reveal: string[]; simulated: boolean; step: number }
export interface GraphNode { id: string; kind: string; label: string; detail?: Record<string, unknown>; focus?: boolean; ring?: boolean; hidden?: boolean; cluster?: string; members?: string[] }
export interface GraphEdge { source: string; target: string; rel: string; ring?: boolean; hidden?: boolean }
export interface GraphData { nodes: GraphNode[]; edges: GraphEdge[]; legend: { kind: string; label: string }[]; note: string }
export interface Sar { file: boolean; reason: string; narrative: string; subjects: string[]; total_amount_usd: number; activity_dates: string[] }

export interface CaseDetail {
  integrity: { ok: boolean; checks: { name: string; match: boolean }[]; note: string };
  header: { case_id: string; status: string; verdict: string; pattern: string; trigger_type: string; opened_at: string; as_of_note: string; evidence_strength: string | null; uncertainty_level: string | null };
  trigger: { text: string; flagged_txn_id: string; card_id: string; customer_id: string; amount_usd: number | null; channel: string; trigger_type: string; opened_at: string };
  uncertainty: { evidence_strength: string | null; level: string | null; independent_sources: number | null; conflicts: string[]; missing: string[]; reasons: string[]; needs_more_evidence: boolean;
                 before_verdict: string; after_verdict: string; after_status: string; statement: string };
  requests: { type: string; label: string; asked_after_step: number; assumed_response: string; outcome: string | null; simulated: boolean }[];
  evidence: EvidenceGroup[];
  activity: { steps: { step: number; state: string; label: string; start_ms: number; duration_ms: number; tool_calls: number; note: string }[];
              tools: { tool: string; label: string; evidence_count: number; top: string[] }[]; events: ReplayEvent[]; total_ms?: number };
  actions: { initial: ActionView[]; final: ActionView[]; what_changed: string };
  case: { status: string; verdict: string; pattern: string; pattern_description: string; summary: string; affected_txn_ids: string[]; first_suspicious_txn_id: string; exposure_usd: number;
          connected_card_ids: string[]; connected_device_profiles: string[]; similar_prior_cases: string[]; written_to_graph: boolean; graph_case_id: string; revision: number | null;
          graph_write_action: string | null; fraud_probability: number | null; probability_note: string; stop_reason: string };
  sar: Sar;
  measured: { tool_calls: number; tokens: number; latency_s: number; mode: string };
  graph: GraphData;
}
export interface Overview { total: number; verdicts: Record<string, number>; patterns: Record<string, number>; triggers: Record<string, number>; sar_filed: number; written_to_graph: number; exposure_usd: number; needs_approval: number; showcase: string | null }
export interface GraphProbe { reachable: boolean; found?: boolean; graph_case_id?: string; revision?: number | string; as_of_epoch?: number; status?: string; error?: string }
export interface CustomerRow { customer_id: string; cards: string[]; cases: { case_id: string; verdict: string; pattern: string }[]; exposure_usd: number }
export interface SystemStatus { api: { ok: boolean; cases: number; records: number }; graph: GraphProbe; safety: string[] }
export interface PolicyDocs { documents: { ref: string; text: string }[]; routes: Record<string, string> }
