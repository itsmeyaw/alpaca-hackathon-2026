from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, cast

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict

from catalyst_router.container import Container
from catalyst_router.domain import AgentMode, PublicDecisionRecord
from catalyst_router.settings import Settings

logger = logging.getLogger(__name__)


class PublicStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: AgentMode
    reconciled: bool


def get_container(request: Request) -> Container:
    return cast(Container, request.app.state.container)


ContainerDependency = Annotated[Container, Depends(get_container)]


def create_app(container: Container | None = None) -> FastAPI:
    app_container = container or Container.build(Settings.from_env())

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if app_container.settings.auto_reconcile and app_container.broker is not None:
            try:
                await asyncio.to_thread(app_container.reconciliation_service().reconcile)
            except Exception:
                logger.exception("startup reconciliation failed; agent remains fenced")
        else:
            app_container.store.begin_execution()
        yield

    app = FastAPI(title="Catalyst Router", version="0.1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(app_container.settings.cors_origins),
        allow_methods=["GET"],
        allow_headers=["Accept"],
    )
    app.state.container = app_container

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    def ready(container: ContainerDependency) -> dict[str, str]:
        if not container.store.get_agent_state().is_reconciled:
            raise HTTPException(status_code=503, detail="current execution epoch is unreconciled")
        return {"status": "ready"}

    @app.get("/api/public/status", response_model=PublicStatus)
    def public_status(container: ContainerDependency) -> PublicStatus:
        state = container.store.get_agent_state()
        return PublicStatus(
            mode=state.mode,
            reconciled=state.is_reconciled,
        )

    @app.get("/api/public/decisions", response_model=list[PublicDecisionRecord])
    def public_decisions(
        container: ContainerDependency,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> list[PublicDecisionRecord]:
        return container.store.list_public_decisions(limit)

    @app.post("/api/operator/reconcile", include_in_schema=False)
    def operator_reconcile() -> None:
        raise HTTPException(status_code=404)

    return app


app = create_app()
