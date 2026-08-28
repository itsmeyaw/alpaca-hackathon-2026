from __future__ import annotations

from dataclasses import dataclass

from catalyst_router.adapters.alpaca import AlpacaPaperBroker
from catalyst_router.adapters.dynamodb import DynamoOperationalStore
from catalyst_router.adapters.market_data import AlpacaIEXMarketData
from catalyst_router.adapters.memory import InMemoryOperationalStore
from catalyst_router.challenger import ShadowChallenger
from catalyst_router.execution import LiveTradingCycle
from catalyst_router.ports import OperationalStore, PaperBroker
from catalyst_router.service import ReconciliationService
from catalyst_router.settings import Settings
from catalyst_router.worker import TradingWorker


@dataclass(slots=True)
class Container:
    settings: Settings
    store: OperationalStore
    broker: PaperBroker | None
    challenger: ShadowChallenger | None = None
    market_data: AlpacaIEXMarketData | None = None

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
                initialize_missing=settings.runtime_role != "reporting",
            )
        else:
            raise ValueError(f"unsupported STATE_BACKEND: {settings.state_backend}")
        if settings.runtime_role != "reporting":
            store.initialize()

        broker: PaperBroker | None = None
        market_data = None
        if settings.runtime_role != "reporting" and settings.alpaca_key and settings.alpaca_secret:
            broker = AlpacaPaperBroker(settings.alpaca_key, settings.alpaca_secret)
            market_data = AlpacaIEXMarketData(settings.alpaca_key, settings.alpaca_secret)
        if settings.auto_reconcile and broker is None:
            raise RuntimeError("AUTO_RECONCILE requires Alpaca credentials")
        challenger = None
        if settings.challenger_manifest_uri:
            if not settings.challenger_manifest_sha256:
                raise RuntimeError(
                    "CHALLENGER_MANIFEST_SHA256 is required with CHALLENGER_MANIFEST_URI"
                )
            challenger = ShadowChallenger.load_from_s3(
                settings.challenger_manifest_uri,
                settings.aws_region,
                settings.challenger_manifest_sha256,
            )
        return cls(
            settings=settings,
            store=store,
            broker=broker,
            challenger=challenger,
            market_data=market_data,
        )

    def reconciliation_service(self) -> ReconciliationService:
        if self.broker is None:
            raise RuntimeError("Alpaca credentials are not configured")
        return ReconciliationService(broker=self.broker, store=self.store)

    def trading_worker(self) -> TradingWorker:
        if self.market_data is None:
            raise RuntimeError("Alpaca market data credentials are not configured")
        if self.challenger is None:
            raise RuntimeError("a challenger model is not configured")
        live_trader = None
        if self.settings.paper_execution_enabled:
            if self.broker is None:
                raise RuntimeError("paper execution requires Alpaca trading credentials")
            live_trader = LiveTradingCycle(
                store=self.store,
                broker=self.broker,
                quotes=self.market_data,
            )
        return TradingWorker(
            store=self.store,
            market_data=self.market_data,
            challenger=self.challenger,
            live_trader=live_trader,
        )
