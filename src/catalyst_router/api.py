from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, cast

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict

from catalyst_router.challenger import PublicChallengerStatus
from catalyst_router.container import Container
from catalyst_router.domain import AgentMode, PublicDecisionPage, PublicDecisionRecord, Route
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
        if (
            app_container.settings.runtime_role != "reporting"
            and app_container.settings.auto_reconcile
            and app_container.broker is not None
        ):
            try:
                await asyncio.to_thread(app_container.reconciliation_service().reconcile)
            except Exception:
                logger.exception("startup reconciliation failed; agent remains fenced")
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

    @app.get("/api/public/decision-pages", response_model=PublicDecisionPage)
    def public_decision_pages(
        container: ContainerDependency,
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
        cursor: str | None = None,
        search: Annotated[str | None, Query(max_length=100)] = None,
        route: Route | None = None,
        decision_type: Annotated[str | None, Query(max_length=100)] = None,
    ) -> PublicDecisionPage:
        try:
            return container.store.list_public_decision_page(
                limit=limit,
                cursor=cursor,
                search=search,
                route=route,
                decision_type=decision_type,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail="invalid decision page cursor") from error

    @app.get("/api/public/challenger", response_model=PublicChallengerStatus)
    def public_challenger(container: ContainerDependency) -> PublicChallengerStatus:
        if container.challenger is None:
            return PublicChallengerStatus.not_deployed()
        return container.challenger.status

    @app.post("/api/operator/reconcile", include_in_schema=False)
    def operator_reconcile() -> None:
        raise HTTPException(status_code=404)

    return app


app = create_app()
