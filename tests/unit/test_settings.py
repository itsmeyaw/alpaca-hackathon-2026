from decimal import Decimal

import pytest

from catalyst_router.settings import Settings


def test_rejects_negative_publication_delay() -> None:
    with pytest.raises(ValueError, match="nonnegative"):
        Settings(
            alpaca_key=None,
            alpaca_secret=None,
            state_backend="memory",
            competition_id="test",
            aws_region="us-east-1",
            dynamodb_table="unused",
            dynamodb_endpoint_url=None,
            auto_reconcile=False,
            public_delay_seconds=-1,
        )


def test_auto_reconcile_without_credentials_is_rejected_by_container() -> None:
    from catalyst_router.container import Container

    configured = Settings(
        alpaca_key=None,
        alpaca_secret=None,
        state_backend="memory",
        competition_id="test",
        aws_region="us-east-1",
        dynamodb_table="unused",
        dynamodb_endpoint_url=None,
        auto_reconcile=True,
        public_delay_seconds=0,
    )

    with pytest.raises(RuntimeError, match="requires Alpaca credentials"):
        Container.build(configured)


def test_reads_packed_alpaca_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALPACA_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET", raising=False)
    monkeypatch.setenv(
        "ALPACA_CREDENTIALS",
        '{"ALPACA_KEY":"paper-key","ALPACA_SECRET":"paper-secret"}',
    )

    configured = Settings.from_env()

    assert configured.require_alpaca() == ("paper-key", "paper-secret")


def test_rejects_invalid_packed_alpaca_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ALPACA_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET", raising=False)
    monkeypatch.setenv("ALPACA_CREDENTIALS", '{"ALPACA_KEY":"paper-key"}')

    with pytest.raises(ValueError, match="must contain string key and secret values"):
        Settings.from_env()


def test_reads_live_model_execution_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODEL_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("PAPER_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("MODEL_AUTHORITY", "PAPER_LIVE")
    monkeypatch.setenv("MODEL_DECISION_GATE", "0.55")
    monkeypatch.setenv("WORKER_POLL_SECONDS", "15")

    configured = Settings.from_env()

    assert configured.model_execution_enabled
    assert configured.model_authority == "PAPER_LIVE"
    assert configured.model_decision_gate == Decimal("0.55")
    assert configured.worker_poll_seconds == 15


def test_rejects_model_gate_without_directional_margin() -> None:
    with pytest.raises(ValueError, match="MODEL_DECISION_GATE"):
        Settings(
            alpaca_key=None,
            alpaca_secret=None,
            state_backend="memory",
            competition_id="test",
            aws_region="us-east-1",
            dynamodb_table="unused",
            dynamodb_endpoint_url=None,
            auto_reconcile=False,
            public_delay_seconds=0,
            model_decision_gate=Decimal("0.50"),
        )


def test_rejects_paper_live_worker_without_model_execution() -> None:
    with pytest.raises(ValueError, match="worker PAPER_LIVE"):
        Settings(
            alpaca_key=None,
            alpaca_secret=None,
            state_backend="memory",
            competition_id="test",
            aws_region="us-east-1",
            dynamodb_table="unused",
            dynamodb_endpoint_url=None,
            auto_reconcile=False,
            public_delay_seconds=0,
            runtime_role="worker",
            model_authority="PAPER_LIVE",
        )


def test_reads_shadow_llm_event_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_EVENTS_ENABLED", "true")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "anthropic.test-model-v1")
    monkeypatch.setenv("BEDROCK_PROMPT_VERSION", "event-v2")

    configured = Settings.from_env()

    assert configured.llm_events_enabled
    assert configured.bedrock_model_id == "anthropic.test-model-v1"
    assert configured.bedrock_prompt_version == "event-v2"


def test_rejects_llm_events_without_bedrock_model() -> None:
    with pytest.raises(ValueError, match="BEDROCK_MODEL_ID"):
        Settings(
            alpaca_key=None,
            alpaca_secret=None,
            state_backend="memory",
            competition_id="test",
            aws_region="us-east-1",
            dynamodb_table="unused",
            dynamodb_endpoint_url=None,
            auto_reconcile=False,
            public_delay_seconds=0,
            llm_events_enabled=True,
        )
