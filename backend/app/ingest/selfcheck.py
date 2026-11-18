"""檢索自檢：條文入庫後，確認每一條真的檢索得到。

每一條用「章＋節＋標題＋內文前 120 字」當問題，限定在該險種內，用跟問答一樣的混合檢索；
自己出現在前 N 名就算通過；前 N 名有內文（去空白後）與自己完全相同的條文，也算找到自己。單條沒通過只是警告，不擋入庫；
整個險種的通過率低於門檻時標示「檢索品質異常」。N 與門檻是設定。

通過率以檢查當下該險種資料庫裡的條文為準；之後逐條放行的條文，由 `merge_article` 增量更新。
"""

import re
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.embeddings import Embeddings
from pydantic import BaseModel, Field
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.domain.rag.hybrid import MAX_K, RuleHit, search_rules
from app.models import Insurance, InsuranceRule

QUERY_BODY_CHARS = 120
Search = Callable[..., Awaitable[list[RuleHit]]]


class _BatchedEmbeddings(Embeddings):
    """問題一次批次算好向量，之後逐條檢索時直接取用，不必每條各打一次向量服務。"""

    def __init__(self, inner: Embeddings, vectors: dict[str, list[float]]) -> None:
        self.inner, self.vectors = inner, vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.inner.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        return self.vectors.get(text) or self.inner.embed_query(text)

    async def aembed_query(self, text: str) -> list[float]:
        return self.vectors.get(text) or await self.inner.aembed_query(text)


class SelfCheck(BaseModel):
    top_n: int
    threshold: float
    checked: int
    passed: int
    rate: float = Field(description="通過率（0 到 1）；沒有檢查任何條文時為 1")
    abnormal: bool = Field(description="通過率低於門檻：這個險種的問答可能不可靠")
    failed: list[int] = Field(description="前 N 名找不到自己的條號")


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def build_query(
    title: str,
    content: str,
    *,
    section: str | None = None,
    subsection: str | None = None,
) -> str:
    """章、節、標題（去掉【】）加上內文（去空白）前 QUERY_BODY_CHARS 字。

    不在第一個「。」切斷：綜合險種每一章都有「理賠文件」，第一句都是「…應檢具下列文件：一、理賠申請書」，
    只取第一句會讓同一險種好幾條的問題一字不差，必定有條找不到自己。章與節本來就在條文的檢索文字裡。
    """
    parts = (
        section,
        subsection,
        title.strip("【】 "),
        _compact(content)[:QUERY_BODY_CHARS],
    )
    return " ".join(p.strip() for p in parts if p and p.strip())


def summarize(
    checked: int, passed: int, failed: list[int], *, top_n: int, threshold: float
) -> SelfCheck:
    rate = passed / checked if checked else 1.0
    return SelfCheck(
        top_n=top_n,
        threshold=threshold,
        checked=checked,
        passed=passed,
        rate=round(rate, 4),
        abnormal=rate < threshold,
        failed=sorted(failed),
    )


async def check_articles(
    session: AsyncSession,
    code: str,
    article_nos: list[int] | None,
    *,
    embeddings: Embeddings,
    model: str,
    search: Search | None = None,
) -> tuple[int, list[int]]:
    """回傳（檢查幾條、沒通過的條號）。article_nos 是 None 就檢查該險種全部條文。"""
    stmt = (
        select(InsuranceRule)
        .join(Insurance)
        .where(Insurance.code == code)
        .order_by(col(InsuranceRule.article_no))
    )
    if article_nos is not None:
        stmt = stmt.where(col(InsuranceRule.article_no).in_(article_nos))
    rules = (await session.exec(stmt)).all()
    failed: list[int] = []
    queries = [
        build_query(r.title, r.content, section=r.section, subsection=r.subsection)
        for r in rules
    ]
    if queries:
        vectors = await embeddings.aembed_documents(queries)
        embeddings = _BatchedEmbeddings(
            embeddings, dict(zip(queries, vectors, strict=True))
        )
    for rule, query in zip(rules, queries, strict=True):
        hits = await (search or search_rules)(
            session,
            query,
            k=min(settings.INGEST_SELFCHECK_TOP_N, MAX_K),
            insurance_codes=[code],
            embeddings=embeddings,
            model=model,
        )
        own = _compact(rule.content)
        # 內容完全相同的條文（例如各章都有的「代位」）檢索分不出來，找到哪一條都算找到自己
        if not any(
            h.article_no == rule.article_no or _compact(h.content) == own for h in hits
        ):
            failed.append(rule.article_no)
    return len(rules), failed


async def check_insurance(
    session: AsyncSession,
    code: str,
    *,
    embeddings: Embeddings,
    model: str,
    search: Search | None = None,
) -> SelfCheck:
    checked, failed = await check_articles(
        session, code, None, embeddings=embeddings, model=model, search=search
    )
    return summarize(
        checked,
        checked - len(failed),
        failed,
        top_n=settings.INGEST_SELFCHECK_TOP_N,
        threshold=settings.INGEST_SELFCHECK_MIN_PASS_RATE,
    )


def merge_article(
    previous: dict[str, Any] | None, article_no: int, passed: bool
) -> SelfCheck:
    """逐條放行一條後增量更新：這一條是新入庫的，加進已檢查的條數。"""
    old = SelfCheck.model_validate(previous) if previous else None
    checked = (old.checked if old else 0) + 1
    ok = (old.passed if old else 0) + (1 if passed else 0)
    failed = [n for n in (old.failed if old else []) if n != article_no]
    if not passed:
        failed.append(article_no)
    return summarize(
        checked,
        ok,
        failed,
        top_n=settings.INGEST_SELFCHECK_TOP_N,
        threshold=settings.INGEST_SELFCHECK_MIN_PASS_RATE,
    )
