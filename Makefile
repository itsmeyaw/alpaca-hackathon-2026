.PHONY: check test test-integration train-local dashboard-build deploy-dashboard docker-build infra-fmt

check:
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy
	uv run pytest
	$(MAKE) test-integration

test:
	uv run pytest

test-integration:
	docker compose up -d dynamodb
	DYNAMODB_ENDPOINT_URL=http://localhost:8001 AWS_ACCESS_KEY_ID=local AWS_SECRET_ACCESS_KEY=local uv run pytest -m integration tests/integration/test_dynamodb_store.py

train-local:
	uv run --group training scripts/train-challengers

dashboard-build:
	npm --prefix dashboard run typecheck
	npm --prefix dashboard run build

deploy-dashboard: dashboard-build
	scripts/deploy-dashboard

docker-build:
	docker build --platform linux/amd64 -t catalyst-router:local .

infra-fmt:
	terraform fmt -check -recursive infra
