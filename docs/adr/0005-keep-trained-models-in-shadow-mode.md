# ADR-0005: Keep Trained Models in Shadow Mode

- Status: Accepted
- Date: 2026-08-27

## Context

Tree models may improve nonlinear return forecasts, but a short competition encourages overfitting and makes weak validation difficult to detect. The event-routing hypothesis can be expressed as deterministic, testable expert scores without making a trained model part of the execution critical path.

## Decision

Use deterministic catalyst, momentum, reversion, regime, and risk scores as the Incumbent Strategy.

Train LightGBM or XGBoost only as a Challenger Model. Record its predictions beside incumbent decisions, but do not let those predictions alter live Trade Intents. Promotion requires chronological walk-forward validation, purged overlapping labels, point-in-time inputs, conservative execution costs, and better results than the incumbent and simple baselines.

## Consequences

- The first live system remains explainable and reproducible.
- The decision schema must store incumbent and challenger outputs separately.
- The dashboard can demonstrate AI/ML comparison without presenting shadow returns as traded P&L.
- Promotion is a deliberate versioned decision, not an automatic response to a few competition outcomes.
