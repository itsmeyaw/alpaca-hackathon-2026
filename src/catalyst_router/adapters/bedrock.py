from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol

import boto3
from botocore.config import Config
from pydantic import BaseModel, ConfigDict, Field

from catalyst_router.domain import Event, EventDirection, EventType, NewsArticle


class EventExtractionError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class _BedrockRuntime(Protocol):
    def converse(self, **request: Any) -> dict[str, Any]: ...


class _EventAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: EventType
    direction: EventDirection
    magnitude: float = Field(ge=0, le=1)
    novelty: float = Field(ge=0, le=1)
    surprise: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    expected_horizon_minutes: int = Field(gt=0, le=10_080)
    affected_symbols: tuple[str, ...] = Field(min_length=1)
    summary: str = Field(min_length=1, max_length=500)
    invalidating_evidence: tuple[str, ...]


class BedrockEventExtractor:
    """Converts one news article into bounded Event evidence using Bedrock tool use."""

    TOOL_NAME = "record_event"

    def __init__(
        self,
        *,
        model_id: str,
        prompt_version: str,
        region: str = "us-east-1",
        client: _BedrockRuntime | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.model_id = model_id
        self.prompt_version = prompt_version
        self._client = client or boto3.client(
            "bedrock-runtime",
            region_name=region,
            config=Config(
                connect_timeout=3,
                read_timeout=15,
                retries={"mode": "standard", "total_max_attempts": 3},
            ),
        )
        self._now = now or (lambda: datetime.now(UTC))

    def extract(self, article: NewsArticle) -> Event:
        request = {
            "modelId": self.model_id,
            "system": [
                {
                    "text": (
                        "You extract factual financial events from supplied news only. "
                        "Do not predict prices, invent symbols, or provide trading instructions. "
                        f"Prompt version: {self.prompt_version}."
                    )
                }
            ],
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "text": json.dumps(
                                article.model_dump(mode="json"),
                                separators=(",", ":"),
                                sort_keys=True,
                            )
                        }
                    ],
                }
            ],
            "inferenceConfig": {"maxTokens": 700, "temperature": 0},
            "toolConfig": {
                "tools": [
                    {
                        "toolSpec": {
                            "name": self.TOOL_NAME,
                            "description": "Record the structured event contained in the article.",
                            "inputSchema": {"json": _EventAnalysis.model_json_schema()},
                        }
                    }
                ],
                "toolChoice": {"tool": {"name": self.TOOL_NAME}},
            },
            "requestMetadata": {
                "component": "catalyst-router-event-extractor",
                "prompt-version": self.prompt_version,
            },
        }
        try:
            response = self._client.converse(**request)
        except Exception as exc:
            raise EventExtractionError(
                "Bedrock Event extraction request failed", retryable=True
            ) from exc
        try:
            content = response["output"]["message"]["content"]
            tool_use = next(
                item["toolUse"]
                for item in content
                if item.get("toolUse", {}).get("name") == self.TOOL_NAME
            )
            analysis = _EventAnalysis.model_validate(tool_use["input"])
            article_symbols = set(article.symbols)
            affected_symbols = tuple(dict.fromkeys(analysis.affected_symbols))
            if not set(affected_symbols).issubset(article_symbols):
                raise EventExtractionError("affected symbols must be present on the source article")
            usage = response.get("usage", {})
            metadata = response.get("ResponseMetadata", {})
            return Event(
                source_id=article.source_id,
                published_at=article.published_at,
                analyzed_at=self._now(),
                event_type=analysis.event_type,
                direction=analysis.direction,
                magnitude=Decimal(str(analysis.magnitude)),
                novelty=Decimal(str(analysis.novelty)),
                surprise=Decimal(str(analysis.surprise)),
                confidence=Decimal(str(analysis.confidence)),
                expected_horizon_minutes=analysis.expected_horizon_minutes,
                affected_symbols=affected_symbols,
                summary=analysis.summary,
                invalidating_evidence=analysis.invalidating_evidence,
                model_id=self.model_id,
                prompt_version=self.prompt_version,
                request_id=metadata.get("RequestId"),
                input_tokens=int(usage.get("inputTokens", 0)),
                output_tokens=int(usage.get("outputTokens", 0)),
            )
        except EventExtractionError:
            raise
        except Exception as exc:
            raise EventExtractionError("Bedrock did not return a valid structured Event") from exc
