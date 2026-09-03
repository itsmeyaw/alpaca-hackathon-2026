from __future__ import annotations

import json
import os
from dataclasses import dataclass
from decimal import Decimal


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    alpaca_key: str | None
    alpaca_secret: str | None
    state_backend: str
    competition_id: str
    aws_region: str
    dynamodb_table: str
    dynamodb_endpoint_url: str | None
    auto_reconcile: bool
    public_delay_seconds: int
    runtime_role: str = "local"
    worker_poll_seconds: int = 15
    cors_origins: tuple[str, ...] = ("http://localhost:3000", "http://127.0.0.1:3000")
    challenger_manifest_uri: str | None = None
    challenger_manifest_sha256: str | None = None
    paper_execution_enabled: bool = False
    model_execution_enabled: bool = False
    model_options_execution_enabled: bool = False
    model_authority: str = "SHADOW_ONLY"
    model_decision_gate: Decimal = Decimal("0.55")
    llm_events_enabled: bool = False
    bedrock_model_id: str | None = None
    bedrock_prompt_version: str = "event-v1"

    def __post_init__(self) -> None:
        if self.state_backend not in {"memory", "dynamodb"}:
            raise ValueError("STATE_BACKEND must be memory or dynamodb")
        if self.public_delay_seconds < 0:
            raise ValueError("PUBLIC_DELAY_SECONDS must be nonnegative")
        if self.runtime_role not in {"local", "reporting", "worker"}:
            raise ValueError("RUNTIME_ROLE must be local, reporting, or worker")
        if self.worker_poll_seconds < 1:
            raise ValueError("WORKER_POLL_SECONDS must be positive")
        if not Decimal("0.5") < self.model_decision_gate <= Decimal("1"):
            raise ValueError("MODEL_DECISION_GATE must be greater than 0.5 and at most 1")
        if self.model_authority not in {"SHADOW_ONLY", "PAPER_LIVE"}:
            raise ValueError("MODEL_AUTHORITY must be SHADOW_ONLY or PAPER_LIVE")
        if self.model_execution_enabled and not self.paper_execution_enabled:
            raise ValueError("MODEL_EXECUTION_ENABLED requires PAPER_EXECUTION_ENABLED")
        if self.model_execution_enabled and self.model_authority != "PAPER_LIVE":
            raise ValueError("MODEL_EXECUTION_ENABLED requires MODEL_AUTHORITY=PAPER_LIVE")
        if self.model_options_execution_enabled and not (
            self.paper_execution_enabled
            and self.model_execution_enabled
            and self.model_authority == "PAPER_LIVE"
        ):
            raise ValueError(
                "MODEL_OPTIONS_EXECUTION_ENABLED requires paper execution and "
                "PAPER_LIVE model execution"
            )
        if (
            self.runtime_role == "worker"
            and self.model_authority == "PAPER_LIVE"
            and not self.model_execution_enabled
        ):
            raise ValueError("worker PAPER_LIVE authority requires MODEL_EXECUTION_ENABLED")
        if self.llm_events_enabled and not self.bedrock_model_id:
            raise ValueError("LLM_EVENTS_ENABLED requires BEDROCK_MODEL_ID")

    @classmethod
    def from_env(cls) -> Settings:
        alpaca_key = os.getenv("ALPACA_KEY")
        alpaca_secret = os.getenv("ALPACA_SECRET")
        packed_credentials = os.getenv("ALPACA_CREDENTIALS")
        if packed_credentials and (not alpaca_key or not alpaca_secret):
            credentials = json.loads(packed_credentials)
            if not isinstance(credentials, dict):
                raise ValueError("ALPACA_CREDENTIALS must be a JSON object")
            alpaca_key = alpaca_key or credentials.get("ALPACA_KEY")
            alpaca_secret = alpaca_secret or credentials.get("ALPACA_SECRET")
            if not isinstance(alpaca_key, str) or not isinstance(alpaca_secret, str):
                raise ValueError("ALPACA_CREDENTIALS must contain string key and secret values")
        return cls(
            alpaca_key=alpaca_key,
            alpaca_secret=alpaca_secret,
            state_backend=os.getenv("STATE_BACKEND", "memory"),
            competition_id=os.getenv("COMPETITION_ID", "local-development"),
            aws_region=os.getenv("AWS_REGION", "us-east-1"),
            dynamodb_table=os.getenv("DYNAMODB_TABLE", "catalyst-router-local"),
            dynamodb_endpoint_url=os.getenv("DYNAMODB_ENDPOINT_URL"),
            auto_reconcile=_as_bool(os.getenv("AUTO_RECONCILE")),
            public_delay_seconds=int(os.getenv("PUBLIC_DELAY_SECONDS", "900")),
            runtime_role=os.getenv("RUNTIME_ROLE", "local"),
            worker_poll_seconds=int(os.getenv("WORKER_POLL_SECONDS", "15")),
            cors_origins=tuple(
                origin.strip()
                for origin in os.getenv(
                    "CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
                ).split(",")
                if origin.strip()
            ),
            challenger_manifest_uri=os.getenv("CHALLENGER_MANIFEST_URI"),
            challenger_manifest_sha256=os.getenv("CHALLENGER_MANIFEST_SHA256"),
            paper_execution_enabled=_as_bool(os.getenv("PAPER_EXECUTION_ENABLED")),
            model_execution_enabled=_as_bool(os.getenv("MODEL_EXECUTION_ENABLED")),
            model_options_execution_enabled=_as_bool(os.getenv("MODEL_OPTIONS_EXECUTION_ENABLED")),
            model_authority=os.getenv("MODEL_AUTHORITY", "SHADOW_ONLY"),
            model_decision_gate=Decimal(os.getenv("MODEL_DECISION_GATE", "0.55")),
            llm_events_enabled=_as_bool(os.getenv("LLM_EVENTS_ENABLED")),
            bedrock_model_id=os.getenv("BEDROCK_MODEL_ID"),
            bedrock_prompt_version=os.getenv("BEDROCK_PROMPT_VERSION", "event-v1"),
        )

    def require_alpaca(self) -> tuple[str, str]:
        if not self.alpaca_key or not self.alpaca_secret:
            raise RuntimeError("ALPACA_KEY and ALPACA_SECRET are required")
        return self.alpaca_key, self.alpaca_secret
