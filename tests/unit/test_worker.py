from datetime import UTC, datetime

from catalyst_router.adapters.memory import InMemoryOperationalStore
from catalyst_router.challenger import PublicChallengerStatus, ShadowPrediction
from catalyst_router.domain import DecisionRecord, Route
from catalyst_router.training import FEATURE_NAMES, FEATURE_SCHEMA, FeatureVector, MarketBar
from catalyst_router.worker import TradingWorker


class FakeMarketData:
    def __init__(self) -> None:
        self.lookbacks: list[int] = []

    def recent_bars(
        self, symbols: tuple[str, ...], *, timeframe_minutes: int, lookback_days: int
    ) -> list[MarketBar]:
        assert symbols == ("SPY", "AAPL")
        assert timeframe_minutes == 5
        self.lookbacks.append(lookback_days)
        return []


class FakeFeatureBuilder:
    def __init__(self, observed_at: datetime = datetime(2026, 8, 28, 14, 0, tzinfo=UTC)) -> None:
        self.observed_at = observed_at

    def build(self, bars: list[MarketBar]) -> tuple[FeatureVector, ...]:
        del bars
        return tuple(
            FeatureVector(
                symbol=symbol,
                observed_at=self.observed_at,
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
        authority="PAPER_LIVE",
        run_id="run-1",
        feature_schema=FEATURE_SCHEMA,
        decision_gate=0.6,
        horizon_bars=48,
        timeframe_minutes=5,
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


class FakeLiveTrader:
    def __init__(self) -> None:
        self.calls: list[tuple[FeatureVector, ...]] = []

    def run(
        self, vectors: tuple[FeatureVector, ...], *, expected_epoch: str
    ) -> tuple[DecisionRecord, ...]:
        del expected_epoch
        self.calls.append(vectors)
        return ()


class FakeEventRouter:
    def run(
        self, vectors: tuple[FeatureVector, ...], *, expected_epoch: str
    ) -> tuple[DecisionRecord, ...]:
        del expected_epoch
        assert all(isinstance(vector, FeatureVector) for vector in vectors)
        return (
            DecisionRecord.create(
                decision_type="LLM_EVENT_ROUTE",
                route=Route.CATALYST_CONTINUATION,
                symbol="AAPL",
                summary="shadow event route",
            ),
        )


class FailingEventRouter:
    def run(
        self, vectors: tuple[FeatureVector, ...], *, expected_epoch: str
    ) -> tuple[DecisionRecord, ...]:
        del vectors, expected_epoch
        raise KeyError("shadow feature mismatch")


def test_worker_cycle_persists_each_prediction_once() -> None:
    store = InMemoryOperationalStore()
    started = store.begin_execution()
    store.commit_reconciliation(
        started.execution_epoch,
        DecisionRecord.create(decision_type="RECONCILIATION_COMPLETED", summary="ok"),
    )
    market_data = FakeMarketData()
    worker = TradingWorker(
        store=store,
        market_data=market_data,
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
    assert all(record.route is None for record in decisions)
    assert {record.payload["authority"] for record in first} == {"PAPER_LIVE"}
    assert {record.public_summary for record in first} == {
        "Paper-Live Model signal: ABSTAIN",
        "Paper-Live Model signal: LONG",
    }
    assert market_data.lookbacks == [10, 1]


def test_worker_scores_each_symbols_latest_complete_bar() -> None:
    store = InMemoryOperationalStore()
    started = store.begin_execution()
    store.commit_reconciliation(
        started.execution_epoch,
        DecisionRecord.create(decision_type="RECONCILIATION_COMPLETED", summary="ok"),
    )

    class UnevenFeatureBuilder:
        def build(self, bars: list[MarketBar]) -> tuple[FeatureVector, ...]:
            del bars
            return tuple(
                FeatureVector(
                    symbol=symbol,
                    observed_at=observed_at,
                    schema=FEATURE_SCHEMA,
                    names=FEATURE_NAMES,
                    values=(0.0,) * len(FEATURE_NAMES),
                )
                for symbol, observed_at in (
                    ("SPY", datetime(2026, 8, 28, 14, 5, tzinfo=UTC)),
                    ("AAPL", datetime(2026, 8, 28, 14, 0, tzinfo=UTC)),
                )
            )

    worker = TradingWorker(
        store=store,
        market_data=FakeMarketData(),
        challenger=FakeChallenger(),
        feature_builder=UnevenFeatureBuilder(),
    )

    records = worker.run_cycle()

    assert {record.symbol for record in records} == {"SPY", "AAPL"}


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


def test_live_trader_waits_for_a_bar_completed_after_worker_start() -> None:
    store = InMemoryOperationalStore()
    started = store.begin_execution()
    store.commit_reconciliation(
        started.execution_epoch,
        DecisionRecord.create(decision_type="RECONCILIATION_COMPLETED", summary="ok"),
    )
    live_trader = FakeLiveTrader()
    worker = TradingWorker(
        store=store,
        market_data=FakeMarketData(),
        challenger=FakeChallenger(),
        feature_builder=FakeFeatureBuilder(),
        live_trader=live_trader,
        live_started_at=datetime(2026, 8, 28, 14, 0, tzinfo=UTC),
    )

    worker.run_cycle()

    assert live_trader.calls == []


def test_live_trader_accepts_the_next_completed_bar() -> None:
    store = InMemoryOperationalStore()
    started = store.begin_execution()
    store.commit_reconciliation(
        started.execution_epoch,
        DecisionRecord.create(decision_type="RECONCILIATION_COMPLETED", summary="ok"),
    )
    live_trader = FakeLiveTrader()
    worker = TradingWorker(
        store=store,
        market_data=FakeMarketData(),
        challenger=FakeChallenger(),
        feature_builder=FakeFeatureBuilder(datetime(2026, 8, 28, 14, 15, tzinfo=UTC)),
        live_trader=live_trader,
        live_started_at=datetime(2026, 8, 28, 14, 0, tzinfo=UTC),
    )

    worker.run_cycle()
    worker.run_cycle()

    assert len(live_trader.calls) == 1
    assert {vector.observed_at for vector in live_trader.calls[0]} == {
        datetime(2026, 8, 28, 14, 15, tzinfo=UTC)
    }


def test_shadow_event_record_is_not_passed_to_live_trader() -> None:
    store = InMemoryOperationalStore()
    started = store.begin_execution()
    store.commit_reconciliation(
        started.execution_epoch,
        DecisionRecord.create(decision_type="RECONCILIATION_COMPLETED", summary="ok"),
    )
    live_trader = FakeLiveTrader()
    worker = TradingWorker(
        store=store,
        market_data=FakeMarketData(),
        challenger=FakeChallenger(),
        feature_builder=FakeFeatureBuilder(datetime(2026, 8, 28, 14, 15, tzinfo=UTC)),
        live_trader=live_trader,
        event_router=FakeEventRouter(),
        live_started_at=datetime(2026, 8, 28, 14, 0, tzinfo=UTC),
    )

    records = worker.run_cycle()

    assert sum(record.decision_type == "LLM_EVENT_ROUTE" for record in records) == 1
    assert len(live_trader.calls) == 1
    assert all(isinstance(vector, FeatureVector) for vector in live_trader.calls[0])


def test_shadow_event_failure_does_not_block_live_trader() -> None:
    store = InMemoryOperationalStore()
    started = store.begin_execution()
    store.commit_reconciliation(
        started.execution_epoch,
        DecisionRecord.create(decision_type="RECONCILIATION_COMPLETED", summary="ok"),
    )
    live_trader = FakeLiveTrader()
    worker = TradingWorker(
        store=store,
        market_data=FakeMarketData(),
        challenger=FakeChallenger(),
        feature_builder=FakeFeatureBuilder(datetime(2026, 8, 28, 14, 15, tzinfo=UTC)),
        live_trader=live_trader,
        event_router=FailingEventRouter(),
        live_started_at=datetime(2026, 8, 28, 14, 0, tzinfo=UTC),
    )

    worker.run_cycle()

    assert len(live_trader.calls) == 1
