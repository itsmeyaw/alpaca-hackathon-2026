from __future__ import annotations

from dataclasses import dataclass

from catalyst_router.adapters.alpaca import AlpacaPaperBroker
from catalyst_router.adapters.dynamodb import DynamoOperationalStore
from catalyst_router.adapters.memory import InMemoryOperationalStore
from catalyst_router.ports import OperationalStore, PaperBroker
from catalyst_router.service import ReconciliationService
from catalyst_router.settings import Settings


@dataclass(slots=True)
class Container:
    settings: Settings
    store: OperationalStore
    broker: PaperBroker | None

    @classmethod
    def build(cls, settings: Settings) -> Container:
        if settings.state_backend == "memory":
            store: OperationalStore = InMemoryOperationalStore(settings.public_delay_seconds)
        elif settings.state_backend == "dynamodb":
            store = DynamoOperationalStore(
                table_name=settings.dynamodb_table,
                competition_id=settings.competition_id,
                region=settings.aws_region,
                endpoint_url=settings.dynamodb_endpoint_url,
                public_delay_seconds=settings.public_delay_seconds,
            )
        else:
            raise ValueError(f"unsupported STATE_BACKEND: {settings.state_backend}")
        store.initialize()

        broker: PaperBroker | None = None
        if settings.alpaca_key and settings.alpaca_secret:
            broker = AlpacaPaperBroker(settings.alpaca_key, settings.alpaca_secret)
        if settings.auto_reconcile and broker is None:
            raise RuntimeError("AUTO_RECONCILE requires Alpaca credentials")
        return cls(settings=settings, store=store, broker=broker)

    def reconciliation_service(self) -> ReconciliationService:
        if self.broker is None:
            raise RuntimeError("Alpaca credentials are not configured")
        return ReconciliationService(broker=self.broker, store=self.store)
