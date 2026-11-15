import os

# 保護 1：在 import app 之前切到測試環境，settings 會指向 POSTGRES_TEST_DB
os.environ["ENVIRONMENT"] = "test"
# 測試一律用 fake model：環境變數優先於 .env，所以 .env 設了雲端供應商也不影響
os.environ["LLM_PROVIDER"] = "fake"

from collections.abc import AsyncGenerator  # noqa: E402

import pytest  # noqa: E402
from app.api.deps import current_user  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.db import ensure_test_database, get_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models import SQLModel, User  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine  # noqa: E402
from sqlmodel.ext.asyncio.session import AsyncSession  # noqa: E402


@pytest.fixture(scope="session")
async def engine() -> AsyncGenerator[AsyncEngine]:
    # 保護 3：資料庫名稱不是 _test 結尾就中止
    ensure_test_database(settings.SQLALCHEMY_DATABASE_URI)
    engine = create_async_engine(settings.SQLALCHEMY_DATABASE_URI)
    ensure_test_database(str(engine.url))

    # 每次測試執行重建 schema
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(SQLModel.metadata.create_all)

    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncGenerator[AsyncSession]:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        yield session


# ---- 共用的條款庫資料（分層，整個測試階段只匯入一次）----
#
# 匯入條款庫（險種、條文、向量）要 2 秒多，原本每個測試都匯入一次。現在依測試需要取最少的一層：
#   incident_catalog：事故類型與待辦範本（seed_sample_catalog）
#   insurances_loaded：真實的險種資料列，沒有條文與向量（保單對得到險種就夠的測試）
#   full_catalog：真實的險種、條文與向量（檢索、結論卡要讀條文內容的測試）
#   policy_codes_loaded：full_catalog 再加上「章名 → 保單代碼」對照（解析保單的測試）
# 這些資料在測試之間不清空。沒有要求它們的測試開始前會先清掉（和原本一樣是空的）；
# 測試結束時發現共用資料被改過（指紋不同）就全部清空重來，所以改條款庫的測試不會污染別人。
# 自己匯入小型假資料的測試不要求這些 fixture，行為和以前完全相同。

_GROUPS = {
    "insurances": ("insurance", "insurance_rule", "rule_embedding"),
    "incidents": ("incident_type", "todo_template"),
}
_SHARED = {
    "insurances_loaded",
    "full_catalog",
    "policy_codes_loaded",
    "incident_catalog",
}


class _Shared:
    """資料庫裡現在留著哪幾層共用資料，以及它們匯入後的指紋。"""

    insurances = False
    rules = False
    codes = False
    incidents = False
    fingerprint: dict[str, str] = {}

    @classmethod
    def clear(cls) -> None:
        cls.insurances = cls.rules = cls.codes = cls.incidents = False
        cls.fingerprint = {}

    @classmethod
    def groups(cls) -> list[str]:
        return [
            g
            for g, on in (("insurances", cls.insurances), ("incidents", cls.incidents))
            if on
        ]


async def _fingerprint(engine: AsyncEngine) -> dict[str, str]:
    out: dict[str, str] = {}
    async with engine.connect() as conn:
        for group in _Shared.groups():
            for table in _GROUPS[group]:
                row = await conn.execute(
                    text(
                        "SELECT md5(coalesce(string_agg(id::text || updated_at::text, ',' "
                        f"ORDER BY id), '')) FROM \"{table}\""
                    )
                )
                out[table] = row.scalar_one()
    return out


async def _truncate(engine: AsyncEngine, keep: set[str]) -> None:
    tables = ", ".join(
        f'"{t.name}"' for t in SQLModel.metadata.sorted_tables if t.name not in keep
    )
    if not tables:
        return
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))


def _kept_tables() -> set[str]:
    return {t for g in _Shared.groups() for t in _GROUPS[g]}


@pytest.fixture(autouse=True)
async def truncate_tables(
    request: pytest.FixtureRequest, engine: AsyncEngine
) -> AsyncGenerator[None]:
    wanted = _SHARED & set(request.fixturenames)
    # 開始前：共用資料有這個測試沒要求的層就清掉（要了 insurances_loaded、留著條文也沒關係）
    layers = {"insurances_loaded", "full_catalog", "policy_codes_loaded"}
    stale = (
        (_Shared.insurances and not wanted & layers)
        or (_Shared.codes and "policy_codes_loaded" not in wanted)
        or (_Shared.incidents and "incident_catalog" not in wanted)
    )
    if stale:
        await _truncate(engine, set())
        _Shared.clear()
    yield
    # 結束後：共用資料被改過就全部清空重來，沒改就留著，只清其他資料表
    if _Shared.groups() and await _fingerprint(engine) != _Shared.fingerprint:
        await _truncate(engine, set())
        _Shared.clear()
    else:
        await _truncate(engine, _kept_tables())


@pytest.fixture
async def incident_catalog(session: AsyncSession, engine: AsyncEngine) -> None:
    """事故類型與待辦範本（`make seed` 載入的同一份 incident_catalog.json）。"""
    if not _Shared.incidents:
        from app.contracts.fakes import seed_sample_catalog

        await seed_sample_catalog(session)
        _Shared.incidents = True
        _Shared.fingerprint = await _fingerprint(engine)


@pytest.fixture
async def insurances_loaded(
    session: AsyncSession, engine: AsyncEngine, catalog
) -> None:
    """真實的險種資料列（FUBON01 等），沒有條文與向量。"""
    if not _Shared.insurances:
        from app.models import Insurance

        specs, _ = catalog
        for spec in specs:
            session.add(
                Insurance(
                    code=spec.code,
                    name=spec.name,
                    approval_no=spec.approval_no,
                    approval_date=spec.approval_date,
                )
            )
        await session.commit()
        _Shared.insurances = True
        _Shared.fingerprint = await _fingerprint(engine)


@pytest.fixture
async def full_catalog(session: AsyncSession, engine: AsyncEngine, catalog) -> None:
    """真實的險種、條文與向量（`make ingest` 實際匯入的內容）。"""
    if not _Shared.rules:
        from app.core.llm import embedding_model_name, get_embeddings
        from app.ingest.catalog import import_catalog

        specs, rules = catalog
        await import_catalog(
            session,
            specs,
            rules,
            embeddings=get_embeddings(),
            model=embedding_model_name(),
        )
        _Shared.insurances = _Shared.rules = True
        _Shared.fingerprint = await _fingerprint(engine)


@pytest.fixture
async def client(engine: AsyncEngine) -> AsyncGenerator[AsyncClient]:
    # 保護 2：API 使用測試 engine 的 session
    async def override_get_session() -> AsyncGenerator[AsyncSession]:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=f"http://test{settings.API_V1_STR}"
    ) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
async def logged_in(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[User]:
    """以一個已登入的管理者呼叫 API（換掉 current_user，不走真的登入；email 在 ADMIN_EMAILS 名單內）。"""
    monkeypatch.setattr(settings, "ADMIN_EMAILS", "admin@example.com")
    user = User(email="admin@example.com")
    session.add(user)
    await session.commit()
    app.dependency_overrides[current_user] = lambda: user
    yield user
    app.dependency_overrides.pop(current_user, None)


@pytest.fixture(scope="session")
def catalog_raw():
    """五份合訂本拆出的險種與條文，未套勘誤 (specs, rules)。"""
    from app.ingest.catalog import build_catalog

    return build_catalog()


@pytest.fixture(scope="session")
def catalog(catalog_raw):
    """catalog_raw 套上勘誤後的結果，也就是 `make ingest` 實際匯入的條文。"""
    from app.ingest.errata import apply_errata, load_errata

    specs, rules = catalog_raw
    return specs, apply_errata(rules, load_errata())


@pytest.fixture
async def policy_codes_loaded(
    session: AsyncSession, engine: AsyncEngine, full_catalog: None
) -> None:
    """條文之外再補上保單代碼對照（`make seed` 會做的那一步）。"""
    assert full_catalog is None  # 要先有條文
    if not _Shared.codes:
        from app.seed import load_policy_codes

        await load_policy_codes(session)
        _Shared.codes = True
        _Shared.fingerprint = await _fingerprint(engine)
