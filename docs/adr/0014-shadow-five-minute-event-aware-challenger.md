# ADR-0014: Shadow a Five-Minute Event-Aware Challenger

- Status: Accepted
- Date: 2026-08-29

## Context

ADR-0013 authorizes one specific 15-minute classifier for bounded paper execution despite incomplete
promotion evidence. Increasing signal cadence and adding LLM-derived news evidence changes the data
distribution, feature semantics, operational load, and model risk. It is not covered by that
authorization.

## Decision

Develop the next Challenger Model on completed five-minute IEX bars with a 48-bar, four-hour target.
Use duration-named `bar-features-v3`, cost-aware labels, one-position evaluation, chronological
purging, and immutable partitioned dataset manifests.

Use Bedrock only to convert Alpaca News into typed Events. A deterministic Decision Engine may use
validated Event direction, novelty, confidence, and horizon as route evidence. Bedrock cannot size a
position, create an Order Plan, submit an order, or bypass a Risk Decision. Missing, stale, malformed,
or unbounded output produces `NO_TRADE`.

Run both the five-minute Challenger Model and Event route as `SHADOW_ONLY`. Paper authority requires
a later ADR naming the exact artifact and evidence. ADR-0013 continues to govern only the existing
15-minute classifier.

## Consequences

- The worker may poll every 15 seconds while evaluating each completed five-minute bar once.
- Runtime feature freshness is limited to seven minutes.
- Training and runtime must reject artifacts that do not use five-minute bars and a 48-bar horizon.
- Shadow records include model, prompt, source, token, and request provenance without exposing raw
  prompts or article content publicly.
- Forward executable-price evidence is required before promotion.
