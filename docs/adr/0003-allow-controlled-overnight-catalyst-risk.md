# ADR-0003: Allow Controlled Overnight Catalyst Risk

- Status: Accepted
- Date: 2026-08-27

## Context

The competition has five judged market sessions. Forcing every position to close each day would discard post-catalyst drift and reduce the number of useful holding periods, but overnight gaps make short-horizon reversion positions and expiring options unsafe.

## Decision

Allow a catalyst option position to remain open overnight only when:

- the catalyst has already occurred;
- the structured Event has high confidence;
- price and volume confirm the event direction;
- the expected event horizon extends beyond the close;
- the option will not expire during the holding period; and
- the Risk Governor approves the overnight exposure.

Liquidity-reversion equity positions remain intraday. The agent must not hold options through expiration and must flatten all positions before the final judging cutoff.

## Consequences

- The Risk Governor needs separate intraday and overnight budgets.
- Position Theses survive process restarts and are reevaluated before the next open.
- Gap scenarios must be included in sizing and validation.
- The scheduler needs explicit pre-close and final-competition liquidation workflows.
