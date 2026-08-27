# ADR-0010: Use DynamoDB for Operational State

- Status: Accepted
- Date: 2026-08-27

## Context

The agent needs durable state for idempotent execution, restart reconciliation, adaptive policy updates, and dashboard queries. A one-week competition does not justify operating a relational database instance, and the required access patterns are known and key-oriented.

## Decision

Use DynamoDB as the authoritative local operational store. Model access patterns for:

- current Agent Mode and circuit-breaker state;
- active Opportunities and Position Theses;
- chronological Decision Records;
- orders by Alpaca ID and deterministic client order ID;
- current positions and Exposure Groups;
- Policy Profile versions and deduplicated rewards; and
- public dashboard projections by competition and trading session.

Use conditional writes for idempotency and optimistic concurrency. Use DynamoDB transactions where a state transition must atomically update risk, order, or policy records.

Store raw and high-volume Market Observations in S3 using replayable, time-partitioned objects. Do not use DynamoDB as an unbounded tick store. Alpaca remains authoritative for external account, order, and position state.

## Consequences

- Table keys and secondary indexes must be designed from explicit access patterns.
- Reporting uses maintained projections instead of relational joins.
- Startup reconciliation can overwrite stale local projections but cannot silently erase Decision Records.
- Point-in-time recovery, TTL, encryption, and on-demand capacity are enabled through Terraform.
- Local development needs a DynamoDB-compatible adapter or emulator plus recorded S3 fixtures.
