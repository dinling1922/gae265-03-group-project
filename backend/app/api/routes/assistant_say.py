"""小助理幫你說（#568）：行程層級的 API，回傳在當地拿給櫃檯看的文件清單（當地語言＋英文＋中文）。"""

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.agents.incident.assistant_say import Translator, get_assistant_translator
from app.api.deps import CurrentUser, SessionDep
from app.api.routes.trip import _own_policy
from app.domain import assistant_say
from app.domain.incident import trip as trip_domain

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

    每次打開都重新翻譯（還不存）。行程已結案或沒有要列的文件時回 404（前端不顯示按鈕）。
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
    try:
        translations = assistant_say.validated(
            await translator(sentences, language), sentences, language
        )
    except Exception as e:
        logger.exception("小助理幫你說翻譯失敗（policy %s）", policy_id)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "翻譯失敗，請再試一次") from e
    return assistant_say.assemble(sections, sentences, translations, language)
