# ADR-0012: Use a Small Event-Driven Market Universe

- Status: Accepted
- Date: 2026-08-27

## Context

The Basic Alpaca plan limits equity WebSocket subscriptions and provides incomplete market coverage. A broad or sub-minute strategy would spread those constraints across too many symbols and amplify feed artifacts.

## Decision

Each premarket session, select approximately 20 liquid, option-enabled underlyings from active stocks and movers. Reserve stream capacity for SPY, QQQ, and sector or regime references. Dynamically subscribe to only the option contracts being evaluated.

Generate Opportunities from Alpaca News, unusual market-relative movement, and corrected one-minute bars. Do not implement a sub-minute latency strategy.

## Consequences

- Universe membership is point-in-time data recorded in each Decision Record.
- The system needs safe dynamic subscribe and unsubscribe behavior.
- A smaller universe makes quote-quality rejection and complete decision trails practical.
- Missing a move outside the selected universe is an accepted tradeoff for higher data and execution quality.
