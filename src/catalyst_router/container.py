from __future__ import annotations

from dataclasses import dataclass

from catalyst_router.adapters.alpaca import AlpacaPaperBroker
from catalyst_router.adapters.bedrock import BedrockEventExtractor
from catalyst_router.adapters.dynamodb import DynamoOperationalStore
from catalyst_router.adapters.market_data import AlpacaIEXMarketData
from catalyst_router.adapters.memory import InMemoryOperationalStore
from catalyst_router.adapters.news import AlpacaNewsSource
from catalyst_router.adapters.options import AlpacaOptionMarketData
from catalyst_router.adapters.universe import AlpacaUniverseSource
from catalyst_router.challenger import ShadowChallenger
from catalyst_router.events import ShadowEventRouter
from catalyst_router.execution import DirectionalOptionStrategy, LiveTradingCycle, ModelStrategy
from catalyst_router.ports import OperationalStore, PaperBroker
from catalyst_router.risk import RiskGovernor
from catalyst_router.service import ReconciliationService
from catalyst_router.settings import Settings
from catalyst_router.universe import DailyUniverse
from catalyst_router.worker import TradingWorker


@dataclass(slots=True)
class Container:
    settings: Settings
    store: OperationalStore
    broker: PaperBroker | None
    challenger: ShadowChallenger | None = None
    market_data: AlpacaIEXMarketData | None = None
    option_market_data: AlpacaOptionMarketData | None = None
    universe: DailyUniverse | None = None

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
        option_market_data = None
        universe = None
        if settings.runtime_role != "reporting" and settings.alpaca_key and settings.alpaca_secret:
            broker = AlpacaPaperBroker(settings.alpaca_key, settings.alpaca_secret)
            market_data = AlpacaIEXMarketData(settings.alpaca_key, settings.alpaca_secret)
            option_market_data = AlpacaOptionMarketData(settings.alpaca_key, settings.alpaca_secret)
            universe = DailyUniverse(
                store=store,
                source=AlpacaUniverseSource(settings.alpaca_key, settings.alpaca_secret),
            )
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
            if settings.model_authority == "PAPER_LIVE":
                challenger.authorize_paper_execution(settings.model_decision_gate)
        return cls(
            settings=settings,
            store=store,
            broker=broker,
            challenger=challenger,
            market_data=market_data,
            option_market_data=option_market_data,
            universe=universe,
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
        if self.universe is None:
            raise RuntimeError("Alpaca universe discovery is not configured")
        live_trader = None
        event_router = None
        if self.settings.llm_events_enabled:
            if not self.settings.bedrock_model_id:
                raise RuntimeError("Bedrock model is not configured")
            key, secret = self.settings.require_alpaca()
            event_router = ShadowEventRouter(
                store=self.store,
                news=AlpacaNewsSource(key, secret),
                extractor=BedrockEventExtractor(
                    model_id=self.settings.bedrock_model_id,
                    prompt_version=self.settings.bedrock_prompt_version,
                    region=self.settings.aws_region,
                ),
                quotes=self.market_data,
            )
        if self.settings.paper_execution_enabled:
            if self.broker is None:
                raise RuntimeError("paper execution requires Alpaca trading credentials")
            live_trader = LiveTradingCycle(
                store=self.store,
                broker=self.broker,
                quotes=self.market_data,
                strategy=(
                    (
                        DirectionalOptionStrategy(
                            self.challenger,
                            self._require_option_market_data(),
                            decision_gate=self.settings.model_decision_gate,
                        )
                        if self.settings.model_options_execution_enabled
                        else ModelStrategy(
                            self.challenger,
                            decision_gate=self.settings.model_decision_gate,
                        )
                    )
                    if self.settings.model_execution_enabled
                    else None
                ),
                risk_governor=RiskGovernor(
                    options_execution_enabled=self.settings.model_options_execution_enabled
                ),
                option_quotes=(
                    self._require_option_market_data()
                    if self.settings.model_options_execution_enabled
                    else None
                ),
            )
        return TradingWorker(
            store=self.store,
            market_data=self.market_data,
            challenger=self.challenger,
            universe=self.universe,
            live_trader=live_trader,
            event_router=event_router,
        )

    def _require_option_market_data(self) -> AlpacaOptionMarketData:
        if self.option_market_data is None:
            raise RuntimeError("Alpaca option market data is not configured")
        return self.option_market_data
