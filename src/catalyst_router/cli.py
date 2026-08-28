from __future__ import annotations

import argparse
import json
import logging

import uvicorn
from dotenv import load_dotenv

from catalyst_router.adapters.alpaca import AlpacaPaperBroker
from catalyst_router.container import Container
from catalyst_router.domain import AgentMode, DecisionRecord
from catalyst_router.service import ReconciliationService
from catalyst_router.settings import Settings


def _check_alpaca(settings: Settings) -> int:
    key, secret = settings.require_alpaca()
    snapshot = AlpacaPaperBroker(key, secret).reconciliation_snapshot()
    print(
        json.dumps(
            {
                "paper": True,
                "market_is_open": snapshot.clock.is_open,
                "equity": str(snapshot.account.equity),
                "buying_power": str(snapshot.account.buying_power),
                "trading_blocked": snapshot.account.trading_blocked,
                "options_trading_level": snapshot.account.options_trading_level,
                "position_count": len(snapshot.positions),
                "open_order_count": len(snapshot.open_orders),
            },
            indent=2,
        )
    )
    return 0


def _reconcile(settings: Settings) -> int:
    container = Container.build(settings)
    snapshot = container.reconciliation_service().reconcile()
    state = container.store.get_agent_state()
    print(
        json.dumps(
            {
                "paper": True,
                "mode": state.mode,
                "reconciled": state.is_reconciled,
                "market_is_open": snapshot.clock.is_open,
                "position_count": len(snapshot.positions),
                "open_order_count": len(snapshot.open_orders),
            },
            indent=2,
        )
    )
    return 0


def _worker(settings: Settings) -> int:
    if settings.runtime_role != "worker":
        raise RuntimeError("worker command requires RUNTIME_ROLE=worker")
    container = Container.build(settings)
    container.trading_worker().run_forever(
        reconcile=container.reconciliation_service().reconcile,
        poll_seconds=settings.worker_poll_seconds,
    )
    return 0


def _set_mode(settings: Settings, mode: AgentMode, reason: str, *, flatten: bool = False) -> int:
    container = Container.build(settings)
    if (flatten or mode is AgentMode.KILLED) and container.broker is None:
        raise RuntimeError("flattening controls require Alpaca credentials")
    if mode is AgentMode.RUNNING:
        if container.broker is None:
            raise RuntimeError("resume requires Alpaca credentials")
        snapshot = container.broker.reconciliation_snapshot()
        ReconciliationService.validate_snapshot(snapshot)
        if snapshot.positions or snapshot.open_orders:
            raise RuntimeError("first-slice resume requires no positions or open orders")
        state = container.store.get_agent_state()
        if state.active_order_id is not None:
            execution = container.store.get_order(state.active_order_id)
            if execution is None:
                raise RuntimeError("active order has no durable execution record")
            broker_order = container.broker.get_order_by_client_id(state.active_order_id)
            status = (
                broker_order.status.rsplit(".", 1)[-1].lower()
                if broker_order is not None
                else "missing"
            )
            terminal = status in {
                "canceled",
                "expired",
                "filled",
                "rejected",
                "replaced",
            }
            expired_and_missing = (
                status == "missing" and snapshot.clock.timestamp >= execution.plan.expires_at
            )
            if not terminal and not expired_and_missing:
                raise RuntimeError("active order is not terminal; refusing resume")
            container.store.clear_active_order(state.active_order_id)
        if not container.store.get_agent_state().is_reconciled:
            container.reconciliation_service().reconcile()
    record = DecisionRecord.create(
        decision_type="OPERATOR_ACTION",
        summary=f"operator changed mode to {mode}: {reason}",
        payload={"mode": mode, "reason": reason},
    )
    state = container.store.transition_agent_mode(mode, reason=reason, record=record)
    if flatten or mode is AgentMode.KILLED:
        assert container.broker is not None
        container.broker.flatten()
    print(
        json.dumps(
            {
                "paper": True,
                "mode": state.mode,
                "reconciled": state.is_reconciled,
                "reason": state.reason,
            },
            indent=2,
        )
    )
    return 0


def main() -> int:
    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(prog="catalyst-router")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check-alpaca", help="read and sanitize Alpaca paper account state")
    subparsers.add_parser("reconcile", help="persist a fenced paper-account reconciliation")
    subparsers.add_parser("worker", help="run the always-on shadow inference worker")
    for command in ("resume", "pause", "flatten", "kill"):
        control = subparsers.add_parser(command, help=f"{command} paper execution")
        control.add_argument("--reason", required=True)
    serve = subparsers.add_parser("serve", help="run the local API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.command == "check-alpaca":
        return _check_alpaca(Settings.from_env())
    if args.command == "reconcile":
        return _reconcile(Settings.from_env())
    if args.command == "worker":
        return _worker(Settings.from_env())
    if args.command == "resume":
        return _set_mode(Settings.from_env(), AgentMode.RUNNING, args.reason)
    if args.command == "pause":
        return _set_mode(Settings.from_env(), AgentMode.PAUSED, args.reason)
    if args.command == "flatten":
        return _set_mode(Settings.from_env(), AgentMode.PAUSED, args.reason, flatten=True)
    if args.command == "kill":
        return _set_mode(Settings.from_env(), AgentMode.KILLED, args.reason)
    if args.command == "serve":
        uvicorn.run("catalyst_router.api:app", host=args.host, port=args.port, reload=False)
        return 0
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
