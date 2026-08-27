# Catalyst Router

Catalyst Router is an autonomous Alpaca paper-trading agent. The first vertical slice is deliberately read-only: it validates paper credentials, reconciles account state, evaluates deterministic route and risk decisions, and exposes sanitized status APIs. It contains no order-submission method.

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

The API starts in `PAUSED`. Open `http://127.0.0.1:8000/docs` or query:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/api/public/status
```

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

Order execution remains absent until DynamoDB idempotency, reconciliation, and risk-reservation integration tests are complete.

## Local Challenger Training

Train price-only challenger models from cached raw IEX bars:

```bash
make train-local
```

The runner compares deterministic baselines, linear models, histogram boosting, and XGBoost
with purged chronological walk-forward validation and a diagnostic holdout. Data and model
artifacts stay under the ignored `.local/training/` directory. Every resulting manifest has
`authority=SHADOW_ONLY`; numeric results cannot promote a model or affect a `TradeIntent`.

See `docs/training/local-benchmark.md` for the current winner, experiment history, and limits.

The provisioned AWS table can be exercised read-only with:

```bash
STATE_BACKEND=dynamodb \
DYNAMODB_TABLE=catalyst-router-prod-operational \
COMPETITION_ID=alpaca-hackathon-2026 \
uv run catalyst-router reconcile
```
