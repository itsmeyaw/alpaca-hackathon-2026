# Catalyst Router Domain

## Purpose

Catalyst Router is an autonomous paper-trading agent competing for terminal P&L over a one-week Alpaca hackathon. It uses an LLM to classify new information and deterministic code to select trades, size risk, submit orders, and enforce portfolio limits.

## Core Concepts

### Market Observation

A timestamped price, quote, bar, news item, market status, or account update. Every downstream decision records the observations and data feed that supported it.

### Opportunity

A symbol and time horizon selected for evaluation because of a new Event, unusual market-relative movement, or regime evidence. An Opportunity can expire without producing a Trade Intent.

### Event

A structured interpretation of news that includes type, direction, magnitude, novelty, surprise, expected horizon, confidence, affected symbols, and invalidating evidence. An Event is evidence, not permission to trade.

### Expert Assessment

A deterministic or structured assessment produced by the catalyst, momentum, reversion, regime, or risk expert.

### Incumbent Strategy

The deterministic scoring and routing policy authorized to create live Trade Intents. Its parameters are versioned so every decision can be reproduced.

### Challenger Model

A trained model evaluated against the Incumbent Strategy without authority to place orders. A challenger requires chronological walk-forward evidence after costs before it can be promoted outside the judged window.

### Adaptive Policy

A bounded policy that may change route-allocation weights and entry-confidence thresholds within prevalidated ranges. It cannot change risk limits, execution safeguards, data-quality requirements, event schemas, or the available strategies.

### Policy Profile

One of the prevalidated `CONSERVATIVE`, `BASE`, or `AGGRESSIVE` combinations of route allocation and entry threshold. Profiles use fixed, versioned parameter values established before judging; the Adaptive Policy selects profiles but cannot synthesize parameters.

### Resolved Thesis

A Position Thesis whose exit and after-cost outcome are known. Its clipped return in units of initial risk is the reward supplied to the Adaptive Policy.

### Route

The selected interpretation of an opportunity:

- `CATALYST_CONTINUATION`: new information and market confirmation imply continuation.
- `LIQUIDITY_REVERSION`: an unexplained, market-relative displacement implies reversion.
- `REGIME_TREND`: broad market conditions justify a small index position.
- `NO_TRADE`: evidence, liquidity, data quality, or risk is insufficient.

### Trade Intent

A typed proposal containing the route, thesis, instrument, direction, entry constraints, invalidation, expected horizon, and supporting evidence. It has no authority to place an order until approved by the Risk Governor.

### Risk Decision

The Risk Governor's immutable record of approving, reducing, or vetoing a Trade Intent, including every evaluated invariant and the account snapshot used for sizing.

### Order Plan

The deterministic translation of an approved Trade Intent into idempotent Alpaca orders and exit instructions. Alpaca trade updates, followed by reconciliation, are authoritative for order state.

### Risk Governor

The deterministic authority that sizes positions and either approves, reduces, or vetoes a Trade Intent. The LLM cannot bypass it or choose order quantity.

### Position Thesis

The accepted Trade Intent and invalidation conditions attached to an open position. Price stops, elapsed horizon, contradictory evidence, and portfolio limits can invalidate it.

### Exposure Group

Positions expected to lose together because they share a sector, underlying driver, or market direction. The Risk Governor budgets an Exposure Group as one correlated risk rather than counting its positions as independent trades.

### Decision Record

An immutable, timestamped account of observations, expert assessments, route selection, risk checks, order lifecycle, and outcome, including decisions that result in `NO_TRADE`.

### Agent Mode

The operational authority state: `RUNNING`, `PAUSED`, `RISK_HALTED`, or `KILLED`. Only `RUNNING` permits new exposure. Restarting the process does not clear a halt or kill state.

## Decision Lifecycle

```text
Market Observation
  -> Opportunity
  -> Event and Expert Assessments
  -> Route
  -> Trade Intent
  -> Risk Decision
  -> Order Plan
  -> Position Thesis
  -> Resolved Thesis
  -> Adaptive Policy update
```

Any stage may terminate in `NO_TRADE`. Termination is a Decision Record, not an error.

## Module Boundaries

- **Market Intelligence** owns feed normalization, point-in-time features, Opportunities, and Events.
- **Decision Engine** owns Expert Assessments, route selection, Trade Intents, and Challenger Model predictions.
- **Risk and Execution** owns Risk Decisions, Agent Mode, sizing, Order Plans, reconciliation, and exits.
- **Portfolio Ledger** owns positions, realized and unrealized P&L, exposure groups, and performance attribution.
- **Adaptation** owns Policy Profiles, priors, rewards, and versioned Adaptive Policy changes.
- **Reporting** projects sanitized Decision Records into the public dashboard without controlling trading.

## Trading Mandate

- Strong catalyst-continuation opportunities may use defined-risk options.
- Liquidity-reversion opportunities use equities.
- Naked short options are prohibited.
- The system uses Alpaca's free market-data plan initially: IEX equities and indicative options pricing.
- Incomplete, stale, or contradictory data must produce `NO_TRADE` rather than an inferred price.
- Execution is autonomous after deterministic approval, with an operator kill switch.
- Trained return models begin as Challenger Models and cannot affect live orders without prior promotion.
- Every Adaptive Policy change is versioned, attributable to evidence, and reversible.
- The Adaptive Policy learns only from Resolved Theses and starts the competition with the `BASE` Policy Profile.

## Market Scope

- Build a daily universe of approximately 20 liquid option-enabled underlyings from active stocks and movers.
- Reserve the remaining Basic-plan equity stream capacity for SPY, QQQ, and sector or regime references.
- Dynamically subscribe only to option contracts under active evaluation and remain within the Basic-plan stream limit.
- Use Alpaca News as the initial catalyst source.
- Evaluate on news arrival and corrected one-minute bars; do not pursue sub-minute latency strategies on IEX and indicative option data.

## Agent Authority

- The Bedrock-hosted LLM emits typed Events and explanations only.
- `alpaca-py` is the runtime integration for streams, account reconciliation, market data, and orders.
- Alpaca MCP exposes read-oriented research and demonstration tools to the LLM; it does not grant the LLM an ungoverned order path.
- The Alpaca CLI is an operator and judging demonstration surface, including dry runs and account inspection.
- Only Risk and Execution can create an Order Plan or submit an order.

## Current Risk Posture

The accepted posture is competition-aggressive but survival-aware:

- Risk at most 1% of equity on one trade.
- Hold at most six positions concurrently.
- Limit total open stop-risk to 4% of equity.
- Limit overnight stop-risk to 2% of equity.
- Limit one Exposure Group to 2% of equity.
- At a 2% daily drawdown, reduce new-trade risk to 0.5% of equity.
- Stop opening risk and begin flattening at a 4% daily loss.
- Kill autonomous trading at a 12% competition drawdown.

## Holding Policy

- A catalyst option position may remain open overnight only when the catalyst has already occurred, confidence is high, and the expected horizon extends beyond the close.
- Liquidity-reversion equity positions must close during the entry session.
- Do not hold an option through expiration.
- Flatten all positions before the final judging cutoff.

## Option Contract Policy

- Live option trades are long calls or long puts only.
- Target 14 to 30 calendar days to expiration and approximately 0.55 to 0.70 absolute delta.
- Prohibit 0DTE contracts and short option legs.
- Require complete, fresh quotes and strict liquidity checks; otherwise route to `NO_TRADE`.
- Evaluate debit spreads in shadow mode until consolidated option quotes are available and validated.

## Dashboard Policy

- The public dashboard is read-only and shows delayed, sanitized decisions, P&L, drawdown, routes, vetoes, and policy changes.
- Account identifiers, secrets, raw prompts, internal logs, and operator actions are private.
- Pause, resume, flatten, and kill controls require operator authentication.

## Persistence Policy

- DynamoDB is the authoritative operational store for agent mode, opportunities, decisions, orders, positions, risk state, policies, idempotency records, and dashboard projections.
- Use conditional writes and transactions for state transitions that must not be applied twice.
- Archive raw and high-volume market observations to S3 rather than making DynamoDB a tick store.
- Alpaca remains authoritative for external account, order, and position state; startup reconciliation repairs local projections before trading resumes.

## Operating Cycle

- **Premarket:** reconcile Alpaca state, check Agent Mode, select the universe, ingest news, and establish regime context.
- **Regular session:** evaluate Opportunities on events and corrected minute bars, manage orders and Position Theses, and publish delayed reporting projections.
- **Before close:** close liquidity-reversion positions, reevaluate every overnight candidate, and enforce overnight risk limits.
- **After close:** reconcile outcomes, resolve completed theses, update the Adaptive Policy once per new reward, archive observations, and produce the daily report.
- **Final session:** stop new entries early enough to flatten and reconcile every position before the judging cutoff.

## Validation Policy

- Validate with chronological walk-forward splits and purged overlapping labels.
- Include point-in-time universe construction, inference and order latency, bid/ask execution, slippage, and estimated costs.
- Compare against cash, SPY, equal-weight, basic momentum, and an incumbent without LLM event routing.
- Freeze Policy Profile definitions, safety limits, features, and strategy code before judging; only profile selection adapts live.
