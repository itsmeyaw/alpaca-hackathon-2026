from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from catalyst_router.adapters.bedrock import BedrockEventExtractor, EventExtractionError
from catalyst_router.adapters.memory import InMemoryOperationalStore
from catalyst_router.domain import (
    DecisionRecord,
    Event,
    EventDirection,
    EventType,
    NewsArticle,
    QuoteSnapshot,
    Route,
)
from catalyst_router.events import ShadowEventRouter
from catalyst_router.training import (
    FEATURE_NAMES,
    FEATURE_NAMES_V2,
    FEATURE_SCHEMA,
    FEATURE_SCHEMA_V2,
    FeatureVector,
)


def article() -> NewsArticle:
    return NewsArticle(
        source_id="alpaca:42",
        headline="AAPL raises full-year guidance",
        summary="Demand is stronger than expected.",
        content="Apple raised full-year revenue guidance after stronger demand.",
        source="benzinga",
        author="Reporter",
        url="https://example.test/news/42",
        published_at=datetime(2026, 8, 28, 14, 1, tzinfo=UTC),
        updated_at=datetime(2026, 8, 28, 14, 1, tzinfo=UTC),
        symbols=("AAPL",),
    )


def tool_response(input_value: dict[str, Any]) -> dict[str, Any]:
    return {
        "output": {
            "message": {
                "content": [
                    {
                        "toolUse": {
                            "toolUseId": "tool-1",
                            "name": "record_event",
                            "input": input_value,
                        }
                    }
                ]
            }
        },
        "usage": {"inputTokens": 120, "outputTokens": 35},
        "ResponseMetadata": {"RequestId": "request-1"},
    }


class FakeBedrock:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def converse(self, **request: Any) -> dict[str, Any]:
        self.calls.append(request)
        return self.response


class FailingBedrock:
    def converse(self, **request: Any) -> dict[str, Any]:
        del request
        raise TimeoutError("Bedrock timed out")


def valid_analysis() -> dict[str, Any]:
    return {
        "event_type": "GUIDANCE",
        "direction": "BULLISH",
        "magnitude": 0.8,
        "novelty": 0.85,
        "surprise": 0.7,
        "confidence": 0.9,
        "expected_horizon_minutes": 240,
        "affected_symbols": ["AAPL"],
        "summary": "Raised guidance indicates stronger expected revenue.",
        "invalidating_evidence": ["Guidance is withdrawn"],
    }


def test_bedrock_extractor_returns_typed_event_with_provenance() -> None:
    client = FakeBedrock(tool_response(valid_analysis()))
    extractor = BedrockEventExtractor(
        model_id="anthropic.test-model-v1",
        prompt_version="event-v1",
        client=client,
        now=lambda: datetime(2026, 8, 28, 14, 2, tzinfo=UTC),
    )

    event = extractor.extract(article())

    assert event.direction is EventDirection.BULLISH
    assert event.event_type is EventType.GUIDANCE
    assert event.source_id == "alpaca:42"
    assert event.affected_symbols == ("AAPL",)
    assert event.input_tokens == 120
    assert event.output_tokens == 35
    assert event.request_id == "request-1"
    assert client.calls[0]["toolConfig"]["toolChoice"] == {"tool": {"name": "record_event"}}


def test_bedrock_extractor_rejects_malformed_or_unbounded_output() -> None:
    analysis = valid_analysis()
    analysis["affected_symbols"] = ["UNRELATED"]
    extractor = BedrockEventExtractor(
        model_id="anthropic.test-model-v1",
        prompt_version="event-v1",
        client=FakeBedrock(tool_response(analysis)),
    )

    with pytest.raises(EventExtractionError, match="affected symbols"):
        extractor.extract(article())


def test_bedrock_transport_failure_is_classified_as_extraction_failure() -> None:
    extractor = BedrockEventExtractor(
        model_id="anthropic.test-model-v1",
        prompt_version="event-v1",
        client=FailingBedrock(),
    )

    with pytest.raises(EventExtractionError, match="request failed") as captured:
        extractor.extract(article())
    assert captured.value.retryable


class FakeNews:
    def recent_news(
        self,
        symbols: tuple[str, ...],
        *,
        since: datetime,
        until: datetime,
    ) -> tuple[NewsArticle, ...]:
        assert "AAPL" in symbols
        assert until - since == timedelta(minutes=15)
        return (article(),)


class FakeExtractor:
    model_id = "anthropic.test-model-v1"
    prompt_version = "event-v1"

    def extract(self, article: NewsArticle) -> Event:
        return Event(
            source_id=article.source_id,
            published_at=article.published_at,
            analyzed_at=datetime(2026, 8, 28, 14, 6, tzinfo=UTC),
            event_type=EventType.GUIDANCE,
            direction=EventDirection.BULLISH,
            magnitude=Decimal("0.8"),
            novelty=Decimal("0.85"),
            surprise=Decimal("0.7"),
            confidence=Decimal("0.9"),
            expected_horizon_minutes=240,
            affected_symbols=("AAPL",),
            summary="Raised guidance indicates stronger expected revenue.",
            invalidating_evidence=("Guidance is withdrawn",),
            model_id=self.model_id,
            prompt_version=self.prompt_version,
            request_id="request-1",
            input_tokens=120,
            output_tokens=35,
        )


class MismatchedExtractor(FakeExtractor):
    def extract(self, article: NewsArticle) -> Event:
        return super().extract(article).model_copy(update={"source_id": "alpaca:other"})


class RetryExtractor(FakeExtractor):
    def __init__(self) -> None:
        self.calls = 0

    def extract(self, article: NewsArticle) -> Event:
        self.calls += 1
        if self.calls == 1:
            raise EventExtractionError("transient timeout", retryable=True)
        return super().extract(article)


class FakeQuotes:
    def latest_quote(self, symbol: str) -> QuoteSnapshot:
        return QuoteSnapshot(
            symbol=symbol,
            bid_price=Decimal("99.98"),
            ask_price=Decimal("100.00"),
            timestamp=datetime(2026, 8, 28, 14, 5, 59, tzinfo=UTC),
            feed="iex",
        )


class RetryQuotes(FakeQuotes):
    def __init__(self) -> None:
        self.calls = 0

    def latest_quote(self, symbol: str) -> QuoteSnapshot:
        self.calls += 1
        if self.calls == 1:
            raise TimeoutError("quote timed out")
        return super().latest_quote(symbol)


def vector() -> FeatureVector:
    values = dict.fromkeys(FEATURE_NAMES, 0.0)
    values["relative_return_1h"] = 0.008
    values["market_return_1h"] = 0.002
    return FeatureVector(
        symbol="AAPL",
        observed_at=datetime(2026, 8, 28, 14, 6, tzinfo=UTC),
        schema=FEATURE_SCHEMA,
        names=FEATURE_NAMES,
        values=tuple(values[name] for name in FEATURE_NAMES),
    )


def vector_v2() -> FeatureVector:
    values = dict.fromkeys(FEATURE_NAMES_V2, 0.0)
    values["relative_return_4"] = 0.008
    values["market_return_4"] = 0.002
    return FeatureVector(
        symbol="AAPL",
        observed_at=datetime(2026, 8, 28, 14, 6, tzinfo=UTC),
        schema=FEATURE_SCHEMA_V2,
        names=FEATURE_NAMES_V2,
        values=tuple(values[name] for name in FEATURE_NAMES_V2),
    )


def test_shadow_event_router_uses_llm_event_as_deterministic_route_evidence_once() -> None:
    store = InMemoryOperationalStore()
    started = store.begin_execution()
    store.commit_reconciliation(
        started.execution_epoch,
        DecisionRecord.create(decision_type="RECONCILIATION_COMPLETED", summary="ok"),
    )
    router = ShadowEventRouter(
        store=store,
        news=FakeNews(),
        extractor=FakeExtractor(),
        quotes=FakeQuotes(),
        now=lambda: datetime(2026, 8, 28, 14, 6, tzinfo=UTC),
    )

    first = router.run((vector(),), expected_epoch=started.execution_epoch)
    restarted_router = ShadowEventRouter(
        store=store,
        news=FakeNews(),
        extractor=FakeExtractor(),
        quotes=FakeQuotes(),
        now=lambda: datetime(2026, 8, 28, 14, 6, tzinfo=UTC),
    )
    second = restarted_router.run((vector(),), expected_epoch=started.execution_epoch)

    assert len(first) == 1
    assert first[0].decision_type == "LLM_EVENT_ROUTE"
    assert first[0].route is Route.CATALYST_CONTINUATION
    assert first[0].payload["authority"] == "SHADOW_ONLY"
    assert second == ()


def test_shadow_event_router_supports_deployed_fifteen_minute_features() -> None:
    store = InMemoryOperationalStore()
    started = store.begin_execution()
    store.commit_reconciliation(
        started.execution_epoch,
        DecisionRecord.create(decision_type="RECONCILIATION_COMPLETED", summary="ok"),
    )
    router = ShadowEventRouter(
        store=store,
        news=FakeNews(),
        extractor=FakeExtractor(),
        quotes=FakeQuotes(),
        now=lambda: datetime(2026, 8, 28, 14, 6, tzinfo=UTC),
    )

    records = router.run((vector_v2(),), expected_epoch=started.execution_epoch)

    assert len(records) == 1
    assert records[0].route is Route.CATALYST_CONTINUATION


def test_shadow_event_router_fails_closed_on_stale_market_evidence() -> None:
    store = InMemoryOperationalStore()
    started = store.begin_execution()
    store.commit_reconciliation(
        started.execution_epoch,
        DecisionRecord.create(decision_type="RECONCILIATION_COMPLETED", summary="ok"),
    )
    router = ShadowEventRouter(
        store=store,
        news=FakeNews(),
        extractor=FakeExtractor(),
        quotes=FakeQuotes(),
        now=lambda: datetime(2026, 8, 28, 14, 14, tzinfo=UTC),
    )

    records = router.run((vector(),), expected_epoch=started.execution_epoch)

    assert len(records) == 1
    assert records[0].route is Route.NO_TRADE


def test_shadow_event_router_revalidates_extractor_provenance() -> None:
    store = InMemoryOperationalStore()
    started = store.begin_execution()
    store.commit_reconciliation(
        started.execution_epoch,
        DecisionRecord.create(decision_type="RECONCILIATION_COMPLETED", summary="ok"),
    )
    router = ShadowEventRouter(
        store=store,
        news=FakeNews(),
        extractor=MismatchedExtractor(),
        quotes=FakeQuotes(),
        now=lambda: datetime(2026, 8, 28, 14, 6, tzinfo=UTC),
    )

    records = router.run((vector(),), expected_epoch=started.execution_epoch)

    assert len(records) == 1
    assert records[0].route is Route.NO_TRADE
    assert "provenance" in records[0].summary


def test_event_analysis_cannot_precede_publication() -> None:
    with pytest.raises(ValidationError, match="must not precede"):
        Event(
            source_id="alpaca:42",
            published_at=datetime(2026, 8, 28, 14, 1, tzinfo=UTC),
            analyzed_at=datetime(2026, 8, 28, 14, 0, tzinfo=UTC),
            event_type=EventType.GUIDANCE,
            direction=EventDirection.BULLISH,
            magnitude=Decimal("0.8"),
            novelty=Decimal("0.8"),
            surprise=Decimal("0.7"),
            confidence=Decimal("0.9"),
            expected_horizon_minutes=240,
            affected_symbols=("AAPL",),
            summary="Raised guidance.",
            invalidating_evidence=(),
            model_id="model-1",
            prompt_version="event-v1",
        )


def test_retryable_extraction_releases_claim_for_next_cycle() -> None:
    store = InMemoryOperationalStore()
    started = store.begin_execution()
    store.commit_reconciliation(
        started.execution_epoch,
        DecisionRecord.create(decision_type="RECONCILIATION_COMPLETED", summary="ok"),
    )
    extractor = RetryExtractor()
    router = ShadowEventRouter(
        store=store,
        news=FakeNews(),
        extractor=extractor,
        quotes=FakeQuotes(),
        now=lambda: datetime(2026, 8, 28, 14, 6, tzinfo=UTC),
    )

    first = router.run((vector(),), expected_epoch=started.execution_epoch)
    second = router.run((vector(),), expected_epoch=started.execution_epoch)

    assert first[0].route is Route.NO_TRADE
    assert second[0].route is Route.CATALYST_CONTINUATION
    assert extractor.calls == 2


def test_quote_failure_releases_claim_for_next_cycle() -> None:
    store = InMemoryOperationalStore()
    started = store.begin_execution()
    store.commit_reconciliation(
        started.execution_epoch,
        DecisionRecord.create(decision_type="RECONCILIATION_COMPLETED", summary="ok"),
    )
    quotes = RetryQuotes()
    router = ShadowEventRouter(
        store=store,
        news=FakeNews(),
        extractor=FakeExtractor(),
        quotes=quotes,
        now=lambda: datetime(2026, 8, 28, 14, 6, tzinfo=UTC),
    )

    first = router.run((vector(),), expected_epoch=started.execution_epoch)
    second = router.run((vector(),), expected_epoch=started.execution_epoch)

    assert first[0].route is Route.NO_TRADE
    assert second[0].route is Route.CATALYST_CONTINUATION
    assert quotes.calls == 2


def test_future_event_analysis_fails_closed() -> None:
    store = InMemoryOperationalStore()
    started = store.begin_execution()
    store.commit_reconciliation(
        started.execution_epoch,
        DecisionRecord.create(decision_type="RECONCILIATION_COMPLETED", summary="ok"),
    )

    class FutureExtractor(FakeExtractor):
        def extract(self, article: NewsArticle) -> Event:
            return (
                super()
                .extract(article)
                .model_copy(update={"analyzed_at": datetime(2026, 8, 28, 14, 7, tzinfo=UTC)})
            )

    router = ShadowEventRouter(
        store=store,
        news=FakeNews(),
        extractor=FutureExtractor(),
        quotes=FakeQuotes(),
        now=lambda: datetime(2026, 8, 28, 14, 6, tzinfo=UTC),
    )

    records = router.run((vector(),), expected_epoch=started.execution_epoch)

    assert records[0].route is Route.NO_TRADE
