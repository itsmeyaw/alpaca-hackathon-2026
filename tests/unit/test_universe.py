from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from catalyst_router.adapters.memory import InMemoryOperationalStore
from catalyst_router.domain import DecisionRecord
from catalyst_router.universe import UniverseCandidate, UniverseSelector


def candidate(
    symbol: str,
    *,
    most_active_rank: int | None = None,
    gainer_rank: int | None = None,
    loser_rank: int | None = None,
    price: str = "100",
    dollar_volume: str = "100000000",
    spread_bps: str = "5",
    options_enabled: bool = True,
) -> UniverseCandidate:
    return UniverseCandidate(
        symbol=symbol,
        most_active_rank=most_active_rank,
        gainer_rank=gainer_rank,
        loser_rank=loser_rank,
        price=Decimal(price),
        prior_day_dollar_volume=Decimal(dollar_volume),
        spread_bps=Decimal(spread_bps),
        active=True,
        tradable=True,
        options_enabled=options_enabled,
    )


def test_ranked_union_rewards_names_confirmed_by_multiple_sources() -> None:
    selected_at = datetime(2026, 9, 3, 12, tzinfo=UTC)
    result = UniverseSelector(target_size=2).select(
        session_date=date(2026, 9, 3),
        selected_at=selected_at,
        candidates=(
            candidate("AAPL", most_active_rank=1),
            candidate("NVDA", most_active_rank=2, gainer_rank=2),
            candidate("TSLA", gainer_rank=1),
        ),
    )

    assert result.symbols == ("NVDA", "AAPL")
    assert result.session_date == date(2026, 9, 3)
    assert result.selected_at == selected_at
    assert result.universe_id == "universe-v1:2026-09-03"


def test_universe_fails_closed_on_underlying_quality_gates() -> None:
    result = UniverseSelector(target_size=10).select(
        session_date=date(2026, 9, 3),
        selected_at=datetime(2026, 9, 3, 12, tzinfo=UTC),
        candidates=(
            candidate("GOOD", most_active_rank=1),
            candidate("CHEAP", most_active_rank=2, price="4.99"),
            candidate("THIN", most_active_rank=3, dollar_volume="49999999"),
            candidate("WIDE", most_active_rank=4, spread_bps="15.01"),
            candidate("NOOPT", most_active_rank=5, options_enabled=False),
        ),
    )

    assert result.symbols == ("GOOD",)
    assert result.rejections == {
        "CHEAP": ("price below $5",),
        "NOOPT": ("options are not enabled",),
        "THIN": ("prior-day dollar volume below $50M",),
        "WIDE": ("underlying spread exceeds 15 bps",),
    }


def test_daily_universe_is_immutable_and_epoch_fenced() -> None:
    store = InMemoryOperationalStore()
    started = store.begin_execution()
    store.commit_reconciliation(
        started.execution_epoch,
        DecisionRecord.create(decision_type="RECONCILIATION_COMPLETED", summary="ok"),
    )
    snapshot = UniverseSelector(target_size=1).select(
        session_date=date(2026, 9, 3),
        selected_at=datetime(2026, 9, 3, 12, tzinfo=UTC),
        candidates=(candidate("AAPL", most_active_rank=1),),
    )

    assert store.put_daily_universe(snapshot, expected_epoch=started.execution_epoch)
    assert not store.put_daily_universe(snapshot, expected_epoch=started.execution_epoch)
    assert store.get_daily_universe(date(2026, 9, 3)) == snapshot
    store.begin_execution()

    with pytest.raises(RuntimeError, match="lost execution epoch"):
        store.put_daily_universe(
            snapshot.model_copy(update={"session_date": date(2026, 9, 4)}),
            expected_epoch=started.execution_epoch,
        )
