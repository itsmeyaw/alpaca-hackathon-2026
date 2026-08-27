# ADR-0008: Separate Public Reporting From Operator Control

- Status: Accepted
- Date: 2026-08-27

## Context

Judges need a low-friction demonstration of autonomous behavior, including losing trades and vetoes. Publishing the trading control plane or raw account details would create avoidable security and operational risk.

## Decision

Provide an unauthenticated, read-only dashboard backed by delayed and sanitized reporting projections. Show:

- P&L and drawdown against benchmarks;
- positions with safe public identifiers;
- Event cards and route decisions;
- expert assessments and `NO_TRADE` vetoes;
- grouped risk and circuit-breaker status;
- Policy Profile changes; and
- incumbent-versus-challenger results.

Require operator authentication for pause, resume, flatten, and kill actions. Never expose API credentials, account identifiers, raw prompts, private logs, or mutable trading endpoints publicly.

## Consequences

- Reporting reads projections rather than the execution command path.
- Public DTOs require explicit allowlists and a publication delay.
- Operator actions need authentication, authorization, and audit records.
- A dashboard outage cannot stop or alter the trading process.
