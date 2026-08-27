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
