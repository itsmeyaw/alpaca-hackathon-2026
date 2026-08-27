# ADR-0002: Start With Free Alpaca Market Data

- Status: Accepted
- Date: 2026-08-27

## Context

Algo Trader Plus costs $99 per month and provides consolidated SIP equity data and real-time OPRA options data. The competition lasts one week, and the team does not want to purchase the plan initially.

## Decision

Start with Alpaca's Basic market-data plan, using real-time IEX equity data and the indicative options feed. Do not silently substitute delayed consolidated data for current prices.

Treat feed identity, quote timestamp, spread, and quote completeness as inputs to every options decision. A Trade Intent must be vetoed when the available feed cannot establish an executable price with sufficient confidence.

## Consequences

- The tradable universe must favor highly liquid underlyings and option contracts.
- Options opportunities may be rejected more often than they would be with OPRA.
- Backtests and live reports must identify the feed used so their results are not conflated.
- Upgrading to Algo Trader Plus remains a reversible operational decision if free-feed rejection rates or pricing discrepancies become material.
