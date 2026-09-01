from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
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


class EventRouter(Protocol):
    def run(
        self, vectors: tuple[FeatureVector, ...], *, expected_epoch: str
    ) -> tuple[DecisionRecord, ...]: ...


class LiveFeatureBuilder:
    def __init__(self, feature_schema: str) -> None:
        self._feature_schema = feature_schema

    def build(self, bars: list[MarketBar]) -> tuple[FeatureVector, ...]:
        return build_feature_vectors(bars, feature_schema=self._feature_schema)


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
        event_router: EventRouter | None = None,
        live_started_at: datetime | None = None,
    ) -> None:
        self._store = store
        self._market_data = market_data
        self._challenger = challenger
        self._feature_builder = feature_builder or LiveFeatureBuilder(
            challenger.status.feature_schema or "bar-features-v3"
        )
        self._live_trader = live_trader
        self._event_router = event_router
        self._live_started_at = (
            live_started_at or datetime.now(UTC) if live_trader is not None else None
        )
        self._execution_epoch: str | None = None
        self._bars: dict[tuple[str, datetime], MarketBar] = {}
        self._market_data_bootstrapped = False

    def run_cycle(self) -> tuple[DecisionRecord, ...]:
        state = self._store.get_agent_state()
        if not state.is_reconciled:
            raise RuntimeError("worker execution epoch is not reconciled")
        if self._execution_epoch is not None and state.execution_epoch != self._execution_epoch:
            raise RuntimeError("worker lost execution epoch ownership")
        if not self._challenger.status.deployed or not self._challenger.status.loaded:
            raise RuntimeError("challenger is not ready for inference")
        timeframe = self._challenger.status.timeframe_minutes or 5
        bars = self._market_data.recent_bars(
            self._challenger.symbols,
            timeframe_minutes=timeframe,
            lookback_days=10 if not self._market_data_bootstrapped else 1,
        )
        self._market_data_bootstrapped = True
        self._bars.update({(bar.symbol, bar.timestamp): bar for bar in bars})
        vectors = self._feature_builder.build(list(self._bars.values()))
        if not vectors:
            return ()
        latest_by_symbol: dict[str, datetime] = {}
        for vector in vectors:
            latest_by_symbol[vector.symbol] = max(
                vector.observed_at,
                latest_by_symbol.get(vector.symbol, vector.observed_at),
            )
        latest_vectors = tuple(
            vector for vector in vectors if vector.observed_at == latest_by_symbol[vector.symbol]
        )
        expected_epoch = self._execution_epoch or state.execution_epoch
        records = []
        for vector in latest_vectors:
            prediction = self._challenger.predict(vector)
            record = self._prediction_record(prediction)
            if self._store.append_decision_once(record, expected_epoch=expected_epoch):
                records.append(record)
        if self._event_router is not None:
            try:
                records.extend(
                    self._event_router.run(latest_vectors, expected_epoch=expected_epoch)
                )
            except Exception:
                logger.exception("shadow event routing failed; live trading remains enabled")
        if self._live_trader is not None:
            eligible_vectors = tuple(
                vector
                for vector in latest_vectors
                if self._live_started_at is None or vector.observed_at > self._live_started_at
            )
            live_vectors = tuple(
                vector
                for vector in eligible_vectors
                if self._store.append_decision_once(
                    self._live_claim_record(vector), expected_epoch=expected_epoch
                )
            )
            if not live_vectors:
                return tuple(records)
            records.extend(self._live_trader.run(live_vectors, expected_epoch=expected_epoch))
        return tuple(records)

    def run_forever(
        self,
        *,
        reconcile: Callable[[], object],
        poll_seconds: int = 15,
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
                    logger.info("persisted %d worker decisions", len(records))
                heartbeat_path.touch()
            except Exception:
                consecutive_failures += 1
                logger.exception("trading worker cycle failed")
                if consecutive_failures >= 5:
                    raise
            sleep(poll_seconds)

    def _prediction_record(self, prediction: ShadowPrediction) -> DecisionRecord:
        authority = self._challenger.status.authority or "SHADOW_ONLY"
        model_label = "paper-live model" if authority == "PAPER_LIVE" else "shadow model"
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
                f"{model_label} {prediction.run_id} predicted {prediction.value:.6f} "
                f"({prediction.signal})"
            ),
            payload={
                "authority": authority,
                "run_id": prediction.run_id,
                "value": prediction.value,
                "signal": prediction.signal,
            },
            public=True,
            public_summary=f"{model_label.title()} signal: {prediction.signal}",
        )

    def _live_claim_record(self, vector: FeatureVector) -> DecisionRecord:
        run_id = self._challenger.status.run_id or "unknown-model"
        return DecisionRecord.create(
            decision_id=str(
                uuid5(
                    NAMESPACE_URL,
                    ":".join(
                        (
                            "live-bar-evaluation",
                            run_id,
                            vector.symbol,
                            vector.observed_at.isoformat(),
                        )
                    ),
                )
            ),
            decision_type="LIVE_BAR_EVALUATION_CLAIM",
            occurred_at=vector.observed_at,
            symbol=vector.symbol,
            summary=f"claimed one live evaluation for {run_id}",
        )
