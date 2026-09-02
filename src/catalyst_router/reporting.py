from __future__ import annotations

import logging
from collections.abc import Iterable
from decimal import Decimal

from catalyst_router.domain import (
    AgentState,
    OrderExecution,
    PublicPortfolioPoint,
    ReconciliationSnapshot,
)
from catalyst_router.ports import OperationalStore

logger = logging.getLogger(__name__)


def build_public_portfolio_point(
    snapshot: ReconciliationSnapshot,
    state: AgentState,
    executions: Iterable[OrderExecution] = (),
) -> PublicPortfolioPoint:
    equity = snapshot.account.equity
    baseline = state.competition_start_equity or state.equity_peak or equity
    last_equity = snapshot.account.last_equity or equity
    equity_peak = state.equity_peak or equity
    plans = tuple(execution.plan for execution in executions)
    risks = [plan.risk_amount for plan in plans]
    groups: dict[str, Decimal] = {}
    for plan in plans:
        groups[plan.exposure_group] = (
            groups.get(plan.exposure_group, Decimal("0")) + plan.risk_amount
        )

    return PublicPortfolioPoint(
        captured_at=snapshot.captured_at,
        equity=equity,
        cash=snapshot.account.cash,
        net_pnl=equity - baseline,
        daily_return=(equity - last_equity) / last_equity,
        competition_return=(equity - baseline) / baseline,
        drawdown=max(Decimal("0"), (equity_peak - equity) / equity_peak),
        position_count=max(len(snapshot.positions), len(risks)),
        max_trade_risk_rate=max(risks, default=Decimal("0")) / equity,
        total_open_risk_rate=sum(risks, Decimal("0")) / equity,
        overnight_open_risk_rate=(
            sum(risks, Decimal("0")) / equity if not snapshot.clock.is_open else Decimal("0")
        ),
        max_group_open_risk_rate=max(groups.values(), default=Decimal("0")) / equity,
    )


def publish_public_portfolio(
    store: OperationalStore,
    point: PublicPortfolioPoint,
    *,
    expected_epoch: str,
) -> None:
    try:
        store.append_public_portfolio(point, expected_epoch=expected_epoch)
    except RuntimeError:
        raise
    except Exception:
        logger.exception("public portfolio projection failed; trading remains enabled")
