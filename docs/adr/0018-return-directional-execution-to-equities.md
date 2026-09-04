# ADR-0018: Return Directional Execution to Equities

- Status: Accepted
- Date: 2026-09-03

## Context

The first paper-live option session opened six long calls and puts. Indicative option quote events
repeatedly exceeded the five-second freshness threshold; one EWZ quote remained stale through the
60-second supervision grace and caused a portfolio-wide risk halt and flatten. The option trades lost
approximately `$194` including the earlier GDX trade, while the authorized model was trained and
validated on underlying equity returns rather than option-premium returns.

## Decision

Disable `MODEL_OPTIONS_EXECUTION_ENABLED` in production. Continue using the dynamic daily universe,
but route bullish model signals to long equities and bearish signals to short equities through
server-hosted Alpaca bracket orders. Keep the 5% aggregate and Exposure Group risk limits and all
other circuit breakers unchanged.

The option implementation remains gated off by default and may be evaluated in shadow mode, but it
requires a separate decision before receiving paper-live authority again.

## Consequences

- Live stop-loss and take-profit protection is hosted by Alpaca rather than the worker process.
- Returns more closely match the instrument class used by the authorized model's validation.
- Option leverage and defined-premium loss are no longer part of live execution.
- The runtime no longer depends on indicative option quote freshness to supervise open positions.
