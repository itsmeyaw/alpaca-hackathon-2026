from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class AgentMode(StrEnum):
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    RISK_HALTED = "RISK_HALTED"
    KILLED = "KILLED"


class Route(StrEnum):
    CATALYST_CONTINUATION = "CATALYST_CONTINUATION"
    LIQUIDITY_REVERSION = "LIQUIDITY_REVERSION"
    REGIME_TREND = "REGIME_TREND"
    MODEL_DIRECTIONAL = "MODEL_DIRECTIONAL"
    NO_TRADE = "NO_TRADE"


class EventDirection(StrEnum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class EventType(StrEnum):
    EARNINGS = "EARNINGS"
    GUIDANCE = "GUIDANCE"
    MERGER_ACQUISITION = "MERGER_ACQUISITION"
    REGULATORY = "REGULATORY"
    PRODUCT = "PRODUCT"
    MANAGEMENT = "MANAGEMENT"
    MACRO = "MACRO"
    ANALYST = "ANALYST"
    LEGAL = "LEGAL"
    OTHER = "OTHER"


class InstrumentType(StrEnum):
    EQUITY = "EQUITY"
    OPTION = "OPTION"


class OptionType(StrEnum):
    CALL = "CALL"
    PUT = "PUT"


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class RiskDecisionStatus(StrEnum):
    APPROVED = "APPROVED"
    REDUCED = "REDUCED"
    VETOED = "VETOED"


class OrderExecutionStatus(StrEnum):
    PREPARED = "PREPARED"
    UNKNOWN = "UNKNOWN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    REJECTED = "REJECTED"


class AgentState(FrozenModel):
    mode: AgentMode = AgentMode.PAUSED
    version: int = 0
    execution_epoch: str = "not-started"
    reconciled_epoch: str | None = None
    active_order_ids: tuple[str, ...] = ()
    equity_peak: Decimal | None = None
    competition_start_equity: Decimal | None = None
    reason: str = "initialized safely in PAUSED"
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="before")
    @classmethod
    def adopt_legacy_active_order(cls, data: Any) -> Any:
        """Reads durable state written before concurrent positions were allowed."""
        if not isinstance(data, dict) or "active_order_id" not in data:
            return data
        migrated = dict(data)
        legacy = migrated.pop("active_order_id")
        if legacy is not None and not migrated.get("active_order_ids"):
            migrated["active_order_ids"] = (legacy,)
        return migrated

    @model_validator(mode="after")
    def validate_active_order_ids(self) -> AgentState:
        if len(set(self.active_order_ids)) != len(self.active_order_ids):
            raise ValueError("active order ids must be unique")
        return self

    @property
    def is_reconciled(self) -> bool:
        return self.execution_epoch == self.reconciled_epoch


class AccountSnapshot(FrozenModel):
    equity: Decimal
    buying_power: Decimal
    cash: Decimal
    portfolio_value: Decimal
    trading_blocked: bool
    options_trading_level: int
    options_buying_power: Decimal | None = None
    last_equity: Decimal | None = None


class MarketClockSnapshot(FrozenModel):
    is_open: bool
    timestamp: datetime
    next_open: datetime
    next_close: datetime


class PositionSnapshot(FrozenModel):
    symbol: str
    asset_class: str
    quantity: Decimal
    market_value: Decimal
    unrealized_pl: Decimal


class OpenOrderSnapshot(FrozenModel):
    client_order_id: str
    symbol: str
    status: str
    side: str
    quantity: Decimal | None


class ReconciliationSnapshot(FrozenModel):
    account: AccountSnapshot
    clock: MarketClockSnapshot
    positions: tuple[PositionSnapshot, ...]
    open_orders: tuple[OpenOrderSnapshot, ...]
    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class QuoteSnapshot(FrozenModel):
    symbol: str
    bid_price: Decimal = Field(gt=0)
    ask_price: Decimal = Field(gt=0)
    timestamp: datetime
    feed: str

    @model_validator(mode="after")
    def validate_market(self) -> QuoteSnapshot:
        if self.timestamp.tzinfo is None:
            raise ValueError("quote timestamp must be timezone-aware")
        if self.ask_price < self.bid_price:
            raise ValueError("ask price must not be below bid price")
        return self

    @property
    def spread_bps(self) -> Decimal:
        midpoint = (self.ask_price + self.bid_price) / Decimal("2")
        return (self.ask_price - self.bid_price) / midpoint * Decimal("10000")


class NewsArticle(FrozenModel):
    source_id: str = Field(min_length=1)
    headline: str = Field(min_length=1, max_length=500)
    summary: str = Field(max_length=5_000)
    content: str = Field(max_length=50_000)
    source: str = Field(min_length=1)
    author: str
    url: str | None = None
    published_at: datetime
    updated_at: datetime
    symbols: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_article(self) -> NewsArticle:
        if self.published_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("news timestamps must be timezone-aware")
        if self.updated_at < self.published_at:
            raise ValueError("news update must not precede publication")
        if any(not symbol or symbol != symbol.upper() for symbol in self.symbols):
            raise ValueError("news symbols must be nonempty uppercase values")
        if len(self.symbols) != len(set(self.symbols)):
            raise ValueError("news symbols must be unique")
        return self


class Event(FrozenModel):
    source_id: str = Field(min_length=1)
    published_at: datetime
    analyzed_at: datetime
    event_type: EventType
    direction: EventDirection
    magnitude: Decimal = Field(ge=0, le=1)
    novelty: Decimal = Field(ge=0, le=1)
    surprise: Decimal = Field(ge=0, le=1)
    confidence: Decimal = Field(ge=0, le=1)
    expected_horizon_minutes: int = Field(gt=0, le=10_080)
    affected_symbols: tuple[str, ...] = Field(min_length=1)
    summary: str = Field(min_length=1, max_length=500)
    invalidating_evidence: tuple[str, ...]
    model_id: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    request_id: str | None = None
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_event(self) -> Event:
        if self.published_at.tzinfo is None or self.analyzed_at.tzinfo is None:
            raise ValueError("event timestamps must be timezone-aware")
        if self.analyzed_at < self.published_at:
            raise ValueError("event analysis must not precede publication")
        if any(not symbol or symbol != symbol.upper() for symbol in self.affected_symbols):
            raise ValueError("affected symbols must be nonempty uppercase values")
        if len(self.affected_symbols) != len(set(self.affected_symbols)):
            raise ValueError("affected symbols must be unique")
        return self


class SignalFrame(FrozenModel):
    symbol: str
    observed_at: datetime
    expected_horizon_minutes: int = Field(gt=0)
    has_credible_event: bool
    event_confidence: Decimal = Field(ge=0, le=1)
    event_novelty: Decimal = Field(ge=0, le=1)
    event_direction: Decimal = Field(ge=-1, le=1)
    momentum_score: Decimal = Field(ge=-1, le=1)
    reversion_score: Decimal = Field(ge=0, le=1)
    regime_score: Decimal = Field(ge=0, le=1)
    spread_bps: Decimal = Field(ge=0)
    expected_edge_bps: Decimal
    estimated_cost_bps: Decimal = Field(ge=0)
    data_quality_passed: bool
    exposure_group: str


class RouteDecision(FrozenModel):
    route: Route
    symbol: str
    confidence: Decimal = Field(ge=0, le=1)
    reasons: tuple[str, ...]
    policy_profile: str
    observed_at: datetime


class TradeIntent(FrozenModel):
    intent_id: str
    route: Route
    symbol: str
    instrument_type: InstrumentType
    side: Side
    confidence: Decimal = Field(ge=0, le=1)
    entry_price: Decimal = Field(gt=0)
    stop_price: Decimal = Field(gt=0)
    expected_horizon_minutes: int = Field(gt=0)
    exposure_group: str
    quote_age_seconds: Decimal = Field(ge=0)
    data_quality_passed: bool
    contract_multiplier: int = Field(default=1, gt=0)
    underlying_symbol: str | None = None
    option_type: OptionType | None = None
    option_expiration_date: date | None = None
    take_profit_price: Decimal | None = Field(default=None, gt=0)
    universe_id: str | None = None

    @model_validator(mode="after")
    def validate_contract_multiplier(self) -> TradeIntent:
        expected = 100 if self.instrument_type is InstrumentType.OPTION else 1
        if self.contract_multiplier != expected:
            raise ValueError(f"{self.instrument_type} contract multiplier must be {expected}")
        if self.instrument_type is InstrumentType.OPTION and (
            self.underlying_symbol is None
            or self.option_type is None
            or self.option_expiration_date is None
            or self.take_profit_price is None
        ):
            raise ValueError("long option intents require complete contract and exit metadata")
        return self

    @property
    def stop_distance(self) -> Decimal:
        return abs(self.entry_price - self.stop_price)

    @property
    def stop_risk_per_unit(self) -> Decimal:
        if self.instrument_type is InstrumentType.OPTION:
            return self.entry_price * self.contract_multiplier
        return self.stop_distance * self.contract_multiplier

    @property
    def entry_cost_per_unit(self) -> Decimal:
        return self.entry_price * self.contract_multiplier


class PortfolioRiskState(FrozenModel):
    equity: Decimal = Field(gt=0)
    buying_power: Decimal = Field(ge=0)
    position_count: int = Field(ge=0)
    total_open_risk: Decimal = Field(ge=0)
    overnight_open_risk: Decimal = Field(ge=0)
    group_open_risk: dict[str, Decimal] = Field(default_factory=dict)
    daily_pnl: Decimal
    competition_drawdown: Decimal = Field(ge=0)


class RiskDecision(FrozenModel):
    status: RiskDecisionStatus
    intent_id: str
    quantity: int = Field(ge=0)
    risk_amount: Decimal = Field(ge=0)
    checks: tuple[str, ...]
    decided_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class OrderPlan(FrozenModel):
    client_order_id: str = Field(min_length=1, max_length=48)
    intent_id: str
    symbol: str
    side: Side
    quantity: int = Field(gt=0)
    limit_price: Decimal = Field(gt=0)
    stop_price: Decimal = Field(gt=0)
    take_profit_price: Decimal = Field(gt=0)
    risk_amount: Decimal = Field(gt=0)
    exposure_group: str
    instrument_type: InstrumentType = InstrumentType.EQUITY
    underlying_symbol: str | None = None
    option_type: OptionType | None = None
    option_expiration_date: date | None = None
    universe_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime

    @model_validator(mode="after")
    def validate_bracket(self) -> OrderPlan:
        if self.side is Side.BUY and not (
            self.stop_price < self.limit_price < self.take_profit_price
        ):
            raise ValueError("long bracket prices must be stop < entry < take profit")
        if self.side is Side.SELL and not (
            self.take_profit_price < self.limit_price < self.stop_price
        ):
            raise ValueError("short bracket prices must be take profit < entry < stop")
        if self.expires_at.tzinfo is None or self.expires_at <= self.created_at:
            raise ValueError("order plan expiry must be timezone-aware and after creation")
        if self.instrument_type is InstrumentType.OPTION and (
            self.side is not Side.BUY
            or self.underlying_symbol is None
            or self.option_type is None
            or self.option_expiration_date is None
        ):
            raise ValueError("long option plans require complete contract metadata")
        return self


class OrderExecution(FrozenModel):
    plan: OrderPlan
    request_hash: str = Field(min_length=64, max_length=64)
    status: OrderExecutionStatus = OrderExecutionStatus.PREPARED
    version: int = Field(default=0, ge=0)
    alpaca_order_id: str | None = None
    broker_status: str | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class BrokerOrderSnapshot(FrozenModel):
    order_id: str
    client_order_id: str
    symbol: str
    side: Side
    quantity: int = Field(gt=0)
    status: str
    has_active_take_profit: bool = False
    has_active_stop_loss: bool = False


class PublicDecisionRecord(FrozenModel):
    decision_id: str
    decision_type: str
    occurred_at: datetime
    route: Route | None = None
    symbol: str | None = None
    summary: str


class PublicDecisionPage(FrozenModel):
    records: list[PublicDecisionRecord]
    next_cursor: str | None = None


class PublicPortfolioPoint(FrozenModel):
    captured_at: datetime
    equity: Decimal
    cash: Decimal
    net_pnl: Decimal
    daily_return: Decimal
    competition_return: Decimal
    drawdown: Decimal = Field(ge=0)
    position_count: int = Field(ge=0)
    max_trade_risk_rate: Decimal = Field(ge=0)
    total_open_risk_rate: Decimal = Field(ge=0)
    overnight_open_risk_rate: Decimal = Field(ge=0)
    max_group_open_risk_rate: Decimal = Field(ge=0)


class DecisionRecord(PublicDecisionRecord):
    decision_id: str
    decision_type: str
    occurred_at: datetime
    route: Route | None = None
    symbol: str | None = None
    summary: str
    payload: dict[str, Any] = Field(default_factory=dict)
    public: bool = False
    public_summary: str | None = None

    @model_validator(mode="after")
    def validate_public_summary(self) -> DecisionRecord:
        if self.public and not self.public_summary:
            raise ValueError("public decisions require an explicit sanitized public_summary")
        return self

    def public_projection(self) -> PublicDecisionRecord:
        if not self.public or self.public_summary is None:
            raise ValueError("private decisions do not have public projections")
        return PublicDecisionRecord(
            decision_id=self.decision_id,
            decision_type=self.decision_type,
            occurred_at=self.occurred_at,
            route=self.route,
            symbol=self.symbol,
            summary=self.public_summary,
        )

    def public_projection_json(self) -> str:
        return self.public_projection().model_dump_json()

    @classmethod
    def create(
        cls,
        *,
        decision_id: str | None = None,
        decision_type: str,
        summary: str,
        route: Route | None = None,
        symbol: str | None = None,
        payload: dict[str, Any] | None = None,
        public: bool = False,
        public_summary: str | None = None,
        occurred_at: datetime | None = None,
    ) -> DecisionRecord:
        return cls(
            decision_id=decision_id or str(uuid4()),
            decision_type=decision_type,
            occurred_at=occurred_at or datetime.now(UTC),
            route=route,
            symbol=symbol,
            summary=summary,
            payload=payload or {},
            public=public,
            public_summary=public_summary,
        )
