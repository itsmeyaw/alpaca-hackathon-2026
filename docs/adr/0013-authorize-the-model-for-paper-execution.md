# ADR-0013: Authorize the Model for Paper Execution

- Status: Accepted
- Date: 2026-08-28

## Context

ADR-0005 kept trained models in shadow mode until they had stable chronological evidence after
costs. The selected regularized XGBoost classifier has positive aggregate validation and diagnostic
holdout results, but only two of four validation folds are positive and the holdout was inspected
during development. It does not meet the original promotion standard.

The competition operator has explicitly chosen greater paper-trading aggressiveness and accepts the
model risk. The Alpaca account is paper-only, and deterministic risk and execution controls remain
available outside model authority.

## Decision

Authorize the deployed classifier to create long and short equity Trade Intents in Alpaca paper
trading when its directional probability is at least `0.52`. Evaluate only completed 15-minute bars
and begin with the next completed bar after deployment. Poll every 15 seconds to reduce post-close
entry latency without treating repeated polls as new observations.

Model Trade Intents remain subject to fresh IEX quotes, spread limits, market hours, reconciliation,
deterministic Risk Governor sizing, the concurrent-position bound of ADR-0015, idempotent order
claims, protected bracket exits, drawdown circuit breakers, and end-of-session flattening. The model
cannot choose quantity or bypass a veto.

## Consequences

- Runtime authority is reported as `PAPER_LIVE`, while the artifact retains its recorded shadow
  validation history.
- A prediction at or above `0.52` opens a long; one at or below `0.48` opens a short.
- Lower confidence increases trade frequency and expected false positives.
- Short entries add borrow-availability and rejection risk that historical bar validation did not
  model.
- This decision does not claim that the original promotion evidence was satisfied.
