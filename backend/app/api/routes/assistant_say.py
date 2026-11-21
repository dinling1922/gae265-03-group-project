"""小助理幫你說（#568）：行程層級的 API，回傳在當地拿給櫃檯看的文件清單（當地語言＋英文＋中文）。"""

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.dialects.postgresql import insert
from sqlmodel import select

from app.agents.incident.assistant_say import Translator, get_assistant_translator
from app.api.deps import CurrentUser, SessionDep
from app.api.routes.trip import _own_policy
from app.domain import assistant_say
from app.domain.incident import trip as trip_domain
from app.models import AssistantSayTranslation

router = APIRouter(tags=["trip"])
logger = logging.getLogger(__name__)


@router.post("/policies/{policy_id}/assistant-say")
async def create_assistant_say(
    session: SessionDep,
    user: CurrentUser,
    policy_id: uuid.UUID,
    translator: Annotated[Translator, Depends(get_assistant_translator)],
) -> assistant_say.AssistantSayOut:
    """把整個行程「事故當下」要向當地單位拿的文件，翻成當地語言與英文，依開立單位分段。

    翻好的內容整個行程存一份：句子與語言沒變就直接回存下的；變了才重新翻譯並覆蓋（#570）。行程已結案或沒有要列的文件時回 404（前端不顯示按鈕）。

    翻譯失敗（LLM 出錯、句子 ID 對不上、譯文有原句沒有的數字）回 200，status 是 failed、內容只有中文；失敗不存（#571）。
    """
    policy = await _own_policy(session, user, policy_id)
    trip = await trip_domain.build_trip_context(session, policy)
    sections = await assistant_say.say_sections(session, trip)
    if not sections:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "沒有需要請櫃檯開立的文件")
    language = assistant_say.local_language_of(
        trip_domain.destination_of(trip) or policy.destination
    )
    sentences = assistant_say.sentences_of(sections)
    digest = assistant_say.fingerprint(sentences, language)
    saved = (
        await session.exec(
            select(AssistantSayTranslation).where(
                AssistantSayTranslation.policy_id == policy_id
            )
        )
    ).first()
    if saved is not None and saved.fingerprint == digest:
        return assistant_say.AssistantSayOut.model_validate(saved.content)
    try:
        translations = assistant_say.validated(
            await translator(sentences, language), sentences, language
        )
    except Exception:
        # 只記例外，不記句子與旅客的話；失敗的結果不存，下一次打開會再翻譯（前端的「再試一次」）
        logger.exception("小助理幫你說翻譯失敗（policy %s）", policy_id)
        return assistant_say.assemble(sections, sentences, None, language)
    out = assistant_say.assemble(sections, sentences, translations, language)
    # 整個行程存一份；兩個請求同時第一次打開也只會有一筆（失敗的翻譯在上面就回去了，不會存）
    values = {
        "fingerprint": digest,
        "language": language,
        "content": out.model_dump(),
    }
    await session.exec(
        insert(AssistantSayTranslation)
        .values(policy_id=policy_id, **values)
        .on_conflict_do_update(index_elements=["policy_id"], set_=values)
    )
    await session.commit()
    return out
