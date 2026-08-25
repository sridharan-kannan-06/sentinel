// Server side access to the engine.
//
// Every call runs on the server. The engine URL is never sent to the browser,
// there is no CORS to arrange, and a page renders from data that was fetched
// with the board's own service identity rather than the visitor's.

const ENGINE = (process.env.ENGINE_BASE_URL ?? "").replace(/\/$/, "");

export type BoardRow = {
  id: string;
  type: string;
  subject_token: string;
  owner_role: string;
  owner_id: string;
  status: string;
  risk_tier: string;
  deadline: string;
  opened_at: string;
  seconds_to_breach: number;
  risk_score: number;
  root_blocker: string | null;
  next_checkpoint: string | null;
  next_checkpoint_kind: string | null;
  version: number;
};

export type LedgerEntry = {
  actor: string;
  action: string;
  reason: string;
  evidence_ref: string | null;
  policy_decision_id: string | null;
  recipient: string | null;
  trace_id: string | null;
  at: string;
};

export type WhyStuck = {
  blocked: boolean;
  root_blocker: string | null;
  proximate_blocker?: string;
  chain: string[];
  depth?: number;
  answer: string;
};

export type AuditChain = {
  obligation: BoardRow & {
    required_evidence: string;
    blocked_by: string[];
    checkpoints: {
      kind: string;
      at: string;
      fired: boolean;
      fired_at: string | null;
    }[];
    created_at: string;
    updated_at: string;
  };
  why_stuck: WhyStuck;
  risk_score: number;
  seconds_to_breach: number;
  history: LedgerEntry[];
  policy_decisions: {
    policy_decision_id: string;
    action: string;
    actor: string;
    reason: string;
    at: string;
  }[];
  evidence_checks: {
    outcome: string;
    detail: string;
    evidence_ref: string | null;
    at: string;
  }[];
  notifications: LedgerEntry[];
  agent_actions: LedgerEntry[];
  sweep_repairs: LedgerEntry[];
  counts: Record<string, number>;
};

export type ApprovalRequest = {
  id: string;
  obligation_id: string;
  agent: string;
  action: string;
  risk_tier: string;
  rendered_payload: string;
  policy_decision_id: string | null;
  policy_hash: string;
  status: string;
  requested_at: string;
  decided_at: string | null;
  decided_by: string | null;
  decision_reason: string | null;
};

export type Trust = {
  policy: { hash: string; version: number | null };
  evidence: { hash: string; gate_enabled: boolean };
  escalation: { hash: string; max_notifications_per_recipient_per_24h: number };
  model: { name: string; location: string; role: string };
  git_sha: string;
};

async function getJson<T>(path: string): Promise<T> {
  if (!ENGINE) throw new Error("ENGINE_BASE_URL is not configured");
  // The board reports live state, so nothing here may be cached. A stale time
  // to breach is worse than no time to breach.
  const response = await fetch(`${ENGINE}${path}`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`GET ${path} returned ${response.status}`);
  }
  return (await response.json()) as T;
}

export function getBoard(): Promise<{
  count: number;
  generated_at: string;
  obligations: BoardRow[];
}> {
  return getJson("/board");
}

export function getAudit(id: string): Promise<AuditChain> {
  return getJson(`/audit/${encodeURIComponent(id)}`);
}

export function getApprovals(): Promise<{
  count: number;
  approvals: ApprovalRequest[];
}> {
  return getJson("/approvals");
}

export function getTrust(): Promise<Trust> {
  return getJson("/trust");
}

export function engineUrl(): string {
  return ENGINE;
}
