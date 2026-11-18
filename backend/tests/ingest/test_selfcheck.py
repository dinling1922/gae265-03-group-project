"""檢索自檢（上傳 API 的接縫）：入庫後每一條用章、節、標題加內文開頭檢索，前 N 名找得到自己算通過。

向量是確定性的假向量（相同文字相同向量）；真實 PDF、真實資料庫。
「某幾條找不到」靠換掉檢索函式製造（假向量下很難自然重現）。
"""

import logging
from types import SimpleNamespace
from typing import Any

import pytest
from app.api.routes import ingest as ingest_route
from app.core import db
from app.core.config import settings
from app.domain.rag.hybrid import search_rules
from app.ingest import agent, selfcheck
from app.ingest.agent import Classification
from app.models import Insurance, InsuranceRule
from langchain_core.embeddings import DeterministicFakeEmbedding
from sqlmodel import select

from tests.ingest.test_agent import other_company_pdf
from tests.ingest.test_verify_upload import (  # noqa: F401
    stored_nos,
    tamper,
    upload_and_wait,
)

MISSING = {2, 4}  # 假檢索找不到的條號


pytestmark = pytest.mark.usefixtures("logged_in")


@pytest.fixture(autouse=True)
def setup(engine, monkeypatch):
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(ingest_route, "POLL_SECONDS", 0.05)

    async def yes(_: str) -> Classification:
        return Classification(is_policy=True, reason="有條文結構")

    monkeypatch.setattr(agent, "llm_classify", yes)


@pytest.fixture
def some_missing(monkeypatch):
    """檢索找不到 MISSING 裡的條文，其他條文只找得到自己。

    自檢依條號由小到大逐條檢索，所以第 n 次呼叫就是第 n 條（這份 PDF 的條號是 1 到 6）。
    """
    calls: list[str] = []

    async def fake_search(_session, query, **_):
        calls.append(query)
        number = len(calls)
        return [] if number in MISSING else [SimpleNamespace(article_no=number)]

    monkeypatch.setattr(selfcheck, "search_rules", fake_search)


def selfcheck_event(detail: dict) -> dict:
    (event,) = [
        e
        for e in detail["events"]
        if e["step"] == "selfcheck" and e["status"] != "running"
    ]
    return event


async def test_入庫後事件串流出現檢索自檢步驟與通過率(client):
    detail = await upload_and_wait(client, other_company_pdf("富邦產物"))

    event = selfcheck_event(detail)
    data = event["data"]
    assert data["checked"] == 6
    assert data["passed"] + len(data["failed"]) == 6
    assert data["rate"] == round(data["passed"] / 6, 4)
    assert data["top_n"] == settings.INGEST_SELFCHECK_TOP_N
    assert detail["summary"]["selfcheck"] == data  # 也存進工作的結果
    steps = [e["step"] for e in detail["events"] if e["status"] != "running"]
    assert steps.index("import") < steps.index("selfcheck") < steps.index("done")


async def test_名次放寬到很大時全部找得到(client, monkeypatch):
    monkeypatch.setattr(settings, "INGEST_SELFCHECK_TOP_N", 100)

    detail = await upload_and_wait(client, other_company_pdf("富邦產物"))

    data = selfcheck_event(detail)["data"]
    assert data["rate"] == 1.0
    assert data["failed"] == []
    assert data["abnormal"] is False
    assert selfcheck_event(detail)["status"] == "ok"


async def test_門檻改設定_異常標示跟著變(client, monkeypatch):
    monkeypatch.setattr(settings, "INGEST_SELFCHECK_TOP_N", 100)
    monkeypatch.setattr(settings, "INGEST_SELFCHECK_MIN_PASS_RATE", 1.01)

    detail = await upload_and_wait(client, other_company_pdf("富邦產物"))

    assert selfcheck_event(detail)["data"]["abnormal"] is True


@pytest.mark.usefixtures("some_missing")
async def test_單條沒通過只是警告_不影響入庫(client, session, monkeypatch):
    monkeypatch.setattr(settings, "INGEST_SELFCHECK_MIN_PASS_RATE", 0.5)

    detail = await upload_and_wait(client, other_company_pdf("富邦產物"))

    event = selfcheck_event(detail)
    assert event["status"] == "warning"
    assert event["data"]["failed"] == sorted(MISSING)
    assert event["data"]["abnormal"] is False  # 4/6 高於門檻 0.5
    assert detail["status"] == "done"  # 沒擋入庫
    assert await stored_nos(session) == [1, 2, 3, 4, 5, 6]


@pytest.mark.usefixtures("some_missing")
async def test_通過率低於門檻時標示檢索品質異常(client, monkeypatch):
    monkeypatch.setattr(settings, "INGEST_SELFCHECK_MIN_PASS_RATE", 0.95)

    detail = await upload_and_wait(client, other_company_pdf("富邦產物"))

    data = selfcheck_event(detail)["data"]
    assert data["abnormal"] is True
    assert "檢索品質異常" in selfcheck_event(detail)["message"]


async def test_自檢出錯時略過_不讓上傳失敗(client, session, monkeypatch):
    async def broken(*_a, **_k):
        raise RuntimeError("向量服務掛了")

    monkeypatch.setattr(selfcheck, "search_rules", broken)

    detail = await upload_and_wait(client, other_company_pdf("富邦產物"))

    assert detail["status"] == "done"
    assert selfcheck_event(detail)["status"] == "warning"
    assert "selfcheck" not in detail["summary"]
    assert await stored_nos(session) == [1, 2, 3, 4, 5, 6]


@pytest.mark.usefixtures("tamper")
async def test_險種資料回報待處理條文數與自檢結果(client):
    await upload_and_wait(client, other_company_pdf("富邦產物"))

    (insurance,) = [
        i for i in (await client.get("/insurances")).json() if i["code"] == "FUBON07"
    ]
    assert insurance["pending_count"] == 2
    check = insurance["retrieval_check"]
    assert check["checked"] == 4  # 通過的 4 條入庫後做了自檢
    assert {"rate", "abnormal", "failed", "top_n", "threshold"} <= set(check)


@pytest.mark.usefixtures("tamper")
async def test_放行一條後自檢跟著更新_待處理數減一(client):
    detail = await upload_and_wait(client, other_company_pdf("富邦產物"))
    job_id = detail["id"]

    response = await client.post(f"/ingest/jobs/{job_id}/articles/3/release")

    assert response.status_code == 200, response.text
    insurance = next(
        i for i in (await client.get("/insurances")).json() if i["code"] == "FUBON07"
    )
    assert insurance["pending_count"] == 1
    assert insurance["retrieval_check"]["checked"] == 5


async def test_沒有上傳紀錄的險種待處理數為_0(client):
    for insurance in (await client.get("/insurances")).json():
        assert insurance["pending_count"] == 0
        assert insurance["retrieval_check"] is None


@pytest.mark.usefixtures("tamper")
async def test_險種有待處理條文時檢索會留_log(client, session, caplog):
    await upload_and_wait(client, other_company_pdf("富邦產物"))

    with caplog.at_level(logging.WARNING, logger="app.domain.rag.hybrid"):
        await search_rules(session, "班機延誤", insurance_codes=["FUBON07"])

    messages = [r.getMessage() for r in caplog.records]
    assert any("FUBON07" in m and "2 條待確認條文" in m for m in messages)
    assert not any("班機延誤" in m for m in messages)  # 不記使用者的問題


async def test_沒有待處理條文時檢索不留_log(client, session, caplog):
    await upload_and_wait(client, other_company_pdf("富邦產物"))

    with caplog.at_level(logging.WARNING, logger="app.domain.rag.hybrid"):
        await search_rules(session, "班機延誤", insurance_codes=["FUBON07"])

    assert not [r for r in caplog.records if "待確認條文" in r.getMessage()]


# ---- 查詢句組法：章＋節＋標題＋內文前 120 字；內容相同的條文算找到自己 ----

CLAIM_OPENING = "被保險人向本公司申請理賠時，應檢具下列文件：一、理賠申請書。"
SAME_CONTENT = (
    "本公司依本保險契約給付保險金後，於給付金額範圍內代位行使被保險人對第三人之請求權。"
)


async def add_rules(session, rules: list[tuple[int, str, str, str]]) -> None:
    """建一個小險種 SELF01；rules 是（條號、章、標題、內文）。"""
    insurance = Insurance(code="SELF01", name="自檢測試險種")
    session.add(insurance)
    await session.flush()
    for no, section, title, content in rules:
        session.add(
            InsuranceRule(
                insurance_id=insurance.id,
                article_no=no,
                chapter=f"條文{no}",
                title=title,
                content=content,
                section=section,
                source_file="self.pdf",
                page_start=1,
                page_end=1,
            )
        )
    await session.commit()


def bigram_search(session_rules: list[InsuranceRule]):
    """只看查詢句的假檢索：跟條文（章、節、標題、內文）共有的二字詞越多排越前。

    結果只由查詢句決定，所以兩條查詢句一字不差時，結果也一模一樣（真實檢索也是如此）。
    """

    def grams(text: str) -> set[str]:
        return {text[i : i + 2] for i in range(len(text) - 1)}

    async def search(_session, query, *, k, **_) -> list[Any]:
        q = grams(query)
        scored = sorted(
            session_rules,
            key=lambda r: (
                -len(
                    q
                    & grams(
                        f"{r.section or ''}{r.subsection or ''}{r.title}{r.content}"
                    )
                ),
                r.article_no,
            ),
        )
        return scored[:k]

    return search


async def all_rules(session) -> list[InsuranceRule]:
    return list((await session.exec(select(InsuranceRule))).all())


async def test_兩章都有理賠文件且第一句相同_兩條都找得到自己(session, monkeypatch):
    monkeypatch.setattr(settings, "INGEST_SELFCHECK_TOP_N", 1)
    await add_rules(
        session,
        [
            (
                10,
                "第二章 旅程取消保險",
                "理賠文件",
                CLAIM_OPENING + "二、旅程取消證明。",
            ),
            (
                20,
                "第三章 班機延誤保險",
                "理賠文件",
                CLAIM_OPENING + "二、班機延誤證明。",
            ),
        ],
    )

    checked, failed = await selfcheck.check_articles(
        session,
        "SELF01",
        None,
        embeddings=DeterministicFakeEmbedding(size=8),
        model="fake",
        search=bigram_search(await all_rules(session)),
    )

    assert checked == 2
    assert failed == []


def test_查詢句含章與節_不在第一個句號切斷():
    query = selfcheck.build_query(
        "【理賠文件】",
        "被保險人申請理賠時，應檢具下列文件：一、理賠申請書。二、班機延誤證明。",
        section="第三章 個人海外旅行不便保險",
        subsection="第二節 班機延誤保險",
    )

    assert query.startswith("第三章 個人海外旅行不便保險 第二節 班機延誤保險 理賠文件 ")
    assert "班機延誤證明" in query  # 第一句之後的內文也在


def test_查詢句的內文最多取_120_字():
    query = selfcheck.build_query("標題", "字" * 300)

    assert query == "標題 " + "字" * 120


async def test_前_N_名有內容完全相同的條文_算找到自己(session, monkeypatch):
    monkeypatch.setattr(settings, "INGEST_SELFCHECK_TOP_N", 1)
    await add_rules(
        session,
        [
            (5, "第二章 旅程取消保險", "代位", SAME_CONTENT),
            (
                9,
                "第三章 班機延誤保險",
                "代位",
                " " + SAME_CONTENT.replace("，", "，\n"),
            ),
            (12, "第四章 行李延誤保險", "其他保險", "內容不同的條文。"),
        ],
    )
    rules = {r.article_no: r for r in await all_rules(session)}

    async def always(no: int):
        async def search(*_a, **_k) -> list[Any]:
            return [rules[no]]

        return search

    _, twin = await selfcheck.check_articles(
        session,
        "SELF01",
        [9],
        embeddings=DeterministicFakeEmbedding(size=8),
        model="fake",
        search=await always(5),  # 只找到內容相同（空白不同）的第 5 條
    )
    _, other = await selfcheck.check_articles(
        session,
        "SELF01",
        [12],
        embeddings=DeterministicFakeEmbedding(size=8),
        model="fake",
        search=await always(5),  # 內容不同，不算
    )

    assert twin == []
    assert other == [12]
