"use client";

import { startTransition, useDeferredValue, useEffect, useState } from "react";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  type AgentMode,
  type PublicDecision,
  type PublicDecisionPage,
  type PublicSnapshot,
  type Route,
  fetchPublicDecisionPage,
  fetchPublicSnapshot,
} from "@/lib/api";

const ROUTES: Route[] = [
  "CATALYST_CONTINUATION",
  "LIQUIDITY_REVERSION",
  "REGIME_TREND",
  "MODEL_DIRECTIONAL",
  "NO_TRADE",
];

const RECORDS_PER_PAGE = 25;

const routeLabels: Record<Route, string> = {
  CATALYST_CONTINUATION: "Catalyst continuation",
  LIQUIDITY_REVERSION: "Liquidity reversion",
  REGIME_TREND: "Regime trend",
  MODEL_DIRECTIONAL: "Model directional",
  NO_TRADE: "No trade",
};

const modeClasses: Record<AgentMode, string> = {
  RUNNING: "border-emerald-600/20 bg-emerald-600/10 text-emerald-800",
  PAUSED: "border-amber-700/20 bg-amber-500/15 text-amber-900",
  RISK_HALTED: "border-red-700/20 bg-red-600/10 text-red-800",
  KILLED: "border-red-700/20 bg-red-600/10 text-red-800",
};

function formatTime(value: Date | string): string {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    timeZoneName: "short",
  }).format(typeof value === "string" ? new Date(value) : value);
}

function formatRecorderTime(value: string): string {
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function routeBreakdown(decisions: PublicDecision[]) {
  const routed = decisions.filter((decision) => decision.route !== null);
  return ROUTES.map((route) => {
    const count = routed.filter((decision) => decision.route === route).length;
    return {
      route,
      count,
      percentage: routed.length === 0 ? 0 : Math.round((count / routed.length) * 100),
    };
  });
}

function SectionTitle({ number, title, description }: { number: string; title: string; description: string }) {
  return (
    <div className="space-y-1">
      <p className="font-mono text-[11px] font-semibold tracking-[0.16em] text-muted-foreground uppercase">{number}</p>
      <h2 className="text-2xl font-semibold tracking-tight text-foreground sm:text-3xl">{title}</h2>
      <p className="text-sm text-muted-foreground">{description}</p>
    </div>
  );
}

export function Dashboard() {
  const [snapshot, setSnapshot] = useState<PublicSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [decisionPage, setDecisionPage] = useState<PublicDecisionPage | null>(null);
  const [decisionPageError, setDecisionPageError] = useState<string | null>(null);
  const [decisionPageIndex, setDecisionPageIndex] = useState(0);
  const [decisionPageCursors, setDecisionPageCursors] = useState<(string | null)[]>([null]);
  const [decisionSearch, setDecisionSearch] = useState("");
  const [decisionRoute, setDecisionRoute] = useState<Route | "">("");
  const deferredDecisionSearch = useDeferredValue(decisionSearch);

  useEffect(() => {
    const controller = new AbortController();

    const refresh = async () => {
      try {
        const next = await fetchPublicSnapshot(controller.signal);
        startTransition(() => {
          setSnapshot(next);
          setError(null);
        });
      } catch (reason) {
        if (!controller.signal.aborted) {
          setError(reason instanceof Error ? reason.message : "Public API unavailable");
        }
      }
    };

    void refresh();
    const interval = window.setInterval(() => void refresh(), 30_000);
    return () => {
      controller.abort();
      window.clearInterval(interval);
    };
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const cursor = decisionPageCursors[decisionPageIndex] ?? null;

    const refresh = async () => {
      try {
        const next = await fetchPublicDecisionPage(
          { search: deferredDecisionSearch, route: decisionRoute },
          cursor,
          controller.signal,
        );
        startTransition(() => {
          setDecisionPage(next);
          setDecisionPageError(null);
        });
      } catch (reason) {
        if (!controller.signal.aborted) {
          setDecisionPageError(reason instanceof Error ? reason.message : "Decision records unavailable");
        }
      }
    };

    void refresh();
    return () => controller.abort();
  }, [decisionPageCursors, decisionPageIndex, decisionRoute, deferredDecisionSearch]);

  const status = snapshot?.status;
  const challenger = snapshot?.challenger;
  const decisions = snapshot?.decisions ?? [];
  const routes = routeBreakdown(decisions);
  const visibleDecisions = decisionPage?.records ?? [];
  const firstRecordIndex = decisionPageIndex * RECORDS_PER_PAGE;
  const resetDecisionPaging = () => {
    setDecisionPageIndex(0);
    setDecisionPageCursors([null]);
  };
  return (
    <main className="min-h-screen bg-background">
      <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 sm:py-10 lg:px-8">
        <header className="mb-6 flex flex-col gap-6 border-b border-border pb-6 sm:mb-8 sm:flex-row sm:items-end sm:justify-between">
          <div className="space-y-3">
            <div>
              <h1 className="text-4xl font-bold tracking-[-0.05em] text-foreground sm:text-6xl">Catalyst Router</h1>
            </div>
            <div aria-live="polite" className="flex flex-wrap items-center gap-x-4 gap-y-2 font-mono text-[10px] font-semibold tracking-[0.12em] text-muted-foreground uppercase">
              <span className="text-muted-foreground">Agent status</span>
              <Badge
                variant="outline"
                className={status ? modeClasses[status.mode] : "border-border bg-muted text-muted-foreground"}
              >
                {status?.mode.replaceAll("_", " ") ?? "Connecting"}
              </Badge>
              <span>Reconciliation: <strong className="text-foreground">{status ? (status.reconciled ? "Matched" : "Pending") : "Unknown"}</strong></span>
              <span>Policy: <strong className="text-foreground">Base</strong></span>
            </div>
          </div>
          <div className="space-y-1 font-mono text-[11px] text-muted-foreground sm:text-right">
            <div className="font-semibold tracking-[0.16em] text-destructive">READ-ONLY</div>
            <p>Sanitized projection / 15 min delay</p>
            <p>{snapshot ? `Received ${formatTime(snapshot.receivedAt)}` : "Connecting..."}</p>
          </div>
        </header>

        {error ? <div role="alert" className="mb-6 rounded-lg border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm text-destructive">{error}. Retrying every 30 seconds.</div> : null}

        <section className="mb-10" aria-labelledby="performance-title">
          <SectionTitle number="01 / Performance field" title="Account ledger" description="Portfolio data will publish here once the sanitized ledger projection is available." />
          <div className="mt-5 grid gap-5 lg:grid-cols-[1.65fr_1fr]">
            <Card className="border-border">
              <CardHeader>
                <CardTitle id="performance-title">Equity curve</CardTitle>
                <CardDescription>Equity, SPY, and cash baselines</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="rounded-xl border border-dashed border-border bg-muted/60 p-3">
                  <svg viewBox="0 0 800 210" role="img" aria-labelledby="chart-title chart-desc" className="h-48 w-full sm:h-56">
                    <title id="chart-title">Portfolio equity placeholder</title>
                    <desc id="chart-desc">Performance data has not been published yet.</desc>
                    <path d="M0 178 H800 M0 112 H800 M0 46 H800" fill="none" stroke="var(--border)" strokeWidth="1" />
                    <path d="M0 166 C180 160 275 135 400 139 S610 88 800 72" fill="none" stroke="var(--primary)" strokeDasharray="8 8" strokeWidth="3" opacity=".55" />
                  </svg>
                  <p className="mt-2 font-mono text-[11px] leading-5 text-muted-foreground">Awaiting the first portfolio ledger projection.</p>
                </div>
              </CardContent>
            </Card>
            <Card className="border-border">
              <CardHeader>
                <CardTitle>Headline metrics</CardTitle>
                <CardDescription>Published after ledger reconciliation</CardDescription>
              </CardHeader>
              <CardContent className="space-y-1">
                {[["Net P&L", "-"], ["Today", "-%"], ["Competition return", "-%"], ["Max drawdown", "-%"]].map(([label, value]) => (
                  <div key={label} className="flex items-center justify-between border-t border-border py-3 first:border-t-0 first:pt-0">
                    <span className="text-sm text-muted-foreground">{label}</span>
                    <span className="font-mono text-base font-semibold">{value}</span>
                  </div>
                ))}
              </CardContent>
            </Card>
          </div>
        </section>

        <section className="mb-10" aria-labelledby="risk-title">
          <SectionTitle number="02 / Immutable envelope" title="Risk ruler" description="Hard limits stay outside of model authority." />
          <Card className="mt-5 border-border">
            <CardContent className="grid gap-5 pt-5 sm:grid-cols-2 lg:grid-cols-3">
              {[["Per trade", "1%"], ["Open stop-risk", "4%"], ["Overnight", "2%"], ["Exposure group", "2%"], ["Positions", "6"], ["Competition kill", "12%"]].map(([label, limit]) => (
                <div key={label} className="rounded-xl border border-border bg-muted/60 p-4">
                  <div className="mb-3 flex items-center justify-between font-mono text-[11px] uppercase"><span className="text-muted-foreground">{label}</span><strong>{limit}</strong></div>
                  <div className="h-2 overflow-hidden rounded-full bg-border"><div className="h-full w-0 bg-primary" /></div>
                  <p className="mt-2 text-xs text-muted-foreground">No public exposure projection</p>
                </div>
              ))}
            </CardContent>
          </Card>
        </section>

        <section className="mb-10" aria-labelledby="routes-title">
          <SectionTitle number="03 / Router evidence" title="Recent route allocation" description="Allocation from delayed, sanitized decision records." />
          <div className="mt-5 grid gap-5 lg:grid-cols-[1.65fr_1fr]">
            <Card className="border-border">
              <CardContent className="space-y-5 pt-5">
                {routes.map(({ route, count, percentage }) => (
                  <div key={route}>
                    <div className="mb-2 flex items-center justify-between gap-4 text-sm"><span className="font-medium">{routeLabels[route]}</span><span className="font-mono text-xs text-muted-foreground">{count} decisions / {percentage}%</span></div>
                    <div className="h-2 overflow-hidden rounded-full bg-secondary"><div className={route === "NO_TRADE" ? "h-full bg-muted-foreground" : "h-full bg-primary"} style={{ width: `${percentage}%` }} /></div>
                  </div>
                ))}
              </CardContent>
            </Card>
            <Card className="border-border bg-card">
              <CardHeader><CardTitle>Strategy boundary</CardTitle><CardDescription>Authority remains deterministic.</CardDescription></CardHeader>
              <CardContent className="space-y-4 text-sm">
                <div><p className="font-mono text-[11px] tracking-[0.12em] text-muted-foreground uppercase">Incumbent</p><p className="mt-1 font-medium">Deterministic experts</p></div>
                <div><p className="font-mono text-[11px] tracking-[0.12em] text-muted-foreground uppercase">Deployed challenger</p><p className="mt-1 font-medium">{challenger?.loaded ? challenger.candidate?.replaceAll("_", " ") : "Not loaded"}</p></div>
                <div className="grid grid-cols-2 gap-3 border-t border-border pt-4 font-mono text-[11px]">
                  <div><p className="text-muted-foreground uppercase">Authority</p><p className="mt-1 font-semibold text-destructive">{challenger?.authority ?? "SHADOW ONLY"}</p></div>
                  <div><p className="text-muted-foreground uppercase">Promotion</p><p className="mt-1 font-semibold">{challenger?.promotion_eligible ? "Eligible" : "Blocked"}</p></div>
                  <div><p className="text-muted-foreground uppercase">Validation</p><p className="mt-1 font-semibold">{challenger?.validation_return == null ? "-" : `${(challenger.validation_return * 100).toFixed(2)}%`}</p></div>
                  <div><p className="text-muted-foreground uppercase">Positive folds</p><p className="mt-1 font-semibold">{challenger?.positive_folds ?? "-"} / {challenger?.folds ?? "-"}</p></div>
                </div>
              </CardContent>
            </Card>
          </div>
        </section>

        <section className="mb-10" aria-labelledby="positions-title">
          <SectionTitle number="04 / Position theses" title="Open thesis ledger" description="Positions are delayed and sanitized before publication." />
          <Card className="mt-5 border-border"><CardContent className="py-8 text-sm text-muted-foreground">No sanitized position projection is available in this slice.</CardContent></Card>
        </section>

        <section aria-labelledby="timeline-title">
          <SectionTitle number="05 / Decision history" title="Flight recorder" description="Every delayed, sanitized decision, including no-trade outcomes." />
          <div className="mt-5 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
            <label className="grid gap-1.5 text-sm font-medium sm:w-80">
              <span className="font-mono text-[10px] tracking-[0.1em] text-muted-foreground uppercase">Search records</span>
              <input
                type="search"
                value={decisionSearch}
                onChange={(event) => {
                  setDecisionSearch(event.target.value);
                  resetDecisionPaging();
                }}
                placeholder="Symbol, decision, or summary"
                className="h-8 rounded-lg border border-border bg-background px-3 text-sm outline-none placeholder:text-muted-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/30"
              />
            </label>
            <label className="grid gap-1.5 text-sm font-medium sm:w-56">
              <span className="font-mono text-[10px] tracking-[0.1em] text-muted-foreground uppercase">Route</span>
              <select
                value={decisionRoute}
                onChange={(event) => {
                  setDecisionRoute(event.target.value as Route | "");
                  resetDecisionPaging();
                }}
                className="h-8 rounded-lg border border-border bg-background px-3 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/30"
              >
                <option value="">All routes</option>
                {ROUTES.map((route) => <option key={route} value={route}>{routeLabels[route]}</option>)}
              </select>
            </label>
          </div>
          {decisionPageError ? <div role="alert" className="mt-4 rounded-lg border border-destructive/20 bg-destructive/10 px-4 py-3 text-sm text-destructive">{decisionPageError}</div> : null}
          {decisionPage === null ? (
            <Card className="mt-5 border-border"><CardContent className="py-8 text-sm text-muted-foreground">Loading delayed decision records...</CardContent></Card>
          ) : visibleDecisions.length === 0 ? (
            <Card className="mt-5 border-border"><CardContent className="py-8 text-sm text-muted-foreground">No delayed decisions are visible yet.</CardContent></Card>
          ) : (
            <>
              <Card className="mt-5 overflow-hidden border-border">
                <Table>
                  <TableCaption className="sr-only">Delayed public decision records</TableCaption>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Timestamp</TableHead>
                      <TableHead>Symbol</TableHead>
                      <TableHead>Decision</TableHead>
                      <TableHead>Route</TableHead>
                      <TableHead className="min-w-80">Summary</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {visibleDecisions.map((decision) => (
                      <TableRow key={decision.decision_id}>
                        <TableCell><time dateTime={decision.occurred_at} title={formatTime(decision.occurred_at)} className="font-mono text-[10px] leading-4 whitespace-nowrap text-muted-foreground">{formatRecorderTime(decision.occurred_at)}</time></TableCell>
                        <TableCell className="font-mono text-xs font-semibold">{decision.symbol ?? "SYSTEM"}</TableCell>
                        <TableCell><Badge variant="outline" className="border-border bg-muted px-1.5 py-0 font-mono text-[9px] leading-4 text-muted-foreground">{decision.decision_type.replaceAll("_", " ")}</Badge></TableCell>
                        <TableCell className="font-mono text-[10px] whitespace-nowrap text-muted-foreground">{decision.route ? routeLabels[decision.route] : "System event"}</TableCell>
                        <TableCell title={decision.summary} className="max-w-xl text-xs text-muted-foreground">{decision.summary}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </Card>
              <nav aria-label="Flight recorder pages" className="mt-4 flex flex-col gap-3 text-sm sm:flex-row sm:items-center sm:justify-between">
                <p className="font-mono text-[11px] text-muted-foreground">Showing {firstRecordIndex + 1}-{firstRecordIndex + visibleDecisions.length} matching records</p>
                <div className="flex items-center gap-2">
                  <Button variant="outline" size="sm" disabled={decisionPageIndex === 0} onClick={() => setDecisionPageIndex((page) => page - 1)}>Previous</Button>
                  <span aria-current="page" className="font-mono text-[11px] text-muted-foreground">Page {decisionPageIndex + 1}</span>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={decisionPage.next_cursor === null}
                    onClick={() => {
                      if (decisionPage.next_cursor === null) return;
                      setDecisionPageCursors((cursors) => [...cursors.slice(0, decisionPageIndex + 1), decisionPage.next_cursor]);
                      setDecisionPageIndex((page) => page + 1);
                    }}
                  >
                    Next
                  </Button>
                </div>
              </nav>
            </>
          )}
        </section>

        <footer className="mt-10 flex flex-col gap-2 border-t border-border pt-5 font-mono text-[10px] tracking-[0.08em] text-muted-foreground uppercase sm:flex-row sm:justify-between"><span>Catalyst Router / Alpaca paper trading</span><span>Public data is delayed and is not investment advice.</span></footer>
      </div>
    </main>
  );
}
