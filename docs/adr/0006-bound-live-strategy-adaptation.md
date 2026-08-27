# ADR-0006: Bound Live Strategy Adaptation

- Status: Accepted
- Date: 2026-08-27

## Context

The team wants the agent to adapt during the five judged sessions. Unbounded optimization from a small live sample could increase risk, overfit recent outcomes, and make decisions irreproducible.

## Decision

Permit an Adaptive Policy to change only:

- risk allocation among already validated routes; and
- route entry-confidence thresholds within prevalidated ranges.

Do not permit adaptation of:

- per-trade, grouped, daily, overnight, or competition risk limits;
- circuit breakers or the operator kill switch;
- quote freshness, liquidity, and other data-quality gates;
- order validation and execution safeguards;
- Event and Trade Intent schemas;
- feature definitions, LLM prompts, or the set of available strategies; or
- Challenger Model promotion.

Every adaptive change must have a version, supporting evidence, timestamp, previous value, new value, and deterministic rollback path.

Use a constrained contextual Thompson sampler to choose among three fixed Policy Profiles: `CONSERVATIVE`, `BASE`, and `AGGRESSIVE`. Establish their parameter values through pre-competition validation and start live judging with `BASE`.

Update the sampler only when a Position Thesis resolves. Normalize its after-cost P&L by the position's initial risk and clip the reward to the prevalidated reward bounds. Rejected opportunities and unresolved mark-to-market outcomes do not update the live policy.

## Consequences

- Adaptation cannot bypass the Risk Governor.
- The dashboard must distinguish policy changes from ordinary trade decisions.
- Backtests must exercise the same adaptation bounds used live.
- Policy state and priors must survive restarts without replaying a reward twice.
- Sparse live evidence will usually preserve strong pre-competition priors rather than cause large profile changes.
