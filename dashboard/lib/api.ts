export type AgentMode = "RUNNING" | "PAUSED" | "RISK_HALTED" | "KILLED";

export type Route =
  | "CATALYST_CONTINUATION"
  | "LIQUIDITY_REVERSION"
  | "REGIME_TREND"
  | "MODEL_DIRECTIONAL"
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

export interface PublicDecisionPage {
  records: PublicDecision[];
  next_cursor: string | null;
}

export interface PublicDecisionFilters {
  search: string;
  route: Route | "";
}

export interface PublicPortfolioPoint {
  captured_at: string;
  equity: string;
  cash: string;
  net_pnl: string;
  daily_return: string;
  competition_return: string;
  drawdown: string;
  position_count: number;
  max_trade_risk_rate: string;
  total_open_risk_rate: string;
  overnight_open_risk_rate: string;
  max_group_open_risk_rate: string;
}

export interface PublicChallengerStatus {
  deployed: boolean;
  loaded: boolean;
  authority: "SHADOW_ONLY" | "PAPER_LIVE" | null;
  run_id: string | null;
  created_at: string | null;
  candidate: string | null;
  feature_schema: string | null;
  decision_gate: number | null;
  horizon_bars: number | null;
  timeframe_minutes: number | null;
  symbol_count: number | null;
  selection_score: number | null;
  validation_return: number | null;
  validation_sharpe: number | null;
  positive_folds: number | null;
  folds: number | null;
  holdout_return: number | null;
  holdout_sharpe: number | null;
  holdout_max_drawdown: number | null;
  holdout_trades: number | null;
  numeric_shadow_gate_passed: boolean | null;
  promotion_eligible: boolean;
  model_sha256: string | null;
}

export interface PublicSnapshot {
  status: PublicStatus;
  decisions: PublicDecision[];
  routeDecisions: PublicDecision[];
  portfolio: PublicPortfolioPoint[];
  challenger: PublicChallengerStatus;
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
  const [status, decisions, routeDecisions, portfolio, challenger] = await Promise.all([
    getJson<PublicStatus>("/api/public/status", signal),
    getJson<PublicDecision[]>("/api/public/decisions?limit=50", signal),
    getJson<PublicDecision[]>("/api/public/routes?limit=100", signal),
    getJson<PublicPortfolioPoint[]>("/api/public/portfolio?limit=200", signal),
    getJson<PublicChallengerStatus>("/api/public/challenger", signal).catch(() => ({
      deployed: false,
      loaded: false,
      authority: null,
      run_id: null,
      created_at: null,
      candidate: null,
      feature_schema: null,
      decision_gate: null,
      horizon_bars: null,
      timeframe_minutes: null,
      symbol_count: null,
      selection_score: null,
      validation_return: null,
      validation_sharpe: null,
      positive_folds: null,
      folds: null,
      holdout_return: null,
      holdout_sharpe: null,
      holdout_max_drawdown: null,
      holdout_trades: null,
      numeric_shadow_gate_passed: null,
      promotion_eligible: false,
      model_sha256: null,
    })),
  ]);
  return { status, decisions, routeDecisions, portfolio, challenger, receivedAt: new Date() };
}

export async function fetchPublicDecisionPage(
  filters: PublicDecisionFilters,
  cursor: string | null,
  signal: AbortSignal,
): Promise<PublicDecisionPage> {
  const params = new URLSearchParams({ limit: "25" });
  if (cursor) params.set("cursor", cursor);
  if (filters.search.trim()) params.set("search", filters.search.trim());
  if (filters.route) params.set("route", filters.route);
  return getJson<PublicDecisionPage>(`/api/public/decision-pages?${params}`, signal);
}
