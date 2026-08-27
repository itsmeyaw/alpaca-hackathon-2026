"use client";

import { startTransition, useEffect, useState } from "react";

import {
  type PublicDecision,
  type PublicSnapshot,
  type Route,
  fetchPublicSnapshot,
} from "../lib/api";

const ROUTES: Route[] = [
  "CATALYST_CONTINUATION",
  "LIQUIDITY_REVERSION",
  "REGIME_TREND",
  "NO_TRADE",
];

const routeLabels: Record<Route, string> = {
  CATALYST_CONTINUATION: "Catalyst continuation",
  LIQUIDITY_REVERSION: "Liquidity reversion",
  REGIME_TREND: "Regime trend",
  NO_TRADE: "No trade",
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

export function Dashboard() {
  const [snapshot, setSnapshot] = useState<PublicSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);

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

  const status = snapshot?.status;
  const decisions = snapshot?.decisions ?? [];
  const routes = routeBreakdown(decisions);
  const exposureMessage =
    status?.mode === "RUNNING" && status.reconciled
      ? "New exposure may be evaluated by the Risk Governor."
      : "New exposure is blocked.";

  return (
    <main>
      <header className="masthead">
        <div>
          <p className="eyebrow">Options Alpha Agents / Public record</p>
          <h1>Catalyst Router</h1>
        </div>
        <div className="publication">
          <strong>READ-ONLY</strong>
          <span>Sanitized projection · 15 min delay</span>
          <span>{snapshot ? `Received ${formatTime(snapshot.receivedAt)}` : "Connecting…"}</span>
        </div>
      </header>

      {error ? <p className="connection-error">{error}. Retrying every 30 seconds.</p> : null}

      <section className={`authority mode-${status?.mode.toLowerCase() ?? "unknown"}`}>
        <div className="authority-primary">
          <span>Agent authority</span>
          <strong>{status?.mode ?? "UNKNOWN"}</strong>
        </div>
        <dl>
          <div>
            <dt>Reconciliation</dt>
            <dd>{status ? (status.reconciled ? "MATCHED" : "PENDING") : "UNKNOWN"}</dd>
          </div>
          <div>
            <dt>Policy profile</dt>
            <dd>BASE</dd>
          </div>
          <div>
            <dt>Execution</dt>
            <dd>{exposureMessage}</dd>
          </div>
        </dl>
      </section>

      <section className="performance" aria-labelledby="performance-title">
        <div className="section-heading">
          <p>01 / Performance field</p>
          <h2 id="performance-title">Account ledger awaiting projection</h2>
        </div>
        <div className="chart-placeholder" aria-label="Equity chart unavailable in this slice">
          <svg viewBox="0 0 800 210" role="img" aria-labelledby="chart-title chart-desc">
            <title id="chart-title">Portfolio equity placeholder</title>
            <desc id="chart-desc">Performance data has not been published yet.</desc>
            <path d="M0 178 H800 M0 112 H800 M0 46 H800" className="grid-line" />
            <path d="M0 166 C180 160 275 135 400 139 S610 88 800 72" className="ghost-line" />
          </svg>
          <span>Equity, SPY, and cash baselines will appear after the portfolio ledger lands.</span>
        </div>
        <dl className="headline-metrics">
          {[
            ["Net P&L", "—"],
            ["Today", "—%"],
            ["Competition return", "—%"],
            ["Max drawdown", "—%"],
          ].map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section className="risk-section" aria-labelledby="risk-title">
        <div className="section-heading">
          <p>02 / Immutable envelope</p>
          <h2 id="risk-title">Risk ruler</h2>
        </div>
        <div className="risk-rulers">
          {[
            ["Per trade", "1%"],
            ["Open stop-risk", "4%"],
            ["Overnight", "2%"],
            ["Exposure group", "2%"],
            ["Positions", "6"],
            ["Competition kill", "12%"],
          ].map(([label, limit]) => (
            <div className="risk-row" key={label}>
              <span>{label}</span>
              <div className="risk-track"><i style={{ width: "0%" }} /></div>
              <b>— / {limit}</b>
            </div>
          ))}
        </div>
      </section>

      <section className="routes" aria-labelledby="routes-title">
        <div className="section-heading">
          <p>03 / Router evidence</p>
          <h2 id="routes-title">Recent route allocation</h2>
        </div>
        <div className="route-list">
          {routes.map(({ route, count, percentage }) => (
            <div className={`route-row route-${route.toLowerCase()}`} key={route}>
              <span>{routeLabels[route]}</span>
              <div><i style={{ width: `${percentage}%` }} /></div>
              <b>{percentage}%</b>
              <em>{count} decisions</em>
            </div>
          ))}
        </div>
        <aside>
          <strong>Incumbent</strong>
          <span>Deterministic experts</span>
          <strong>Challenger</strong>
          <span>Shadow only · no order authority</span>
        </aside>
      </section>

      <section className="positions" aria-labelledby="positions-title">
        <div className="section-heading">
          <p>04 / Position theses</p>
          <h2 id="positions-title">Open thesis ledger</h2>
        </div>
        <p>No sanitized position projection is available in this slice.</p>
      </section>

      <section className="timeline" aria-labelledby="timeline-title">
        <div className="section-heading">
          <p>05 / Decision history</p>
          <h2 id="timeline-title">Flight recorder</h2>
        </div>
        {decisions.length === 0 ? (
          <p className="empty-timeline">No delayed decisions are visible yet.</p>
        ) : (
          <ol>
            {decisions.map((decision) => (
              <li key={decision.decision_id}>
                <time dateTime={decision.occurred_at}>{formatTime(decision.occurred_at)}</time>
                <div>
                  <span className="decision-type">{decision.decision_type.replaceAll("_", " ")}</span>
                  <h3>{decision.symbol ?? "SYSTEM"}</h3>
                  <p>{decision.summary}</p>
                </div>
                <code>{decision.route ? routeLabels[decision.route] : "System event"}</code>
              </li>
            ))}
          </ol>
        )}
      </section>

      <footer>
        <span>Catalyst Router / Alpaca paper trading</span>
        <span>Public data is delayed and is not investment advice.</span>
      </footer>
    </main>
  );
}
