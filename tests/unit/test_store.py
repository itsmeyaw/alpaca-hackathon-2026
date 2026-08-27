from datetime import UTC, datetime, timedelta

import pytest

from catalyst_router.adapters.memory import InMemoryOperationalStore
from catalyst_router.domain import AgentMode, DecisionRecord


def test_store_defaults_to_paused_and_restart_epoch_requires_reconciliation() -> None:
    store = InMemoryOperationalStore()

    initial = store.initialize()
    started = store.begin_execution()

    assert initial.mode is AgentMode.PAUSED
    assert not started.is_reconciled
    record = DecisionRecord.create(decision_type="RECONCILIATION_COMPLETED", summary="ok")
    assert store.commit_reconciliation(started.execution_epoch, record).is_reconciled


def test_decisions_are_immutable_and_publicly_filterable() -> None:
    store = InMemoryOperationalStore()
    public = DecisionRecord.create(
        decision_type="NO_TRADE",
        summary="private stale quote detail",
        public=True,
        public_summary="stale quote",
    )
    private = DecisionRecord.create(decision_type="OPERATOR_ACTION", summary="paused", public=False)

    store.append_decision(public)
    store.append_decision(private)

    assert store.list_public_decisions() == [public.public_projection()]
    with pytest.raises(ValueError, match="decision already exists"):
        store.append_decision(public)


def test_publication_delay_hides_recent_decisions() -> None:
    store = InMemoryOperationalStore(public_delay_seconds=900)
    recent = DecisionRecord.create(
        decision_type="NO_TRADE",
        summary="private recent detail",
        public=True,
        public_summary="recent",
    )
    old = DecisionRecord.create(
        decision_type="NO_TRADE",
        summary="old",
        occurred_at=datetime.now(UTC) - timedelta(minutes=16),
        public=True,
        public_summary="old",
    )

    store.append_decision(recent)
    store.append_decision(old)

    assert store.list_public_decisions() == [old.public_projection()]


def test_public_projection_excludes_internal_payload() -> None:
    record = DecisionRecord.create(
        decision_type="NO_TRADE",
        summary="vetoed",
        payload={"raw_prompt": "private", "account_id": "private"},
        public=True,
        public_summary="sanitized veto",
    )

    projection = record.public_projection_json()

    assert "raw_prompt" not in projection
    assert "account_id" not in projection
    assert "sanitized veto" in projection
