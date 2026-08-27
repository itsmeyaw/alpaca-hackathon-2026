# ADR-0007: Limit Live Option Structures With Indicative Data

- Status: Accepted
- Date: 2026-08-27

## Context

The Basic Alpaca plan provides indicative option pricing rather than consolidated real-time OPRA data. Multi-leg structures multiply quote and fill uncertainty, while 0DTE contracts add extreme gamma, expiry, and missing-Greeks risk.

## Decision

The first live agent may buy calls and puts only. Target contracts with:

- 14 to 30 calendar days to expiration;
- approximately 0.55 to 0.70 absolute delta;
- complete and fresh bid/ask quotes;
- sufficient volume and open interest; and
- a spread and estimated slippage below the configured liquidity limits.

Prohibit 0DTE contracts and every short option leg. Run debit-spread selection in shadow mode until consolidated option quotes are available and its execution assumptions have been validated.

## Consequences

- Maximum option loss is the premium paid and must fit the stop-based risk budget.
- Contract selection remains a deterministic step after route selection.
- Missing Greeks or an unreliable quote causes `NO_TRADE`; it is never imputed for execution.
- The system needs active exit management because equity bracket-order behavior cannot be assumed for options.
