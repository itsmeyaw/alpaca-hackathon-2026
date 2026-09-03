from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from pydantic import Field

from catalyst_router.domain import DecisionRecord, FrozenModel

UNIVERSE_POLICY_VERSION = "universe-v1"


class UniverseUnavailable(RuntimeError):
    pass


class UniverseCandidate(FrozenModel):
    symbol: str
    most_active_rank: int | None = Field(default=None, gt=0)
    gainer_rank: int | None = Field(default=None, gt=0)
    loser_rank: int | None = Field(default=None, gt=0)
    price: Decimal = Field(gt=0)
    prior_day_dollar_volume: Decimal = Field(ge=0)
    spread_bps: Decimal = Field(ge=0)
    active: bool
    tradable: bool
    options_enabled: bool


class UniverseSnapshot(FrozenModel):
    universe_id: str
    policy_version: str
    session_date: date
    selected_at: datetime
    symbols: tuple[str, ...]
    source_ranks: dict[str, dict[str, int]]
    rejections: dict[str, tuple[str, ...]]
    candidate_evidence: dict[str, UniverseCandidate] = Field(default_factory=dict)


class UniverseSelector:
    MIN_PRICE = Decimal("5")
    MIN_DOLLAR_VOLUME = Decimal("50000000")
    MAX_SPREAD_BPS = Decimal("15")

    def __init__(self, *, target_size: int = 20) -> None:
        if target_size < 1:
            raise ValueError("universe target size must be positive")
        self._target_size = target_size

    def select(
        self,
        *,
        session_date: date,
        selected_at: datetime,
        candidates: tuple[UniverseCandidate, ...],
    ) -> UniverseSnapshot:
        if selected_at.tzinfo is None:
            raise ValueError("universe selection time must be timezone-aware")
        merged = self._merge(candidates)
        accepted: list[tuple[float, str, UniverseCandidate]] = []
        rejections: dict[str, tuple[str, ...]] = {}
        source_ranks: dict[str, dict[str, int]] = {}
        for symbol, item in sorted(merged.items()):
            reasons = self._rejection_reasons(item)
            if reasons:
                rejections[symbol] = reasons
                continue
            ranks = {
                source: rank
                for source, rank in (
                    ("most_active", item.most_active_rank),
                    ("gainer", item.gainer_rank),
                    ("loser", item.loser_rank),
                )
                if rank is not None
            }
            source_ranks[symbol] = ranks
            score = sum(1 / (60 + rank) for rank in ranks.values())
            accepted.append((score, symbol, item))
        accepted.sort(key=lambda value: (-value[0], value[1]))
        symbols = tuple(symbol for _, symbol, _ in accepted[: self._target_size])
        selected_ranks = {symbol: source_ranks[symbol] for symbol in symbols}
        return UniverseSnapshot(
            universe_id=f"{UNIVERSE_POLICY_VERSION}:{session_date.isoformat()}",
            policy_version=UNIVERSE_POLICY_VERSION,
            session_date=session_date,
            selected_at=selected_at,
            symbols=symbols,
            source_ranks=selected_ranks,
            rejections=rejections,
            candidate_evidence=merged,
        )

    @staticmethod
    def _merge(candidates: tuple[UniverseCandidate, ...]) -> dict[str, UniverseCandidate]:
        merged: dict[str, UniverseCandidate] = {}
        for item in candidates:
            symbol = item.symbol.upper()
            current = merged.get(symbol)
            if current is None:
                merged[symbol] = item.model_copy(update={"symbol": symbol})
                continue
            merged[symbol] = current.model_copy(
                update={
                    "most_active_rank": _minimum_rank(
                        current.most_active_rank, item.most_active_rank
                    ),
                    "gainer_rank": _minimum_rank(current.gainer_rank, item.gainer_rank),
                    "loser_rank": _minimum_rank(current.loser_rank, item.loser_rank),
                }
            )
        return merged

    def _rejection_reasons(self, item: UniverseCandidate) -> tuple[str, ...]:
        reasons = []
        if not item.active:
            reasons.append("asset is not active")
        if not item.tradable:
            reasons.append("asset is not tradable")
        if not item.options_enabled:
            reasons.append("options are not enabled")
        if item.price < self.MIN_PRICE:
            reasons.append("price below $5")
        if item.prior_day_dollar_volume < self.MIN_DOLLAR_VOLUME:
            reasons.append("prior-day dollar volume below $50M")
        if item.spread_bps > self.MAX_SPREAD_BPS:
            reasons.append("underlying spread exceeds 15 bps")
        return tuple(reasons)


class UniverseSource(Protocol):
    def session_date(self) -> date: ...

    def build(self, session_date: date) -> UniverseSnapshot: ...


class UniverseStore(Protocol):
    def get_daily_universe(self, session_date: date) -> UniverseSnapshot | None: ...

    def put_daily_universe(self, snapshot: UniverseSnapshot, *, expected_epoch: str) -> bool: ...

    def append_decision_once(
        self, record: DecisionRecord, *, expected_epoch: str | None = None
    ) -> bool: ...


class DailyUniverse:
    """Returns one immutable point-in-time universe for each Alpaca session."""

    def __init__(self, *, store: UniverseStore, source: UniverseSource) -> None:
        self._store = store
        self._source = source

    def current(self, *, expected_epoch: str) -> UniverseSnapshot:
        session_date = self._source.session_date()
        existing = self._store.get_daily_universe(session_date)
        if existing is not None:
            return existing
        proposed = self._source.build(session_date)
        if self._store.put_daily_universe(proposed, expected_epoch=expected_epoch):
            self._store.append_decision_once(
                DecisionRecord.create(
                    decision_id=proposed.universe_id,
                    decision_type="UNIVERSE_SELECTED",
                    occurred_at=proposed.selected_at,
                    summary=(
                        f"selected {len(proposed.symbols)} option-enabled underlyings "
                        f"for {proposed.session_date}"
                    ),
                    payload=proposed.model_dump(mode="json"),
                    public=True,
                    public_summary=f"Selected {len(proposed.symbols)} liquid underlyings",
                ),
                expected_epoch=expected_epoch,
            )
            return proposed
        winner = self._store.get_daily_universe(proposed.session_date)
        if winner is None:
            raise RuntimeError("daily universe was claimed but could not be loaded")
        return winner


def _minimum_rank(left: int | None, right: int | None) -> int | None:
    if left is None:
        return right
    if right is None:
        return left
    return min(left, right)
