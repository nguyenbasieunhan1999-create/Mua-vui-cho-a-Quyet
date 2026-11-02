"""Điểm vào FastAPI. Chạy local: uv run uvicorn app.main:app --reload --app-dir backend/src"""

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import __version__
from app.api import admin_auth, admin_games, admin_rules, guests, history, public_rules, rooms
from app.config import Settings, get_settings
from app.db import make_engine, make_sessionmaker
from app.errors import install_error_handlers
from app.hardening import check_production_settings, configure_logging, install_hardening
from app.realtime import ws
from app.realtime.chat import ChatCooldown
from app.realtime.games import GameManager
from app.realtime.hub import RoomHub
from app.realtime.sweeper import run_sweeper
from app.security import SlidingWindowLimiter
from app.services.admin_auth import create_admin
from app.services.rules import ensure_seeded
from tlmn_engine import __version__ as engine_version
from tlmn_engine.catalog import excel_ids

log = logging.getLogger(__name__)
STATIC_DIR = Path(os.environ.get("STATIC_DIR", "/app/static"))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    try:
        async with app.state.sessionmaker() as session:
            if await ensure_seeded(session):
                log.info("Đã tạo bộ luật v1 từ cấu hình mặc định")
            if settings.admin_password and len(settings.admin_password) >= 12:
                with contextlib.suppress(ValueError):
                    await create_admin(session, settings.admin_username, settings.admin_password)
                    await session.commit()
                    log.info("Đã khởi tạo tài khoản Admin '%s'", settings.admin_username)
    except (SQLAlchemyError, OSError):
        log.exception("Không kết nối được DB khi khởi động; sẽ thử lại ở request sau")
    try:
        recovered, failed = await app.state.games.recover_on_startup()
        if recovered or failed:
            log.warning("Khôi phục %d ván dang dở; %d ván không khôi phục được (đã hủy)", recovered, failed)
    except (SQLAlchemyError, OSError):
        log.exception("Không khôi phục được ván dang dở khi khởi động")
    sweeper = asyncio.create_task(
        run_sweeper(
            app.state.sessionmaker, app.state.hub, settings.seat_sweep_interval_seconds, app.state.games
        )
    )
    yield
    await app.state.games.shutdown()
    sweeper.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await sweeper
    await app.state.engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    check_production_settings(settings)
    configure_logging(settings)
    app = FastAPI(
        title="Tiến Lên Miền Nam",
        version=__version__,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = make_engine(settings.database_url, settings.db_pool_size, settings.db_max_overflow)
    app.state.sessionmaker = make_sessionmaker(app.state.engine)
    app.state.login_limiter = SlidingWindowLimiter(settings.login_rate_limit_per_minute, 60)
    app.state.room_create_limiter = SlidingWindowLimiter(settings.room_create_per_hour, 3600)
    app.state.room_lookup_limiter = SlidingWindowLimiter(settings.room_lookup_per_minute, 60)
    app.state.chat_cooldown = ChatCooldown()
    app.state.hub = RoomHub(app.state.sessionmaker)
    app.state.games = GameManager(app.state.sessionmaker, app.state.hub)
    install_error_handlers(app)
    install_hardening(app, settings)
    app.include_router(admin_auth.router)
    app.include_router(admin_rules.router)
    app.include_router(admin_games.router)
    app.include_router(public_rules.router)
    app.include_router(guests.router)
    app.include_router(rooms.router)
    app.include_router(history.router)
    app.include_router(ws.router)

    @app.get("/api/health")
    async def health() -> dict[str, object]:
        try:
            async with app.state.sessionmaker() as session:
                await session.execute(text("SELECT 1"))
            db = "ok"
        except (SQLAlchemyError, OSError):
            db = "unavailable"
        return {
            "status": "ok" if db == "ok" else "degraded",
            "db": db,
            "environment": settings.environment,
            "version": __version__,
            "engineVersion": engine_version,
            "ruleCatalogSize": len(excel_ids()),
            "liveGames": app.state.games.live_count(),
            "commandLatency": app.state.games.latency_stats(),
        }

    # Phục vụ giao diện React SPA trực tiếp nếu đã có bản build tĩnh (/app/static)
    if STATIC_DIR.is_dir() and (STATIC_DIR / "index.html").is_file():
        assets_dir = STATIC_DIR / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_spa(full_path: str) -> FileResponse:
            candidate = (STATIC_DIR / full_path).resolve()
            if full_path and candidate.is_relative_to(STATIC_DIR.resolve()) and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(STATIC_DIR / "index.html")

    return app


app = create_app()
