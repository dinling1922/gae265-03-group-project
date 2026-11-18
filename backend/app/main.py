import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.routing import APIRoute
from sqlmodel.ext.asyncio.session import AsyncSession
from starlette.middleware.sessions import SessionMiddleware
from starlette.routing import Route

from app.api.main import api_router
from app.core.auth import SESSION_MAX_AGE, session_secret
from app.core.config import settings
from app.core.db import engine
from app.ingest.agent import fail_stale_jobs
from app.line_bot.webhook import build_route as build_line_route
from app.mcp_server.endpoint import MCP_PATH, build_endpoint

# app 自己的 INFO log（例如方案問答改寫後的問題與 Jev 的判斷）要出現在 server log；
# 沒設定時 Python 只印 WARNING 以上
_app_logger = logging.getLogger("app")
_app_logger.setLevel(logging.INFO)
_app_logger.addHandler(logging.StreamHandler())


def _operation_id_from_route_name(route: APIRoute) -> str:
    """operationId 用路由函式名：前端由 OpenAPI 產生 client，產生出來的函式以它命名，所以路由函式不可同名。"""
    return route.name


mcp_endpoint = build_endpoint(
    lambda: AsyncSession(engine, expire_on_commit=False),
    settings.MCP_RATE_LIMIT_PER_MINUTE,
)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
    # 啟動即檢查 LLM 設定，缺必填項時伺服器起不來並印出缺哪些變數
    settings.check_llm_provider()
    settings.check_auth_settings()
    # 重啟會中斷背景工作，所以所有環境都把殘留的「處理中」標成失敗
    await fail_stale_jobs(engine)
    async with mcp_endpoint.run():
        yield


app = FastAPI(
    title=settings.PROJECT_NAME,
    lifespan=lifespan,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    generate_unique_id_function=_operation_id_from_route_name,
)
# 簽章的 httpOnly cookie 當 session（ADR-0008）；SameSite=Lax 讓跨站的 POST 帶不到 cookie
app.add_middleware(
    SessionMiddleware,
    secret_key=session_secret(),
    max_age=SESSION_MAX_AGE,
    same_site="lax",
    https_only=settings.ENVIRONMENT == "production",
)
app.include_router(api_router, prefix=settings.API_V1_STR)
# MCP 伺服器（ADR-0032）：只收 Bearer，自己驗證，不經過 cookie session 的依賴、不開 CORS
app.router.routes.append(Route(MCP_PATH, mcp_endpoint.asgi))
# LINE bot 轉接層（ADR-0033）：給 LINE 平台打的 webhook，自己驗簽，不進 OpenAPI；沒設定頻道時回 404
app.router.routes.append(
    build_line_route(lambda: AsyncSession(engine, expire_on_commit=False))
)
