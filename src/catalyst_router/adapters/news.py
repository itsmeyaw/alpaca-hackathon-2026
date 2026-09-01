from __future__ import annotations

from datetime import datetime
from typing import Protocol, cast

from alpaca.data.historical.news import NewsClient
from alpaca.data.models.news import News, NewsSet
from alpaca.data.requests import NewsRequest

from catalyst_router.domain import NewsArticle


class _NewsClient(Protocol):
    def get_news(self, request: NewsRequest) -> NewsSet: ...


class AlpacaNewsSource:
    def __init__(
        self,
        key: str,
        secret: str,
        *,
        client: _NewsClient | None = None,
    ) -> None:
        self._client = client or cast(_NewsClient, NewsClient(key, secret))

    def recent_news(
        self,
        symbols: tuple[str, ...],
        *,
        since: datetime,
        until: datetime,
    ) -> tuple[NewsArticle, ...]:
        response = self._client.get_news(
            NewsRequest(
                symbols=",".join(symbols),
                start=since,
                end=until,
                sort="asc",
                limit=50,
                include_content=True,
                exclude_contentless=True,
            )
        )
        requested = set(symbols)
        articles: dict[int, News] = {}
        for items in response.data.values():
            for item in items:
                articles[item.id] = item
        return tuple(
            NewsArticle(
                source_id=f"alpaca:{item.id}",
                headline=item.headline,
                summary=item.summary,
                content=item.content,
                source=item.source,
                author=item.author,
                url=item.url,
                published_at=item.created_at,
                updated_at=item.updated_at,
                symbols=tuple(
                    sorted(symbol for symbol in set(item.symbols) if symbol in requested)
                ),
            )
            for item in sorted(articles.values(), key=lambda value: (value.created_at, value.id))
            if requested.intersection(item.symbols) and since <= item.created_at <= until
        )
