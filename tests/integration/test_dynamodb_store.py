from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import uuid4

import boto3
import pytest
from botocore.exceptions import ClientError

from catalyst_router.adapters.dynamodb import DynamoOperationalStore
from catalyst_router.domain import AgentMode, DecisionRecord


@pytest.fixture
def dynamodb_store() -> Iterator[DynamoOperationalStore]:
    endpoint = os.getenv("DYNAMODB_ENDPOINT_URL")
    if endpoint is None:
        pytest.skip("DYNAMODB_ENDPOINT_URL is not configured")
    table_name = f"catalyst-router-test-{uuid4().hex}"
    client = boto3.client(
        "dynamodb",
        region_name="us-east-1",
        endpoint_url=endpoint,
        aws_access_key_id="local",
        aws_secret_access_key="local",
    )
    client.create_table(
        TableName=table_name,
        KeySchema=[
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    store = DynamoOperationalStore(
        table_name=table_name,
        competition_id="integration-test",
        region="us-east-1",
        endpoint_url=endpoint,
    )
    yield store
    client.delete_table(TableName=table_name)


@pytest.mark.integration
def test_dynamodb_store_fences_restart_and_keeps_decisions_immutable(
    dynamodb_store: DynamoOperationalStore,
) -> None:
    initial = dynamodb_store.initialize()
    started = dynamodb_store.begin_execution()

    assert initial.mode is AgentMode.PAUSED
    assert not started.is_reconciled
    reconciliation = DecisionRecord.create(
        decision_type="RECONCILIATION_COMPLETED",
        summary="internal reconciliation detail",
        public=True,
        public_summary="reconciliation completed",
    )
    assert dynamodb_store.commit_reconciliation(
        started.execution_epoch, reconciliation
    ).is_reconciled

    record = DecisionRecord.create(
        decision_type="NO_TRADE",
        summary="private quality detail",
        public=True,
        public_summary="quality veto",
    )
    dynamodb_store.append_decision(record)
    assert dynamodb_store.list_public_decisions() == [
        record.public_projection(),
        reconciliation.public_projection(),
    ]

    with pytest.raises(ClientError):
        dynamodb_store.append_decision(record)
