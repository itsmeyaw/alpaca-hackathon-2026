from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import boto3
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

from catalyst_router.domain import AgentState, DecisionRecord, PublicDecisionRecord

if TYPE_CHECKING:
    from mypy_boto3_dynamodb.type_defs import TransactWriteItemTypeDef


class DynamoOperationalStore:
    """Minimal DynamoDB store using optimistic writes and immutable decisions."""

    def __init__(
        self,
        *,
        table_name: str,
        competition_id: str,
        region: str,
        endpoint_url: str | None = None,
        public_delay_seconds: int = 0,
    ) -> None:
        resource = boto3.resource("dynamodb", region_name=region, endpoint_url=endpoint_url)
        self._table = resource.Table(table_name)
        self._client = boto3.client("dynamodb", region_name=region, endpoint_url=endpoint_url)
        self._competition_id = competition_id
        self._public_delay = timedelta(seconds=public_delay_seconds)

    @property
    def _control_key(self) -> dict[str, str]:
        return {"PK": f"COMP#{self._competition_id}", "SK": "CONTROL#AGENT_MODE"}

    def initialize(self) -> AgentState:
        state = AgentState()
        item: dict[str, Any] = {
            **self._control_key,
            "payload": state.model_dump_json(),
            "version": state.version,
        }
        try:
            self._table.put_item(
                Item=item,
                ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
            )
            return state
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            return self.get_agent_state()

    def get_agent_state(self) -> AgentState:
        response = self._table.get_item(Key=self._control_key, ConsistentRead=True)
        item = response.get("Item")
        if item is None:
            return self.initialize()
        payload = item.get("payload")
        if not isinstance(payload, (str, bytes, bytearray)):
            raise RuntimeError("agent state payload is missing or invalid")
        return AgentState.model_validate_json(payload)

    def begin_execution(self) -> AgentState:
        state = self.get_agent_state()
        new_state = state.model_copy(
            update={
                "version": state.version + 1,
                "execution_epoch": str(uuid4()),
                "reconciled_epoch": None,
                "updated_at": datetime.now(UTC),
            }
        )
        self._replace_state(state, new_state)
        return new_state

    def commit_reconciliation(self, epoch: str, record: DecisionRecord) -> AgentState:
        state = self.get_agent_state()
        if state.execution_epoch != epoch:
            raise RuntimeError("execution epoch changed during reconciliation")
        new_state = state.model_copy(
            update={
                "version": state.version + 1,
                "reconciled_epoch": epoch,
                "updated_at": datetime.now(UTC),
            }
        )
        serializer = TypeSerializer()

        def serialized(item: dict[str, Any]) -> dict[str, Any]:
            return {key: serializer.serialize(value) for key, value in item.items()}

        operations: list[TransactWriteItemTypeDef] = [
            {
                "Put": {
                    "TableName": self._table.name,
                    "Item": serialized(
                        {
                            **self._control_key,
                            "payload": new_state.model_dump_json(),
                            "version": new_state.version,
                        }
                    ),
                    "ConditionExpression": "attribute_exists(PK) AND #version = :expected",
                    "ExpressionAttributeNames": {"#version": "version"},
                    "ExpressionAttributeValues": {":expected": serializer.serialize(state.version)},
                }
            },
            *self._decision_operations(record, serializer),
        ]
        self._client.transact_write_items(TransactItems=operations)
        return new_state

    def _replace_state(self, old: AgentState, new: AgentState) -> None:
        self._table.put_item(
            Item={**self._control_key, "payload": new.model_dump_json(), "version": new.version},
            ConditionExpression="attribute_exists(PK) AND #version = :expected",
            ExpressionAttributeNames={"#version": "version"},
            ExpressionAttributeValues={":expected": old.version},
        )

    def append_decision(self, record: DecisionRecord) -> None:
        serializer = TypeSerializer()
        operations = self._decision_operations(record, serializer)
        self._client.transact_write_items(TransactItems=operations)

    def _decision_operations(
        self, record: DecisionRecord, serializer: TypeSerializer
    ) -> list[TransactWriteItemTypeDef]:
        occurred = record.occurred_at.astimezone(UTC)
        visible = (occurred + self._public_delay).isoformat(timespec="microseconds")

        def serialized(item: dict[str, Any]) -> dict[str, Any]:
            return {key: serializer.serialize(value) for key, value in item.items()}

        operations: list[TransactWriteItemTypeDef] = [
            {
                "Put": {
                    "TableName": self._table.name,
                    "Item": serialized(
                        {
                            "PK": f"DECISION#{self._competition_id}#{record.decision_id}",
                            "SK": "RECORD",
                            "payload": record.model_dump_json(),
                        }
                    ),
                    "ConditionExpression": "attribute_not_exists(PK)",
                }
            }
        ]
        if record.public:
            operations.append(
                {
                    "Put": {
                        "TableName": self._table.name,
                        "Item": serialized(
                            {
                                "PK": f"PUBLIC#COMP#{self._competition_id}",
                                "SK": f"VISIBLE#{visible}#{record.decision_id}",
                                "payload": record.public_projection_json(),
                            }
                        ),
                        "ConditionExpression": "attribute_not_exists(PK)",
                    }
                }
            )
        return operations

    def list_public_decisions(self, limit: int = 50) -> list[PublicDecisionRecord]:
        now = datetime.now(UTC).isoformat(timespec="microseconds")
        response = self._table.query(
            KeyConditionExpression=Key("PK").eq(f"PUBLIC#COMP#{self._competition_id}")
            & Key("SK").between("VISIBLE#", f"VISIBLE#{now}#~"),
            ScanIndexForward=False,
            Limit=limit,
            ConsistentRead=True,
        )
        records: list[PublicDecisionRecord] = []
        for item in response["Items"]:
            payload = item.get("payload")
            if not isinstance(payload, (str, bytes, bytearray)):
                raise RuntimeError("decision payload is missing or invalid")
            records.append(PublicDecisionRecord.model_validate_json(payload))
        return records
