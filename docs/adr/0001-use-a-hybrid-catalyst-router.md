# ADR-0001: Use a Hybrid Catalyst Router

- Status: Accepted
- Date: 2026-08-27

## Context

The agent is judged primarily on Alpaca paper-trading P&L during a one-week competition. The research supports distinguishing information-driven continuation from temporary liquidity-driven reversal and cautions against unconstrained LLM trading. The options-focused challenge rewards an options component, while relying on options for every signal would add data, liquidity, and execution risk.

## Decision

Build a hybrid Catalyst Router:

- Use defined-risk options for sufficiently strong catalyst-continuation trades.
- Use equities for short-horizon liquidity-reversion trades.
- Permit a small equity-index regime route only when independently justified.
- Route ambiguous opportunities to `NO_TRADE`.
- Use the LLM only for structured event extraction and explanation.
- Keep forecasting, sizing, risk approval, and execution deterministic.
- Prohibit naked short options.

## Consequences

- The domain model must represent an opportunity independently from its execution instrument.
- Options selection needs explicit liquidity and data-quality gates.
- Equity and options positions can share a thesis but require different execution and exit policies.
- Every LLM output must pass typed validation and the Risk Governor.
- Performance must be attributable by route and instrument.
