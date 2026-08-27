# ADR-0004: Budget Correlated Portfolio Risk

- Status: Accepted
- Date: 2026-08-27

## Context

The agent may hold six positions, but position count does not measure diversification. Several technology calls or short reversion trades can express one market bet and reach their stops together.

## Decision

Calculate risk from loss at each position's invalidation or stop, not from market value alone. Enforce these limits against current equity:

- 1% maximum initial risk for one trade;
- 4% maximum aggregate open stop-risk;
- 2% maximum overnight stop-risk;
- 2% maximum risk in one correlated Exposure Group;
- six concurrent positions;
- 0.5% maximum risk for new trades after a 2% daily drawdown;
- flatten and stop after a 4% daily loss; and
- disable autonomous trading after a 12% competition drawdown.

Exposure Groups include a common underlying, sector and direction, or a shared catalyst. The most conservative applicable grouping controls.

## Consequences

- Every Trade Intent needs an Exposure Group before sizing.
- The dashboard must show stop-risk and grouped exposure, not only notional value.
- A valid individual trade can be reduced or vetoed because of existing correlated positions.
- Risk calculations must be rerun after fills, partial fills, stop changes, and material gaps.
