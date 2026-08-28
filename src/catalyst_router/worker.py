from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from catalyst_router.challenger import PublicChallengerStatus, ShadowPrediction
from catalyst_router.domain import DecisionRecord
from catalyst_router.ports import OperationalStore
from catalyst_router.training import FeatureVector, MarketBar, build_feature_vectors

logger = logging.getLogger(__name__)


class MarketData(Protocol):
    def recent_bars(
        self,
        symbols: tuple[str, ...],
        *,
        timeframe_minutes: int,
        lookback_days: int,
    ) -> list[MarketBar]: ...


class ChallengerPredictor(Protocol):
    status: PublicChallengerStatus
    symbols: tuple[str, ...]

    def predict(self, vector: FeatureVector) -> ShadowPrediction: ...


class FeatureBuilder(Protocol):
    def build(self, bars: list[MarketBar]) -> tuple[FeatureVector, ...]: ...


class LiveTrader(Protocol):
    def run(
        self, vectors: tuple[FeatureVector, ...], *, expected_epoch: str
    ) -> tuple[DecisionRecord, ...]: ...


class LiveFeatureBuilder:
    def build(self, bars: list[MarketBar]) -> tuple[FeatureVector, ...]:
        return build_feature_vectors(bars)


class TradingWorker:
    """Runs the always-on inference cycle; execution remains separately gated."""

    def __init__(
        self,
        *,
        store: OperationalStore,
        market_data: MarketData,
        challenger: ChallengerPredictor,
        feature_builder: FeatureBuilder | None = None,
        live_trader: LiveTrader | None = None,
    ) -> None:
        self._store = store
        self._market_data = market_data
        self._challenger = challenger
        self._feature_builder = feature_builder or LiveFeatureBuilder()
        self._live_trader = live_trader
        self._execution_epoch: str | None = None

    def run_cycle(self) -> tuple[DecisionRecord, ...]:
        state = self._store.get_agent_state()
        if not state.is_reconciled:
            raise RuntimeError("worker execution epoch is not reconciled")
        if self._execution_epoch is not None and state.execution_epoch != self._execution_epoch:
            raise RuntimeError("worker lost execution epoch ownership")
        if not self._challenger.status.deployed or not self._challenger.status.loaded:
            raise RuntimeError("challenger is not ready for inference")
        timeframe = self._challenger.status.timeframe_minutes or 15
        bars = self._market_data.recent_bars(
            self._challenger.symbols,
            timeframe_minutes=timeframe,
            lookback_days=10,
        )
        vectors = self._feature_builder.build(bars)
        if not vectors:
            return ()
        latest = max(vector.observed_at for vector in vectors)
        expected_epoch = self._execution_epoch or state.execution_epoch
        records = []
        for vector in vectors:
            if vector.observed_at != latest:
                continue
            prediction = self._challenger.predict(vector)
            record = self._prediction_record(prediction)
            if self._store.append_decision_once(record, expected_epoch=expected_epoch):
                records.append(record)
        if self._live_trader is not None:
            latest_vectors = tuple(vector for vector in vectors if vector.observed_at == latest)
            records.extend(self._live_trader.run(latest_vectors, expected_epoch=expected_epoch))
        return tuple(records)

    def run_forever(
        self,
        *,
        reconcile: Callable[[], object],
        poll_seconds: int = 60,
        sleep: Callable[[float], None] = time.sleep,
        heartbeat_path: Path = Path("/tmp/worker-heartbeat"),
    ) -> None:
        reconcile()
        state = self._store.get_agent_state()
        if not state.is_reconciled:
            raise RuntimeError("startup reconciliation did not complete")
        self._execution_epoch = state.execution_epoch
        consecutive_failures = 0
        while True:
            try:
                records = self.run_cycle()
                consecutive_failures = 0
                if records:
                    logger.info("persisted %d challenger predictions", len(records))
                heartbeat_path.touch()
            except Exception:
                consecutive_failures += 1
                logger.exception("trading worker cycle failed")
                if consecutive_failures >= 5:
                    raise
            sleep(poll_seconds)

    def _prediction_record(self, prediction: ShadowPrediction) -> DecisionRecord:
        decision_id = str(
            uuid5(
                NAMESPACE_URL,
                ":".join(
                    (
                        "challenger-prediction",
                        prediction.run_id,
                        prediction.symbol,
                        prediction.observed_at.isoformat(),
                    )
                ),
            )
        )
        return DecisionRecord.create(
            decision_id=decision_id,
            decision_type="CHALLENGER_PREDICTION",
            occurred_at=prediction.observed_at,
            symbol=prediction.symbol,
            summary=(
                f"shadow model {prediction.run_id} predicted {prediction.value:.6f} "
                f"({prediction.signal})"
            ),
            payload={
                "authority": "SHADOW_ONLY",
                "run_id": prediction.run_id,
                "value": prediction.value,
                "signal": prediction.signal,
            },
            public=True,
            public_summary=f"Shadow model signal: {prediction.signal}",
        )
