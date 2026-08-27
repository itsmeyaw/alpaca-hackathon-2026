export type AgentMode = "RUNNING" | "PAUSED" | "RISK_HALTED" | "KILLED";

export type Route =
  | "CATALYST_CONTINUATION"
  | "LIQUIDITY_REVERSION"
  | "REGIME_TREND"
  | "NO_TRADE";

export interface PublicStatus {
  mode: AgentMode;
  reconciled: boolean;
}

export interface PublicDecision {
  decision_id: string;
  decision_type: string;
  occurred_at: string;
  route: Route | null;
  symbol: string | null;
  summary: string;
}

export interface PublicSnapshot {
  status: PublicStatus;
  decisions: PublicDecision[];
  receivedAt: Date;
}

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

async function getJson<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    signal,
    headers: { Accept: "application/json" },
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(`Public API returned ${response.status}`);
  }
  return (await response.json()) as T;
}

export async function fetchPublicSnapshot(signal: AbortSignal): Promise<PublicSnapshot> {
  const [status, decisions] = await Promise.all([
    getJson<PublicStatus>("/api/public/status", signal),
    getJson<PublicDecision[]>("/api/public/decisions?limit=50", signal),
  ]);
  return { status, decisions, receivedAt: new Date() };
}
