# Catalyst Router

Catalyst Router is an autonomous Alpaca paper-trading agent. It holds up to six long or short equity positions at a time from completed-bar model signals, entering at most one per cycle. Every entry is risk-sized, idempotently claimed in DynamoDB, and submitted to Alpaca paper trading as a day bracket with server-hosted stop-loss and take-profit orders.

## Setup

The existing `.env` should contain:

```text
ALPACA_KEY=...
ALPACA_SECRET=...
```

Install and verify:

```bash
uv sync
uv run pytest
uv run catalyst-router check-alpaca
uv run catalyst-router reconcile
uv run catalyst-router serve
```

The API starts in `PAUSED`. Operator mode changes use the authenticated local/AWS execution identity, not the public API:

```bash
uv run catalyst-router resume --reason "paper competition start"
uv run catalyst-router pause --reason "operator review"
uv run catalyst-router flatten --reason "close all paper exposure"
```

Production control commands are authenticated and authorized by the caller's AWS IAM
credentials and DynamoDB permissions. They are never exposed through App Runner or CloudFront.

Open `http://127.0.0.1:8000/docs` or query:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/api/public/status
```

To run the dashboard locally, start the API first, then run `npm --prefix dashboard run dev`.
Its development configuration calls the local API at `http://127.0.0.1:8000`; production keeps
same-origin `/api/*` requests, which CloudFront proxies to the reporting API.

## Alpaca CLI

Install the alpha CLI and use the paper-only wrapper, which maps this repository's environment-variable names without creating a credential profile:

```bash
brew install alpacahq/tap/cli
scripts/alpaca-paper doctor
scripts/alpaca-paper account get --jq '{status, trading_blocked, options_trading_level}'
scripts/alpaca-paper order submit --symbol AAPL --side buy --qty 1 --type market --dry-run
```

The wrapper rejects `--live` and mutating commands. It permits `order submit` only with `--dry-run`, so production execution remains owned by the deterministic Python gateway.

## State Backends

Local development defaults to an in-memory state store. To use DynamoDB:

```text
STATE_BACKEND=dynamodb
DYNAMODB_TABLE=catalyst-router-local
DYNAMODB_ENDPOINT_URL=http://localhost:8001
AWS_REGION=us-east-1
```

Set `MODEL_AUTHORITY=PAPER_LIVE` and `MODEL_DECISION_GATE=0.52` on both runtimes so reporting matches execution. Set `PAPER_EXECUTION_ENABLED=true` and `MODEL_EXECUTION_ENABLED=true` only on the private worker. The gate authorizes long predictions at or above 0.52 and short predictions at or below 0.48. `RUNNING` mode, a current reconciliation epoch, an empty paper account, a fresh signal and quote, and deterministic Risk Governor approval are all required before submission. The worker polls every 15 seconds but the authorized model evaluates each completed 15-minute bar once with a four-hour horizon.

Set `LLM_EVENTS_ENABLED=true`, `BEDROCK_MODEL_ID`, and `BEDROCK_PROMPT_VERSION=event-v1`
on the private worker to enable shadow Event routing. Bedrock is forced through a typed Event tool
schema. Its output can provide evidence to the deterministic `DecisionEngine`, but it cannot create
an Order Plan, choose quantity, or bypass the Risk Governor. Invalid output records `NO_TRADE`.
Production Terraform additionally requires the exact `bedrock_model_arn` for least-privilege IAM.
`model_paper_execution_enabled` defaults to `false`; runtime rejects paper authorization unless the
artifact matches the ADR-0013 15-minute contract.

## Local Challenger Training

Train price-only challenger models from cached raw IEX bars:

```bash
make train-local
```

The runner compares deterministic baselines, linear models, histogram boosting, and XGBoost
with purged chronological walk-forward validation and a diagnostic holdout. It uses five-minute
bars, a 48-bar horizon, cost-aware directional labels, and one-position evaluation. Source bars are
also materialized as checksummed, symbol/year-partitioned Parquet datasets. Data and model artifacts
stay under the ignored `.local/training/` directory. New artifacts remain `SHADOW_ONLY`; ADR-0013
authorizes only the existing 15-minute deployment.

See `docs/training/local-benchmark.md` for the current winner, experiment history, and limits.

The provisioned AWS table can be exercised read-only with:

```bash
STATE_BACKEND=dynamodb \
DYNAMODB_TABLE=catalyst-router-prod-operational \
COMPETITION_ID=alpaca-hackathon-2026 \
uv run catalyst-router reconcile
```
