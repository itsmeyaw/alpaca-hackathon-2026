# ADR-0017: Increase Concurrent Risk Capacity

- Status: Accepted
- Date: 2026-09-03

## Context

Long-option entry orders reserve their full premium risk while resting at Alpaca. The existing 2%
Exposure Group cap can therefore block additional symbols after only a few bounded orders, even
though the portfolio permits six concurrent positions. Raising only the group cap would still leave
the existing 4% aggregate cap as the binding limit.

## Decision

Increase both maximum aggregate open risk and maximum risk in one correlated Exposure Group to 5%
de-risking, and circuit breakers unchanged.

## Consequences

- Up to five full-risk entries can share one Exposure Group when no other capacity binds.
- Resting orders continue to reserve risk before they fill.
- Correlated intraday losses can reach 5% of equity, while overnight exposure remains capped at 2%.
