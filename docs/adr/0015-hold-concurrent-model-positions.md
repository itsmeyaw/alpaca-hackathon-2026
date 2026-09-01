# ADR-0015: Hold Concurrent Model Positions

- Status: Accepted
- Date: 2026-09-01

## Context

The documented Current Risk Posture has always permitted up to six concurrent positions, and
`RiskGovernor.MAX_POSITIONS` encodes that. `LiveTradingCycle` nevertheless serialized exposure to a
single position: the durable Agent State held one `active_order_id`, `claim_order` refused any second
claim, and the cycle returned early whenever any position or open order existed.

That made observed trade frequency roughly one entry per session. An Order Plan carries the
classifier's trained 16-bar (240-minute) horizon, so a single slot is occupied for most of a
390-minute session, and the `+4% / -2%` bracket is rarely reached intraday by the index and mega-cap
names in the universe. The operator wants materially more paper trades across the hackathon week
without relaxing any risk limit.

The single slot also hid a defect. The cycle reported `position_count=0`, `total_open_risk=0`, and an
empty `group_open_risk` to the Risk Governor, because with one position at a time those values were
always zero at entry. Permitting concurrency without fixing that reporting would silently bypass the
portfolio and Exposure Group budgets.

## Decision

Track a bounded set of `active_order_ids` in Agent State instead of one `active_order_id`, and let
`LiveTradingCycle` hold up to `RiskGovernor.MAX_POSITIONS` concurrent entries. `claim_order` enforces
the bound transactionally under the existing state-version and execution-epoch conditions, so the
durable claim remains the authority for new exposure.

Report real portfolio state to the Risk Governor: observed position count, and total and
per-Exposure-Group open stop-risk summed from the tracked Order Plans. Keep every existing limit
unchanged.

Submit at most one entry per cycle, and never a second entry in a symbol that already has a tracked
order, a broker position, or an open order.

Verify protective exits per position rather than globally, and halt only when a held position has no
active bracket. Release a tracked order once it is terminal and its own symbol is flat.

Close an elapsed-horizon position with a new per-symbol `close_position` broker operation instead of
`flatten`, so one expiry no longer liquidates unrelated positions. Portfolio-wide `flatten` remains
the response to drawdown kills, the daily-loss breaker, missing brackets, and end-of-session exit.

Read legacy Agent State that still carries `active_order_id` and fold it into `active_order_ids`.

## Consequences

- Trade frequency rises by up to about six times; gross exposure reaches roughly 60% of equity at six
  positions, each capped at 10% entry notional.
- Total stop-risk at full concurrency is near 1.2% of equity, well inside the 4% total budget, because
  the 10% notional cap binds sizing long before the 1% per-trade risk cap.
- The Exposure Group and portfolio budgets now genuinely bind as positions accumulate; previously
  they were reported as zero and could not.
- Concurrent same-side entries are correlated, so drawdowns will be larger and more highly correlated
  than single-position operation.
- This decision does not change the classifier's measured edge. Its validation hit rate is near
  0.50, so higher frequency raises variance and cost rather than expected return.
