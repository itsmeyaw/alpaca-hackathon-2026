from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

from alpaca.data.models.news import NewsSet

from catalyst_router.adapters.news import AlpacaNewsSource


class FakeNewsClient:
    def __init__(self) -> None:
        self.request: object | None = None

    def get_news(self, request: object) -> NewsSet:
        self.request = request
        item = SimpleNamespace(
            id=42,
            headline="AAPL raises guidance",
            source="benzinga",
            url="https://example.test/42",
            summary="Demand is stronger.",
            created_at=datetime(2026, 8, 28, 14, 1, tzinfo=UTC),
            updated_at=datetime(2026, 8, 28, 14, 2, tzinfo=UTC),
            symbols=["MSFT", "AAPL"],
            author="Reporter",
            content="Full article",
        )
        stale = SimpleNamespace(
            **{**item.__dict__, "id": 41, "created_at": datetime(2026, 8, 28, 13, 59, tzinfo=UTC)}
        )
        return cast(NewsSet, SimpleNamespace(data={"news": [stale, item, item]}))


def test_alpaca_news_source_returns_deduplicated_bounded_articles() -> None:
    client = FakeNewsClient()
    source = AlpacaNewsSource("key", "secret", client=client)

    articles = source.recent_news(
        ("AAPL",),
        since=datetime(2026, 8, 28, 14, tzinfo=UTC),
        until=datetime(2026, 8, 28, 14, 5, tzinfo=UTC),
    )

    assert len(articles) == 1
    assert articles[0].source_id == "alpaca:42"
    assert articles[0].symbols == ("AAPL",)
    request = cast(Any, client.request)
    assert request.symbols == "AAPL"
    assert request.include_content is True
