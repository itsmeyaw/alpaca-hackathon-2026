from __future__ import annotations

import argparse
import json

import uvicorn
from dotenv import load_dotenv

from catalyst_router.adapters.alpaca import AlpacaPaperBroker
from catalyst_router.container import Container
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


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="catalyst-router")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("check-alpaca", help="read and sanitize Alpaca paper account state")
    subparsers.add_parser("reconcile", help="persist a fenced paper-account reconciliation")
    serve = subparsers.add_parser("serve", help="run the local API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.command == "check-alpaca":
        return _check_alpaca(Settings.from_env())
    if args.command == "reconcile":
        return _reconcile(Settings.from_env())
    if args.command == "serve":
        uvicorn.run("catalyst_router.api:app", host=args.host, port=args.port, reload=False)
        return 0
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
