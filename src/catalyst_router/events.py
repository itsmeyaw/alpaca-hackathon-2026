from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from catalyst_router.adapters.bedrock import EventExtractionError
from catalyst_router.decision import DecisionEngine
from catalyst_router.domain import (
    DecisionRecord,
    Event,
    EventDirection,
    NewsArticle,
    QuoteSnapshot,
    Route,
    SignalFrame,
)
from catalyst_router.ports import OperationalStore
from catalyst_router.training import FeatureVector


class NewsSource(Protocol):
    def recent_news(
        self,
        symbols: tuple[str, ...],
        *,
        since: datetime,
        until: datetime,
    ) -> tuple[NewsArticle, ...]: ...


class EventExtractor(Protocol):
    model_id: str
    prompt_version: str

    def extract(self, article: NewsArticle) -> Event: ...


class QuoteSource(Protocol):
    def latest_quote(self, symbol: str) -> QuoteSnapshot: ...


class ShadowEventRouter:
    """Records LLM Event evidence and deterministic route decisions without order authority."""

    def __init__(
        self,
        *,
        store: OperationalStore,
        news: NewsSource,
        extractor: EventExtractor,
        quotes: QuoteSource,
        engine: DecisionEngine | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._news = news
        self._extractor = extractor
        self._quotes = quotes
        self._engine = engine or DecisionEngine()
        self._now = now or (lambda: datetime.now(UTC))

    def run(
        self, vectors: tuple[FeatureVector, ...], *, expected_epoch: str
    ) -> tuple[DecisionRecord, ...]:
        if not vectors:
            return ()
        observed_at = self._now()
        by_symbol = {vector.symbol: vector for vector in vectors}
        articles = self._news.recent_news(
            tuple(sorted(by_symbol)),
            since=observed_at - timedelta(minutes=15),
            until=observed_at,
        )
        records: list[DecisionRecord] = []
        for article in articles:
            if not self._store.claim_event_extraction(
                article.source_id,
                self._extractor.model_id,
                self._extractor.prompt_version,
                expected_epoch=expected_epoch,
            ):
                continue
            try:
                event = self._extractor.extract(article)
                self._validate_event(article, event)
            except EventExtractionError as exc:
                record = self._failure_record(article, observed_at, str(exc))
                if self._store.append_decision_once(record, expected_epoch=expected_epoch):
                    records.append(record)
                if exc.retryable:
                    self._store.release_event_extraction(
                        article.source_id,
                        self._extractor.model_id,
                        self._extractor.prompt_version,
                        expected_epoch=expected_epoch,
                    )
                continue
            outcome_recorded = False
            retry_reason = "Event has no current feature vector"
            for symbol in event.affected_symbols:
                vector = by_symbol.get(symbol)
                if vector is None:
                    continue
                try:
                    quote = self._quotes.latest_quote(symbol)
                except Exception as exc:
                    retry_reason = f"quote unavailable: {type(exc).__name__}"
                    continue
                evaluated_at = self._now()
                decision = self._engine.evaluate(
                    self._signal_frame(event, vector, quote, now=evaluated_at)
                )
                record = self._route_record(event, decision.route, symbol, evaluated_at)
                if self._store.append_decision_once(record, expected_epoch=expected_epoch):
                    records.append(record)
                outcome_recorded = True
            if not outcome_recorded:
                record = self._failure_record(article, observed_at, retry_reason)
                if self._store.append_decision_once(record, expected_epoch=expected_epoch):
                    records.append(record)
                self._store.release_event_extraction(
                    article.source_id,
                    self._extractor.model_id,
                    self._extractor.prompt_version,
                    expected_epoch=expected_epoch,
                )
        return tuple(records)

    def _validate_event(self, article: NewsArticle, event: Event) -> None:
        if (
            event.source_id != article.source_id
            or event.model_id != self._extractor.model_id
            or event.prompt_version != self._extractor.prompt_version
            or not set(event.affected_symbols).issubset(article.symbols)
        ):
            raise EventExtractionError("Event provenance does not match its source article")

    def _signal_frame(
        self, event: Event, vector: FeatureVector, quote: QuoteSnapshot, *, now: datetime
    ) -> SignalFrame:
        features = dict(zip(vector.names, vector.values, strict=True))
        relative_return = features.get("relative_return_1h")
        if relative_return is None:
            relative_return = features["relative_return_4"]
        momentum = _clamp(Decimal(str(relative_return)) * Decimal("100"))
        direction = {
            EventDirection.BULLISH: Decimal("1"),
            EventDirection.BEARISH: Decimal("-1"),
            EventDirection.NEUTRAL: Decimal("0"),
        }[event.direction]
        quote_age = now - quote.timestamp
        vector_age = now - vector.observed_at
        event_age = now - event.published_at
        analysis_age = now - event.analyzed_at
        data_quality_passed = (
            timedelta(0) <= quote_age <= timedelta(seconds=5)
            and timedelta(0) <= vector_age <= timedelta(minutes=7)
            and timedelta(0) <= event_age <= timedelta(minutes=15)
            and timedelta(0) <= analysis_age <= timedelta(minutes=5)
        )
        return SignalFrame(
            symbol=vector.symbol,
            observed_at=vector.observed_at,
            expected_horizon_minutes=event.expected_horizon_minutes,
            has_credible_event=event.direction is not EventDirection.NEUTRAL,
            event_confidence=event.confidence,
            event_novelty=event.novelty,
            event_direction=direction,
            momentum_score=momentum,
            reversion_score=Decimal("0"),
            regime_score=Decimal("0"),
            spread_bps=quote.spread_bps,
            expected_edge_bps=event.magnitude * Decimal("100"),
            estimated_cost_bps=Decimal("8"),
            data_quality_passed=data_quality_passed,
            exposure_group=("us-equity:long" if direction >= 0 else "us-equity:short"),
        )

    def _route_record(
        self,
        event: Event,
        route: Route,
        symbol: str,
        observed_at: datetime,
    ) -> DecisionRecord:
        decision_id = str(
            uuid5(
                NAMESPACE_URL,
                ":".join(
                    (
                        "llm-event-route",
                        event.source_id,
                        event.model_id,
                        event.prompt_version,
                        symbol,
                    )
                ),
            )
        )
        return DecisionRecord.create(
            decision_id=decision_id,
            decision_type="LLM_EVENT_ROUTE",
            occurred_at=observed_at,
            route=route,
            symbol=symbol,
            summary=f"shadow LLM Event route {route} for {symbol}",
            payload={
                "authority": "SHADOW_ONLY",
                "event": event.model_dump(mode="json"),
                "route": route,
            },
            public=True,
            public_summary=f"Event evidence route: {route}",
        )

    def _failure_record(
        self, article: NewsArticle, observed_at: datetime, reason: str
    ) -> DecisionRecord:
        return DecisionRecord.create(
            decision_id=str(
                uuid5(
                    NAMESPACE_URL,
                    f"llm-event-failure:{article.source_id}:{self._extractor.prompt_version}",
                )
            ),
            decision_type="LLM_EVENT_ROUTE",
            occurred_at=observed_at,
            route=Route.NO_TRADE,
            summary=f"LLM Event extraction failed closed: {reason}",
            payload={
                "authority": "SHADOW_ONLY",
                "source_id": article.source_id,
                "model_id": self._extractor.model_id,
                "prompt_version": self._extractor.prompt_version,
                "reason": reason,
            },
            public=True,
            public_summary="Event evidence unavailable; no trade",
        )


def _clamp(value: Decimal) -> Decimal:
    return max(Decimal("-1"), min(Decimal("1"), value))
