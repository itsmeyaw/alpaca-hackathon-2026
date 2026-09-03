from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import Field, model_validator

from catalyst_router.domain import FrozenModel, Side
from catalyst_router.domain import OptionType as OptionType

OPTION_POLICY_VERSION = "long-directional-options-v1"


class OptionCandidate(FrozenModel):
    contract_symbol: str
    underlying_symbol: str
    option_type: OptionType
    expiration_date: date
    strike_price: Decimal = Field(gt=0)
    multiplier: int = Field(gt=0)
    active: bool
    tradable: bool
    delta: Decimal
    bid_price: Decimal = Field(gt=0)
    ask_price: Decimal = Field(gt=0)
    bid_size: int = Field(ge=0)
    ask_size: int = Field(ge=0)
    quote_timestamp: datetime
    feed: str
    open_interest: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_quote(self) -> OptionCandidate:
        if self.quote_timestamp.tzinfo is None:
            raise ValueError("option quote timestamp must be timezone-aware")
        if self.ask_price < self.bid_price:
            raise ValueError("option ask must not be below bid")
        return self

    @property
    def spread_bps(self) -> Decimal:
        midpoint = (self.bid_price + self.ask_price) / Decimal("2")
        return (self.ask_price - self.bid_price) / midpoint * Decimal("10000")


class OptionSelection(FrozenModel):
    policy_version: str = OPTION_POLICY_VERSION
    underlying_symbol: str
    direction: Side
    selected: OptionCandidate | None = None
    rejections: dict[str, tuple[str, ...]] = Field(default_factory=dict)


class OptionContractSelector:
    MIN_DTE = 14
    MAX_DTE = 30
    TARGET_DTE = 21
    MIN_ABS_DELTA = Decimal("0.55")
    MAX_ABS_DELTA = Decimal("0.70")
    TARGET_ABS_DELTA = Decimal("0.625")
    MAX_SPREAD_BPS = Decimal("1000")
    MIN_OPEN_INTEREST = 100
    MAX_QUOTE_AGE_SECONDS = Decimal("5")

    def select(
        self,
        underlying_symbol: str,
        direction: Side,
        candidates: tuple[OptionCandidate, ...],
        *,
        now: datetime,
    ) -> OptionSelection:
        if now.tzinfo is None:
            raise ValueError("option selection time must be timezone-aware")
        desired_type = OptionType.CALL if direction is Side.BUY else OptionType.PUT
        accepted: list[tuple[Decimal, Decimal, int, int, str, OptionCandidate]] = []
        rejections: dict[str, tuple[str, ...]] = {}
        for item in candidates:
            reasons = self._rejection_reasons(
                item,
                underlying_symbol=underlying_symbol,
                desired_type=desired_type,
                now=now,
            )
            if reasons:
                rejections[item.contract_symbol] = reasons
                continue
            dte = (item.expiration_date - now.date()).days
            accepted.append(
                (
                    abs(abs(item.delta) - self.TARGET_ABS_DELTA),
                    item.spread_bps,
                    -item.open_interest,
                    abs(dte - self.TARGET_DTE),
                    item.contract_symbol,
                    item,
                )
            )
        accepted.sort(key=lambda value: value[:-1])
        return OptionSelection(
            underlying_symbol=underlying_symbol,
            direction=direction,
            selected=accepted[0][-1] if accepted else None,
            rejections=rejections,
        )

    def _rejection_reasons(
        self,
        item: OptionCandidate,
        *,
        underlying_symbol: str,
        desired_type: OptionType,
        now: datetime,
    ) -> tuple[str, ...]:
        reasons = []
        dte = (item.expiration_date - now.date()).days
        quote_age = Decimal(str((now - item.quote_timestamp).total_seconds()))
        if item.underlying_symbol != underlying_symbol:
            reasons.append("underlying does not match")
        if item.option_type is not desired_type:
            reasons.append("option type does not match direction")
        if not item.active or not item.tradable:
            reasons.append("contract is not active and tradable")
        if item.multiplier != 100:
            reasons.append("contract multiplier is not 100")
        if not self.MIN_DTE <= dte <= self.MAX_DTE:
            reasons.append("expiration is outside 14-30 DTE")
        if not self.MIN_ABS_DELTA <= abs(item.delta) <= self.MAX_ABS_DELTA:
            reasons.append("absolute delta is outside 0.55-0.70")
        if item.spread_bps > self.MAX_SPREAD_BPS:
            reasons.append("option spread exceeds 10%")
        if item.open_interest < self.MIN_OPEN_INTEREST:
            reasons.append("open interest is below 100")
        if item.bid_size < 1 or item.ask_size < 1:
            reasons.append("option quote size is empty")
        if item.feed != "indicative":
            reasons.append("option quote is not from the indicative feed")
        if not Decimal("0") <= quote_age <= self.MAX_QUOTE_AGE_SECONDS:
            reasons.append("option quote is stale")
        return tuple(reasons)
