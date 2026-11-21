"""小助理幫你說（#568）：在當地拿給櫃檯看的文件清單。

要列哪些文件、依誰開立分段、每份文件請櫃檯載明什麼，全由程式依理賠文件規則決定（`todo_template`），
語言也由程式決定；LLM（Agent 5）只把程式組好的中文句子翻成當地語言與英文，不產生任何內容或數字（ADR-0004）。
"""

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.contracts import TripContext
from app.models import TodoTemplate

REQUEST = "申請旅遊保險理賠，需要以下文件，麻煩您開立。"
THANKS = "謝謝您的協助。"

# 日本與日本的城市 → 日文。其他目的地的對照表在 #572，先一律只回英文
JAPAN_PLACES = ("日本", "東京", "大阪", "京都", "北海道", "沖繩", "福岡", "名古屋")
JAPANESE = "ja"


def local_language_of(destination: str | None) -> str | None:
    """目的地的當地語言代碼；判斷不出（或不需要另外顯示）是 None，只顯示英文。"""
    if destination and any(place in destination for place in JAPAN_PLACES):
        return JAPANESE
    return None


@dataclass(frozen=True)
class SayDocument:
    code: str
    name: str
    items: tuple[str, ...]


@dataclass(frozen=True)
class SaySection:
    unit: str
    documents: tuple[SayDocument, ...]


async def say_sections(session: AsyncSession, trip: TripContext) -> list[SaySection]:
    """整個行程要請櫃檯開立的文件，依開立單位分段。

    條件：行程沒結案；事件在保障內；階段是事故當下、適用、不是旅客自備、有開立單位與櫃檯用文件名稱的事件待辦。
    文件名稱用 counter_name，不用待辦標題（標題是寫給旅客的待辦句）。
    已完成的照列；同一份文件代碼只列一次；單位與文件依事件、待辦順序第一次出現的先後排列。
    """
    if trip.status == "completed":
        return []
    todos = [
        t
        for incident in trip.incidents
        if incident.covered
        for t in incident.todos
        if t.phase == "on_site"
        and t.applicable
        and t.status != "not_applicable"
        and t.document_code
    ]
    codes = {t.document_code for t in todos if t.document_code}
    templates: dict[str, TodoTemplate] = {}
    for template in (
        await session.exec(
            select(TodoTemplate).where(col(TodoTemplate.document_code).in_(codes))
        )
    ).all():
        # 同一份文件在各事故類型的定義相同，取任何一筆
        if template.document_code:
            templates.setdefault(template.document_code, template)

    units: dict[str, dict[str, SayDocument]] = {}
    for todo in todos:
        template = templates.get(todo.document_code or "")
        if (
            template is None
            or template.self_prepared
            or not template.issuer
            or not template.counter_name
        ):
            continue
        units.setdefault(template.issuer, {}).setdefault(
            template.document_code or "",
            SayDocument(
                code=template.document_code or "",
                name=template.counter_name,
                items=tuple(template.counter_items),
            ),
        )
    return [
        SaySection(unit=unit, documents=tuple(docs.values()))
        for unit, docs in units.items()
    ]


def sentences_of(sections: list[SaySection]) -> dict[str, str]:
    """要翻譯的中文句子，句子 ID → 中文：請求、各單位、各文件名稱、各請櫃檯載明的項目、謝謝。"""
    out = {"request": REQUEST}
    for u, section in enumerate(sections):
        out[f"unit{u}"] = section.unit
        for d, doc in enumerate(section.documents):
            out[f"doc{u}_{d}"] = doc.name
            for i, item in enumerate(doc.items):
                out[f"item{u}_{d}_{i}"] = item
    out["thanks"] = THANKS
    return out


def fingerprint(sentences: dict[str, str], language: str | None) -> str:
    """要翻譯的句子（句子 ID → 中文）加目標語言的指紋。

    文件清單、開立單位、文件名稱、請櫃檯載明的項目、語言任何一個變了都不一樣；勾選完成不影響。
    """
    canonical = json.dumps(
        {"sentences": sentences, "language": language},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class Translation(BaseModel):
    id: str = Field(description="句子 ID，原樣帶回")
    local: str | None = Field(description="當地語言的翻譯；只要英文時是 null")
    english: str = Field(description="英文的翻譯")


class Translations(BaseModel):
    translations: list[Translation]


_NUMBER = re.compile(r"\d+")


def validated(
    raw: object, sentences: dict[str, str], language: str | None
) -> dict[str, Translation]:
    """翻譯員的輸出：句子 ID 要和送出去的完全一樣、譯文不能出現原句沒有的數字（ADR-0004）。

    不合格丟 ValueError（ValidationError 也是 ValueError）。
    """
    result = Translations.model_validate(raw).translations
    by_id = {t.id: t for t in result}
    if len(by_id) != len(result) or set(by_id) != set(sentences):
        raise ValueError(f"翻譯的句子 ID 和送出的不一致：{sorted(by_id)}")
    for key, translation in by_id.items():
        if not translation.english.strip() or (
            language and not (translation.local or "").strip()
        ):
            raise ValueError(f"翻譯是空的：{key}")
        allowed = set(_NUMBER.findall(sentences[key]))
        shown = _NUMBER.findall(f"{translation.local or ''} {translation.english}")
        if not set(shown) <= allowed:
            raise ValueError(f"譯文出現原句沒有的數字：{key}")
    return by_id


class SayLine(BaseModel):
    local: str | None = Field(description="當地語言；只顯示英文時、或翻譯失敗時是 null")
    english: str | None = Field(description="英文；翻譯失敗時是 null")
    chinese: str = Field(description="中文小字，直接用原句，不經過 LLM")


class SayDocumentOut(BaseModel):
    code: str
    name: SayLine
    items: list[SayLine] = Field(description="請櫃檯載明的項目")


class SaySectionOut(BaseModel):
    unit: SayLine = Field(description="開立單位")
    documents: list[SayDocumentOut]


class AssistantSayOut(BaseModel):
    status: Literal["ok", "failed"] = Field(
        default="ok",
        description=(
            "ok 翻譯成功；failed 翻譯失敗（LLM 出錯、句子 ID 對不上、譯文有原句沒有的數字），"
            "這時只有中文，local 與 english 都是 null，不存、下一次打開會再翻譯。"
            "預設 ok：#570 存下的內容沒有這個欄位"
        ),
    )
    language: str | None = Field(
        description="當地語言代碼（目前只有 ja）；只顯示英文時是 null"
    )
    request: SayLine
    sections: list[SaySectionOut]
    thanks: SayLine


def assemble(
    sections: list[SaySection],
    sentences: dict[str, str],
    translations: dict[str, Translation] | None,
    language: str | None,
) -> AssistantSayOut:
    """翻譯結果組成回傳內容；translations 是 None 代表翻譯失敗，只放中文。"""

    def line(key: str) -> SayLine:
        if translations is None:
            return SayLine(local=None, english=None, chinese=sentences[key])
        t = translations[key]
        return SayLine(
            local=t.local if language else None,
            english=t.english,
            chinese=sentences[key],
        )

    return AssistantSayOut(
        status="ok" if translations is not None else "failed",
        language=language,
        request=line("request"),
        sections=[
            SaySectionOut(
                unit=line(f"unit{u}"),
                documents=[
                    SayDocumentOut(
                        code=doc.code,
                        name=line(f"doc{u}_{d}"),
                        items=[line(f"item{u}_{d}_{i}") for i in range(len(doc.items))],
                    )
                    for d, doc in enumerate(section.documents)
                ],
            )
            for u, section in enumerate(sections)
        ],
        thanks=line("thanks"),
    )
