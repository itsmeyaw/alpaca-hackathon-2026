from datetime import UTC, datetime

from catalyst_router.adapters.memory import InMemoryOperationalStore
from catalyst_router.challenger import PublicChallengerStatus, ShadowPrediction
from catalyst_router.domain import DecisionRecord
from catalyst_router.training import FEATURE_NAMES, FEATURE_SCHEMA, FeatureVector, MarketBar
from catalyst_router.worker import TradingWorker


class FakeMarketData:
    def recent_bars(
        self, symbols: tuple[str, ...], *, timeframe_minutes: int, lookback_days: int
    ) -> list[MarketBar]:
        assert symbols == ("SPY", "AAPL")
        assert timeframe_minutes == 15
        assert lookback_days == 10
        return []


class FakeFeatureBuilder:
    def build(self, bars: list[MarketBar]) -> tuple[FeatureVector, ...]:
        del bars
        observed_at = datetime(2026, 8, 28, 14, 0, tzinfo=UTC)
        return tuple(
            FeatureVector(
                symbol=symbol,
                observed_at=observed_at,
                schema=FEATURE_SCHEMA,
                names=FEATURE_NAMES,
                values=(0.0,) * len(FEATURE_NAMES),
            )
            for symbol in ("SPY", "AAPL")
        )


class FakeChallenger:
    status = PublicChallengerStatus(
        deployed=True,
        loaded=True,
        authority="SHADOW_ONLY",
        run_id="run-1",
        feature_schema=FEATURE_SCHEMA,
        decision_gate=0.6,
        model_sha256="a" * 64,
    )
    symbols: tuple[str, ...] = ("SPY", "AAPL")

    def predict(self, vector: FeatureVector) -> ShadowPrediction:
        return ShadowPrediction(
            symbol=vector.symbol,
            observed_at=vector.observed_at,
            run_id="run-1",
            value=0.7 if vector.symbol == "AAPL" else 0.5,
            signal="LONG" if vector.symbol == "AAPL" else "ABSTAIN",
        )


def test_worker_cycle_persists_each_prediction_once() -> None:
    store = InMemoryOperationalStore()
    started = store.begin_execution()
    store.commit_reconciliation(
        started.execution_epoch,
        DecisionRecord.create(decision_type="RECONCILIATION_COMPLETED", summary="ok"),
    )
    worker = TradingWorker(
        store=store,
        market_data=FakeMarketData(),
        challenger=FakeChallenger(),
        feature_builder=FakeFeatureBuilder(),
    )

    first = worker.run_cycle()
    second = worker.run_cycle()

    assert len(first) == 2
    assert second == ()
    decisions = store.list_public_decisions()
    assert {record.symbol for record in decisions} == {"SPY", "AAPL"}
    assert {record.decision_type for record in decisions} == {"CHALLENGER_PREDICTION"}


def test_worker_cycle_requires_current_reconciled_execution_epoch() -> None:
    store = InMemoryOperationalStore()
    worker = TradingWorker(
        store=store,
        market_data=FakeMarketData(),
        challenger=FakeChallenger(),
        feature_builder=FakeFeatureBuilder(),
    )

    try:
        worker.run_cycle()
    except RuntimeError as exc:
        assert str(exc) == "worker execution epoch is not reconciled"
    else:
        raise AssertionError("unreconciled worker cycle should fail")
