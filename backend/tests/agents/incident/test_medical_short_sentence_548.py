"""就醫短句（「我骨折了」「我感冒去看醫生」）第一句不再被擋下（#548）。

LLM 回了不支援的類型、或守門信心值低時，句子有就醫關鍵字就照關鍵字走；
真的無關的句子與不支援的類型仍照舊擋下。Jev、LLM 換成假的，不連網。
"""

from app.agents.incident import flow
from app.agents.incident.agent import UNSUPPORTED_MESSAGE, UNSURE_MESSAGE
from app.contracts import IncidentReply

from tests.agents.incident.test_agent_v1 import SERVICES, agent, extractor, fact
from tests.agents.incident.test_demo_script import medical_ctx


async def start(sentence: str, **kw) -> IncidentReply:
    return await agent(**kw).start(medical_ctx(description=sentence), SERVICES)


async def test_我骨折了_LLM_回不支援的類型_仍判成就醫並開始追問() -> None:
    reply = await start("我骨折了", extract=extractor("passport_loss"))

    assert reply.message != UNSUPPORTED_MESSAGE
    assert fact(reply, "event_type") == "medical"
    assert reply.finished is False


def test_我感冒了_也判成就醫() -> None:
    assert flow.guess_type("我感冒了") == flow.MEDICAL


async def test_LLM_回支援的類型時照_LLM_的_不被關鍵字蓋掉() -> None:
    # 句子裡有「醫院」（關鍵字會猜成就醫），但 LLM 判斷是班機延誤
    sentence = "我在醫院，班機也快來不及了"
    assert flow.guess_type(sentence) == flow.MEDICAL

    reply = await start(sentence, extract=extractor("flight_delay"))

    assert fact(reply, "event_type") == "flight_delay"


async def test_LLM_回不支援_關鍵字也猜不到_照舊回固定訊息() -> None:
    for llm_type in ("passport_loss", None):
        reply = await start("我護照不見了", extract=extractor(llm_type))

        assert reply.message == UNSUPPORTED_MESSAGE
        assert reply.facts == []


async def unsure(_text: str) -> str:
    return "unsure"


async def unrelated(_text: str) -> str:
    return "unrelated"


async def test_守門回_unsure_句子有就醫關鍵字_不反問_直接追問() -> None:
    reply = await start(
        "我感冒去看醫生", gate=unsure, extract=extractor(event_type=None)
    )

    assert reply.message != UNSURE_MESSAGE
    assert fact(reply, "event_type") == "medical"


async def test_守門回_unsure_沒有關鍵字_照舊反問() -> None:
    reply = await start("欸那個", gate=unsure)

    assert reply.message == UNSURE_MESSAGE
    assert reply.facts == []


async def test_守門回_unrelated_就算有關鍵字_照舊擋下() -> None:
    reply = await start("我感冒去看醫生", gate=unrelated)

    assert "沒有可以申請理賠的項目" in reply.message
    assert reply.facts == []
