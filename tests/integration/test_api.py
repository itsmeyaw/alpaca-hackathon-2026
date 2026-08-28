from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from catalyst_router.adapters.memory import InMemoryOperationalStore
from catalyst_router.api import create_app
from catalyst_router.container import Container
from catalyst_router.domain import (
    AccountSnapshot,
    AgentMode,
    DecisionRecord,
    MarketClockSnapshot,
    ReconciliationSnapshot,
)
from catalyst_router.settings import Settings


def settings(*, auto_reconcile: bool = False) -> Settings:
    return Settings(
        alpaca_key=None,
        alpaca_secret=None,
        state_backend="memory",
        competition_id="test",
        aws_region="us-east-1",
        dynamodb_table="unused",
        dynamodb_endpoint_url=None,
        auto_reconcile=auto_reconcile,
        public_delay_seconds=0,
    )


def test_public_api_is_read_only_and_sanitized() -> None:
    store = InMemoryOperationalStore()
    store.initialize()
    store.append_decision(
        DecisionRecord.create(
            decision_type="NO_TRADE",
            summary="private data quality detail",
            payload={"reason": "stale quote"},
            public=True,
            public_summary="data quality veto",
        )
    )
    app = create_app(Container(settings=settings(), store=store, broker=None))

    with TestClient(app) as client:
        status = client.get("/api/public/status")
        readiness = client.get("/ready")
        decisions = client.get("/api/public/decisions")
        challenger = client.get("/api/public/challenger")
        hidden_operator = client.post("/api/operator/reconcile")

    assert status.status_code == 200
    assert status.json()["mode"] == AgentMode.PAUSED
    assert status.json()["reconciled"] is False
    assert readiness.status_code == 503
    assert decisions.status_code == 200
    assert decisions.json()[0]["summary"] == "data quality veto"
    assert "payload" not in decisions.json()[0]
    assert "execution_epoch" not in status.json()
    assert challenger.json() == {
        "deployed": False,
        "loaded": False,
        "authority": None,
        "run_id": None,
        "created_at": None,
        "candidate": None,
        "feature_schema": None,
        "decision_gate": None,
        "horizon_bars": None,
        "timeframe_minutes": None,
        "symbol_count": None,
        "selection_score": None,
        "validation_return": None,
        "validation_sharpe": None,
        "positive_folds": None,
        "folds": None,
        "holdout_return": None,
        "holdout_sharpe": None,
        "holdout_max_drawdown": None,
        "holdout_trades": None,
        "numeric_shadow_gate_passed": None,
        "promotion_eligible": False,
        "model_sha256": None,
    }
    assert hidden_operator.status_code == 404


class FakePaperBroker:
    def reconciliation_snapshot(self) -> ReconciliationSnapshot:
        now = datetime.now(UTC)
        return ReconciliationSnapshot(
            account=AccountSnapshot(
                equity=Decimal("100000"),
                buying_power=Decimal("400000"),
                cash=Decimal("100000"),
                portfolio_value=Decimal("100000"),
                trading_blocked=False,
                options_trading_level=3,
            ),
            clock=MarketClockSnapshot(
                is_open=False,
                timestamp=now,
                next_open=now + timedelta(hours=12),
                next_close=now + timedelta(hours=18),
            ),
            positions=(),
            open_orders=(),
            captured_at=now,
        )


def test_readiness_requires_current_startup_reconciliation() -> None:
    store = InMemoryOperationalStore()
    app = create_app(
        Container(
            settings=settings(auto_reconcile=True),
            store=store,
            broker=FakePaperBroker(),
        )
    )

    with TestClient(app) as client:
        readiness = client.get("/ready")
        status = client.get("/api/public/status")

    assert readiness.status_code == 200
    assert status.json()["reconciled"] is True
