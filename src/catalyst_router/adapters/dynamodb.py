from __future__ import annotations

import base64
import hashlib
import json
from binascii import Error as BinasciiError
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

import boto3
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeSerializer
from botocore.exceptions import ClientError

from catalyst_router.domain import (
    AgentMode,
    AgentState,
    DecisionRecord,
    OrderExecution,
    OrderExecutionStatus,
    PublicDecisionPage,
    PublicDecisionRecord,
    Route,
)

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
        initialize_missing: bool = True,
    ) -> None:
        resource = boto3.resource("dynamodb", region_name=region, endpoint_url=endpoint_url)
        self._table = resource.Table(table_name)
        self._client = boto3.client("dynamodb", region_name=region, endpoint_url=endpoint_url)
        self._competition_id = competition_id
        self._public_delay = timedelta(seconds=public_delay_seconds)
        self._initialize_missing = initialize_missing

    @property
    def _control_key(self) -> dict[str, str]:
        return {"PK": f"COMP#{self._competition_id}", "SK": "CONTROL#AGENT_MODE"}

    def initialize(self) -> AgentState:
        state = AgentState()
        item: dict[str, Any] = {
            **self._control_key,
            "payload": state.model_dump_json(),
            "mode": state.mode,
            "version": state.version,
            "execution_epoch": state.execution_epoch,
            "reconciled_epoch": state.reconciled_epoch,
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
            if self._initialize_missing:
                return self.initialize()
            return AgentState(reason="operational state is not initialized")
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

    def transition_agent_mode(
        self, mode: AgentMode, *, reason: str, record: DecisionRecord
    ) -> AgentState:
        state = self.get_agent_state()
        if state.mode is AgentMode.KILLED:
            raise RuntimeError("KILLED agent mode is terminal")
        if mode is AgentMode.RUNNING and not state.is_reconciled:
            raise RuntimeError("startup reconciliation is required before RUNNING")
        new_state = state.model_copy(
            update={
                "mode": mode,
                "reason": reason,
                "version": state.version + 1,
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
                            "mode": new_state.mode,
                            "version": new_state.version,
                            "execution_epoch": new_state.execution_epoch,
                            "reconciled_epoch": new_state.reconciled_epoch,
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

    def commit_reconciliation(
        self, epoch: str, record: DecisionRecord, *, equity: Decimal | None = None
    ) -> AgentState:
        state = self.get_agent_state()
        if state.execution_epoch != epoch:
            raise RuntimeError("execution epoch changed during reconciliation")
        new_state = state.model_copy(
            update={
                "version": state.version + 1,
                "reconciled_epoch": epoch,
                "equity_peak": (
                    max(state.equity_peak or equity, equity)
                    if equity is not None
                    else state.equity_peak
                ),
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
                            "mode": new_state.mode,
                            "version": new_state.version,
                            "execution_epoch": new_state.execution_epoch,
                            "reconciled_epoch": new_state.reconciled_epoch,
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
            Item={
                **self._control_key,
                "payload": new.model_dump_json(),
                "mode": new.mode,
                "version": new.version,
                "execution_epoch": new.execution_epoch,
                "reconciled_epoch": new.reconciled_epoch,
            },
            ConditionExpression="attribute_exists(PK) AND #version = :expected",
            ExpressionAttributeNames={"#version": "version"},
            ExpressionAttributeValues={":expected": old.version},
        )

    def append_decision(self, record: DecisionRecord) -> None:
        serializer = TypeSerializer()
        operations = self._decision_operations(record, serializer)
        self._client.transact_write_items(TransactItems=operations)

    def append_decision_once(
        self, record: DecisionRecord, *, expected_epoch: str | None = None
    ) -> bool:
        serializer = TypeSerializer()
        operations = self._decision_operations(record, serializer)
        if expected_epoch is not None:
            operations.insert(
                0,
                {
                    "ConditionCheck": {
                        "TableName": self._table.name,
                        "Key": {
                            key: serializer.serialize(value)
                            for key, value in self._control_key.items()
                        },
                        "ConditionExpression": (
                            "execution_epoch = :epoch AND reconciled_epoch = :epoch"
                        ),
                        "ExpressionAttributeValues": {
                            ":epoch": serializer.serialize(expected_epoch)
                        },
                    }
                },
            )
        try:
            self._client.transact_write_items(TransactItems=operations)
            return True
        except ClientError as exc:
            reasons = exc.response.get("CancellationReasons", [])
            if (
                expected_epoch is not None
                and reasons
                and reasons[0].get("Code") == "ConditionalCheckFailed"
            ):
                raise RuntimeError("worker lost execution epoch ownership") from exc
            reason_codes = {reason.get("Code") for reason in reasons}
            if (
                exc.response["Error"]["Code"] == "TransactionCanceledException"
                and "ConditionalCheckFailed" in reason_codes
                and reason_codes <= {"ConditionalCheckFailed", "None"}
            ):
                return False
            raise

    def claim_event_extraction(
        self,
        source_id: str,
        model_id: str,
        prompt_version: str,
        *,
        expected_epoch: str,
    ) -> bool:
        serializer = TypeSerializer()
        claim_hash = hashlib.sha256(
            f"{source_id}\0{model_id}\0{prompt_version}".encode()
        ).hexdigest()
        operations: list[TransactWriteItemTypeDef] = [
            {
                "ConditionCheck": {
                    "TableName": self._table.name,
                    "Key": {
                        key: serializer.serialize(value) for key, value in self._control_key.items()
                    },
                    "ConditionExpression": (
                        "execution_epoch = :epoch AND reconciled_epoch = :epoch"
                    ),
                    "ExpressionAttributeValues": {":epoch": serializer.serialize(expected_epoch)},
                }
            },
            {
                "Put": {
                    "TableName": self._table.name,
                    "Item": {
                        key: serializer.serialize(value)
                        for key, value in {
                            "PK": f"COMP#{self._competition_id}",
                            "SK": f"EVENT_CLAIM#{claim_hash}",
                            "source_id": source_id,
                            "model_id": model_id,
                            "prompt_version": prompt_version,
                        }.items()
                    },
                    "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
                }
            },
        ]
        try:
            self._client.transact_write_items(TransactItems=operations)
            return True
        except ClientError as exc:
            reasons = exc.response.get("CancellationReasons", [])
            if reasons and reasons[0].get("Code") == "ConditionalCheckFailed":
                raise RuntimeError("worker lost execution epoch ownership") from exc
            if len(reasons) > 1 and reasons[1].get("Code") == "ConditionalCheckFailed":
                return False
            raise

    def release_event_extraction(
        self,
        source_id: str,
        model_id: str,
        prompt_version: str,
        *,
        expected_epoch: str,
    ) -> None:
        state = self.get_agent_state()
        if state.execution_epoch != expected_epoch or not state.is_reconciled:
            raise RuntimeError("worker lost execution epoch ownership")
        claim_hash = hashlib.sha256(
            f"{source_id}\0{model_id}\0{prompt_version}".encode()
        ).hexdigest()
        self._table.delete_item(
            Key={"PK": f"COMP#{self._competition_id}", "SK": f"EVENT_CLAIM#{claim_hash}"}
        )

    @property
    def _order_partition_key(self) -> str:
        return f"COMP#{self._competition_id}"

    def claim_order(
        self, execution: OrderExecution, *, expected_epoch: str, max_active_orders: int
    ) -> bool:
        client_order_id = execution.plan.client_order_id
        state = self.get_agent_state()
        if (
            state.mode is not AgentMode.RUNNING
            or state.execution_epoch != expected_epoch
            or not state.is_reconciled
        ):
            raise RuntimeError("agent is not authorized for new exposure")
        existing = self.get_order(client_order_id)
        if existing is not None:
            if (
                existing.status in {OrderExecutionStatus.PREPARED, OrderExecutionStatus.UNKNOWN}
                and client_order_id not in state.active_order_ids
            ):
                raise RuntimeError("agent is not authorized for order recovery")
            return False
        if len(state.active_order_ids) >= max_active_orders:
            raise RuntimeError("agent is not authorized for new exposure")
        new_state = state.model_copy(
            update={
                "active_order_ids": (*state.active_order_ids, client_order_id),
                "version": state.version + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        serializer = TypeSerializer()
        order_item = {
            "PK": self._order_partition_key,
            "SK": f"ORDER#{client_order_id}",
            "payload": execution.model_dump_json(),
            "status": execution.status,
            "version": execution.version,
        }
        operations: list[TransactWriteItemTypeDef] = [
            {
                "Put": {
                    "TableName": self._table.name,
                    "Item": {
                        key: serializer.serialize(value)
                        for key, value in {
                            **self._control_key,
                            "payload": new_state.model_dump_json(),
                            "mode": new_state.mode,
                            "version": new_state.version,
                            "execution_epoch": new_state.execution_epoch,
                            "reconciled_epoch": new_state.reconciled_epoch,
                        }.items()
                    },
                    "ConditionExpression": (
                        "#version = :expected_version AND #mode = :running "
                        "AND execution_epoch = :epoch AND reconciled_epoch = :epoch"
                    ),
                    "ExpressionAttributeNames": {"#mode": "mode", "#version": "version"},
                    "ExpressionAttributeValues": {
                        ":expected_version": serializer.serialize(state.version),
                        ":running": serializer.serialize(AgentMode.RUNNING),
                        ":epoch": serializer.serialize(expected_epoch),
                    },
                }
            },
            {
                "Put": {
                    "TableName": self._table.name,
                    "Item": {key: serializer.serialize(value) for key, value in order_item.items()},
                    "ConditionExpression": "attribute_not_exists(PK) AND attribute_not_exists(SK)",
                }
            },
        ]
        try:
            self._client.transact_write_items(TransactItems=operations)
            return True
        except ClientError as exc:
            reasons = exc.response.get("CancellationReasons", [])
            if reasons and reasons[0].get("Code") == "ConditionalCheckFailed":
                raise RuntimeError("agent is not authorized for new exposure") from exc
            if len(reasons) > 1 and reasons[1].get("Code") == "ConditionalCheckFailed":
                return False
            raise

    def get_order(self, client_order_id: str) -> OrderExecution | None:
        response = self._table.get_item(
            Key={"PK": self._order_partition_key, "SK": f"ORDER#{client_order_id}"},
            ConsistentRead=True,
        )
        item = response.get("Item")
        if item is None:
            return None
        payload = item.get("payload")
        if not isinstance(payload, (str, bytes, bytearray)):
            raise RuntimeError("order execution payload is missing or invalid")
        return OrderExecution.model_validate_json(payload)

    def clear_active_order(self, client_order_id: str) -> AgentState:
        state = self.get_agent_state()
        if client_order_id not in state.active_order_ids:
            raise RuntimeError("active order changed concurrently")
        new_state = state.model_copy(
            update={
                "active_order_ids": tuple(
                    tracked for tracked in state.active_order_ids if tracked != client_order_id
                ),
                "version": state.version + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        self._replace_state(state, new_state)
        return new_state

    def update_equity_peak(self, equity: Decimal) -> AgentState:
        state = self.get_agent_state()
        if state.equity_peak is not None and equity <= state.equity_peak:
            return state
        new_state = state.model_copy(
            update={
                "equity_peak": equity,
                "version": state.version + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        self._replace_state(state, new_state)
        return new_state

    def update_order(
        self,
        execution: OrderExecution,
        *,
        expected_status: OrderExecutionStatus,
    ) -> OrderExecution:
        self._table.put_item(
            Item={
                "PK": self._order_partition_key,
                "SK": f"ORDER#{execution.plan.client_order_id}",
                "payload": execution.model_dump_json(),
                "status": execution.status,
                "version": execution.version,
            },
            ConditionExpression=(
                "attribute_exists(PK) AND #status = :expected_status "
                "AND #version = :expected_version"
            ),
            ExpressionAttributeNames={"#status": "status", "#version": "version"},
            ExpressionAttributeValues={
                ":expected_status": expected_status,
                ":expected_version": execution.version - 1,
            },
        )
        return execution

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

    def list_public_decision_page(
        self,
        *,
        limit: int = 25,
        cursor: str | None = None,
        search: str | None = None,
        route: Route | None = None,
        decision_type: str | None = None,
    ) -> PublicDecisionPage:
        now = datetime.now(UTC).isoformat(timespec="microseconds")
        cursor_key = self._decode_public_cursor(cursor) if cursor else None
        normalized_search = search.casefold().strip() if search else None
        normalized_type = decision_type.casefold() if decision_type else None
        records: list[PublicDecisionRecord] = []

        while len(records) < limit:
            query_arguments: dict[str, Any] = {
                "KeyConditionExpression": Key("PK").eq(f"PUBLIC#COMP#{self._competition_id}")
                & Key("SK").between("VISIBLE#", f"VISIBLE#{now}#~"),
                "ScanIndexForward": False,
                "Limit": limit,
                "ConsistentRead": True,
            }
            if cursor_key:
                query_arguments["ExclusiveStartKey"] = cursor_key
            response = self._table.query(**cast(Any, query_arguments))
            for item in response["Items"]:
                payload = item.get("payload")
                if not isinstance(payload, (str, bytes, bytearray)):
                    raise RuntimeError("decision payload is missing or invalid")
                record = PublicDecisionRecord.model_validate_json(payload)
                if self._matches_public_decision(
                    record,
                    search=normalized_search,
                    route=route,
                    decision_type=normalized_type,
                ):
                    records.append(record)
            cursor_key = response.get("LastEvaluatedKey")
            if cursor_key is None:
                break

        return PublicDecisionPage(
            records=records,
            next_cursor=self._encode_public_cursor(cursor_key) if cursor_key else None,
        )

    @staticmethod
    def _matches_public_decision(
        record: PublicDecisionRecord,
        *,
        search: str | None,
        route: Route | None,
        decision_type: str | None,
    ) -> bool:
        if route is not None and record.route is not route:
            return False
        if decision_type is not None and record.decision_type.casefold() != decision_type:
            return False
        if search is None:
            return True
        return (
            search
            in " ".join(
                value
                for value in (
                    record.symbol,
                    record.decision_type,
                    record.route,
                    record.summary,
                )
                if value
            ).casefold()
        )

    @staticmethod
    def _decode_public_cursor(cursor: str) -> dict[str, Any]:
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            decoded = json.loads(base64.urlsafe_b64decode(padded).decode())
        except (BinasciiError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("invalid cursor") from error
        if not isinstance(decoded, dict) or not all(
            isinstance(value, str) for value in decoded.values()
        ):
            raise ValueError("invalid cursor")
        return decoded

    @staticmethod
    def _encode_public_cursor(cursor_key: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(cursor_key).encode()).decode().rstrip("=")
