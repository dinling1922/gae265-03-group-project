"""小助理幫你說（#568、#569）：在當地拿給櫃檯看的文件清單，用 Agent 5 翻成當地語言與英文。

翻譯員用假的函式注入（不打真的 API）：把每句加上 [ja]／[en] 標記後回傳，並記下收到什麼。
"""

from collections.abc import AsyncGenerator, Generator
from typing import Any

import pytest
from app.agents.incident.assistant_say import get_assistant_translator
from app.api.deps import current_user
from app.domain.incident import service
from app.main import app
from app.models import (
    Incident,
    IncidentClause,
    IncidentTodo,
    Insurance,
    Policy,
    TodoTemplate,
    User,
)
from httpx import AsyncClient
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

pytestmark = pytest.mark.usefixtures("incident_catalog")

REQUEST = "申請旅遊保險理賠，需要以下文件，麻煩您開立。"
THANKS = "謝謝您的協助。"


class FakeTranslator:
    """每句加上 [ja]／[en] 後回傳，並記下收到的句子與目標語言。"""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple[dict[str, str], str | None]] = []
        self.error = error

    async def __call__(self, sentences: dict[str, str], language: str | None) -> Any:
        self.calls.append((dict(sentences), language))
        if self.error:
            raise self.error
        return {
            "translations": [
                {
                    "id": key,
                    "local": f"{text}[ja]" if language else None,
                    "english": f"{text}[en]",
                }
                for key, text in sentences.items()
            ]
        }


@pytest.fixture
def translator() -> Generator[FakeTranslator]:
    fake = FakeTranslator()
    app.dependency_overrides[get_assistant_translator] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_assistant_translator, None)


@pytest.fixture
async def user(session: AsyncSession) -> AsyncGenerator[User]:
    u = User(email="traveler@example.com", name="王小明")
    session.add(u)
    await session.commit()
    app.dependency_overrides[current_user] = lambda: u
    yield u
    app.dependency_overrides.pop(current_user, None)


async def new_policy(
    session: AsyncSession, user: User, destination: str = "日本"
) -> Policy:
    policy = Policy(
        user_id=user.id,
        display_name="進階型 2026-07-05",
        plan_id="fubon_advanced",
        sum_insured=6_000_000,
        destination=destination,
    )
    session.add(policy)
    await session.commit()
    return policy


async def add_incident(
    session: AsyncSession,
    policy: Policy,
    todos: dict[str, dict[str, Any]],
    *,
    title: str = "班機延誤",
    status: str = "open",
) -> Incident:
    """建立事件與事件待辦：todos 是 {文件代碼: 覆蓋的欄位（status、applicable、phase）}。"""
    incident = await service.create_incident(
        session, policy, description="事故", destination=None, title=title
    )
    incident.status = status
    session.add(incident)
    await session.flush()
    for order, (code, overrides) in enumerate(todos.items()):
        template = (
            await session.exec(
                select(TodoTemplate).where(TodoTemplate.document_code == code)
            )
        ).first()
        assert template is not None, code
        session.add(
            IncidentTodo(
                incident_id=incident.id,
                document_code=code,
                phase=overrides.get("phase", template.phase),
                title=template.title,
                status=overrides.get("status", "pending"),
                applicable=overrides.get("applicable", True),
                sort_order=order,
            )
        )
    await session.commit()
    return incident


async def say(client: AsyncClient, policy: Policy) -> Any:
    response = await client.post(f"/policies/{policy.id}/assistant-say")
    assert response.status_code == 200, response.text
    return response.json()


def listed_codes(body: Any) -> list[str]:
    return [d["code"] for s in body["sections"] for d in s["documents"]]


async def test_只列事故當下與已完成的文件_回國後_不適用_旅客自備都不列(
    client: AsyncClient,
    session: AsyncSession,
    user: User,
    translator: FakeTranslator,  # noqa: ARG001
) -> None:
    policy = await new_policy(session, user)
    await add_incident(
        session,
        policy,
        {
            "D01": {},  # 事故當下
            "D05": {},  # 回國後
            "D14": {"applicable": False},  # 不適用
            "D02": {},  # 旅客自備（機票及登機證）
            "D13": {"status": "done"},  # 已完成
        },
    )

    body = await say(client, policy)

    assert sorted(listed_codes(body)) == ["D01", "D13"]


async def test_兩件就醫事件都有診斷書_只列一次(
    client: AsyncClient,
    session: AsyncSession,
    user: User,
    translator: FakeTranslator,  # noqa: ARG001
) -> None:
    policy = await new_policy(session, user)
    await add_incident(session, policy, {"D13": {}}, title="就醫一")
    await add_incident(session, policy, {"D13": {}}, title="就醫二")

    body = await say(client, policy)

    assert listed_codes(body) == ["D13"]


async def test_班機延誤與就醫_分成航空公司與醫院兩段_醫院那段兩份(
    client: AsyncClient,
    session: AsyncSession,
    user: User,
    translator: FakeTranslator,  # noqa: ARG001
) -> None:
    policy = await new_policy(session, user)
    await add_incident(session, policy, {"D01": {}}, title="班機延誤")
    await add_incident(session, policy, {"D13": {}, "D14": {}}, title="就醫")

    body = await say(client, policy)

    assert [s["unit"]["chinese"] for s in body["sections"]] == ["航空公司", "醫院"]
    assert [d["code"] for d in body["sections"][0]["documents"]] == ["D01"]
    assert [d["code"] for d in body["sections"][1]["documents"]] == ["D13", "D14"]


@pytest.mark.parametrize("destination", ["日本", "東京"])
async def test_日本與日本的城市_每句都有日文_英文_中文(
    client: AsyncClient,
    session: AsyncSession,
    user: User,
    translator: FakeTranslator,  # noqa: ARG001
    destination: str,
) -> None:
    policy = await new_policy(session, user, destination)
    await add_incident(session, policy, {"D13": {}, "D14": {}})
    rules = {
        t.document_code: t
        for t in (await session.exec(select(TodoTemplate))).all()
        if t.document_code in {"D13", "D14"}
    }

    body = await say(client, policy)

    assert body["language"] == "ja"
    lines = [body["request"], body["thanks"]]
    for section in body["sections"]:
        lines.append(section["unit"])
        for doc in section["documents"]:
            lines += [doc["name"], *doc["items"]]
    # 請求、單位、2 份文件的名稱、診斷書的病名與就診日期、收據的正本，加上謝謝
    assert len(lines) == 2 + 1 + 2 + len(rules["D13"].counter_items) + len(
        rules["D14"].counter_items
    )
    for line in lines:
        assert line["local"] == f"{line['chinese']}[ja]"
        assert line["english"] == f"{line['chinese']}[en]"  # 中文小字就是送去翻譯的原句
    assert body["request"]["chinese"] == REQUEST
    assert body["thanks"]["chinese"] == THANKS


async def test_翻譯員只收到請求_謝謝與理賠文件規則的句子_不含個資(
    client: AsyncClient,
    session: AsyncSession,
    user: User,
    translator: FakeTranslator,
) -> None:
    policy = await new_policy(session, user)
    await add_incident(session, policy, {"D01": {}, "D13": {}, "D14": {}})
    rules = (await session.exec(select(TodoTemplate))).all()
    allowed = {REQUEST, THANKS}
    for t in rules:
        allowed |= {t.issuer or "", t.counter_name or "", *t.counter_items}

    await say(client, policy)

    [(sentences, language)] = translator.calls
    assert language == "ja"
    assert set(sentences.values()) <= allowed
    titles = {t.title for t in rules if t.document_code in {"D01", "D13", "D14"}}
    assert not titles & set(
        sentences.values()
    )  # 待辦標題是寫給旅客的待辦句，不給櫃檯看
    everything = " ".join(sentences.values())
    for private in (user.email, user.name or "", policy.display_name, str(policy.id)):
        assert private not in everything


async def test_行程已結案_回_404(
    client: AsyncClient,
    session: AsyncSession,
    user: User,
    translator: FakeTranslator,
) -> None:
    policy = await new_policy(session, user)
    await add_incident(session, policy, {"D01": {}}, status="completed")

    response = await client.post(f"/policies/{policy.id}/assistant-say")

    assert response.status_code == 404
    assert translator.calls == []


async def test_沒有符合的文件_回_404(
    client: AsyncClient,
    session: AsyncSession,
    user: User,
    translator: FakeTranslator,
) -> None:
    policy = await new_policy(session, user)
    await add_incident(session, policy, {"D02": {}})  # 只有旅客自備的機票及登機證

    response = await client.post(f"/policies/{policy.id}/assistant-say")

    assert response.status_code == 404
    assert translator.calls == []


async def test_別人的行程_回_404(
    client: AsyncClient,
    session: AsyncSession,
    user: User,  # noqa: ARG001
    translator: FakeTranslator,
) -> None:
    other = User(email="other@example.com")
    session.add(other)
    await session.flush()
    foreign = await new_policy(session, other)
    await add_incident(session, foreign, {"D01": {}})

    response = await client.post(f"/policies/{foreign.id}/assistant-say")

    assert response.status_code == 404
    assert translator.calls == []


@pytest.mark.parametrize(
    ("todos", "status", "expected"),
    [
        ({"D01": {}}, "open", True),
        ({"D01": {"status": "done"}}, "open", True),
        ({"D01": {}}, "completed", False),  # 已結案
        ({"D02": {}, "D05": {}}, "open", False),  # 只有旅客自備、回國後的文件
    ],
)
async def test_行程頁回傳小助理幫你說的按鈕旗標(
    client: AsyncClient,
    session: AsyncSession,
    user: User,
    translator: FakeTranslator,  # noqa: ARG001
    todos: dict[str, dict[str, Any]],
    status: str,
    expected: bool,
) -> None:
    policy = await new_policy(session, user)
    await add_incident(session, policy, todos, status=status)

    response = await client.get(f"/policies/{policy.id}/trip")

    assert response.status_code == 200, response.text
    assert response.json()["can_assistant_say"] is expected


async def test_不符保障的事件_它的事故當下文件不列(
    client: AsyncClient,
    session: AsyncSession,
    user: User,
    translator: FakeTranslator,  # noqa: ARG001
) -> None:
    policy = await new_policy(session, user)
    covered = await add_incident(session, policy, {"D01": {}}, title="班機延誤")
    excluded = await add_incident(session, policy, {"D13": {}}, title="就醫")
    insurance = Insurance(code="X1", name="X1")
    session.add(insurance)
    await session.flush()
    for incident, conclusion in ((covered, "applicable"), (excluded, "excluded")):
        session.add(
            IncidentClause(
                incident_id=incident.id,
                insurance_id=insurance.id,
                conclusion=conclusion,
            )
        )
    await session.commit()

    body = await say(client, policy)

    assert listed_codes(body) == ["D01"]


async def test_翻譯員丟出錯誤_回_502(
    client: AsyncClient,
    session: AsyncSession,
    user: User,
    translator: FakeTranslator,
) -> None:
    translator.error = RuntimeError("LLM 掛了")
    policy = await new_policy(session, user)
    await add_incident(session, policy, {"D01": {}})

    response = await client.post(f"/policies/{policy.id}/assistant-say")

    assert response.status_code == 502
    assert "翻譯失敗" in response.json()["detail"]
    assert len(translator.calls) == 1


# ---- 存下翻好的內容（#570）：整個行程存一份，清單或語言變了才重翻 ----


async def test_第二次打開不再呼叫翻譯員_內容和第一次一樣(
    client: AsyncClient,
    session: AsyncSession,
    user: User,
    translator: FakeTranslator,
) -> None:
    policy = await new_policy(session, user)
    await add_incident(session, policy, {"D01": {}})

    first = await say(client, policy)
    second = await say(client, policy)

    assert second == first
    assert len(translator.calls) == 1


async def test_新增有事故當下文件的事件後再打開_重新翻譯_內容有新的文件(
    client: AsyncClient,
    session: AsyncSession,
    user: User,
    translator: FakeTranslator,
) -> None:
    policy = await new_policy(session, user)
    await add_incident(session, policy, {"D01": {}}, title="班機延誤")
    assert listed_codes(await say(client, policy)) == ["D01"]

    await add_incident(session, policy, {"D13": {}}, title="就醫")
    body = await say(client, policy)

    assert sorted(listed_codes(body)) == ["D01", "D13"]
    assert len(translator.calls) == 2


async def test_待辦變成不適用後再打開_重新翻譯_不再列那份文件(
    client: AsyncClient,
    session: AsyncSession,
    user: User,
    translator: FakeTranslator,
) -> None:
    policy = await new_policy(session, user)
    await add_incident(session, policy, {"D13": {}, "D14": {}})
    assert listed_codes(await say(client, policy)) == ["D13", "D14"]

    todo = (
        await session.exec(
            select(IncidentTodo).where(IncidentTodo.document_code == "D14")
        )
    ).one()
    todo.applicable = False
    session.add(todo)
    await session.commit()
    body = await say(client, policy)

    assert listed_codes(body) == ["D13"]
    assert len(translator.calls) == 2


async def test_只是勾選完成某項待辦_不重新翻譯(
    client: AsyncClient,
    session: AsyncSession,
    user: User,
    translator: FakeTranslator,
) -> None:
    policy = await new_policy(session, user)
    await add_incident(session, policy, {"D13": {}, "D14": {}})
    first = await say(client, policy)

    todo = (
        await session.exec(
            select(IncidentTodo).where(IncidentTodo.document_code == "D13")
        )
    ).one()
    todo.status = "done"
    todo.done_by = "user"
    session.add(todo)
    await session.commit()

    assert await say(client, policy) == first
    assert len(translator.calls) == 1


async def test_請櫃檯載明的項目改了再打開_重新翻譯(
    client: AsyncClient,
    session: AsyncSession,
    user: User,
    translator: FakeTranslator,
) -> None:
    policy = await new_policy(session, user)
    await add_incident(session, policy, {"D13": {}})
    await say(client, policy)

    # 模擬 Leon 改了理賠文件規則、重跑 seed
    for template in (
        await session.exec(
            select(TodoTemplate).where(TodoTemplate.document_code == "D13")
        )
    ).all():
        template.counter_items = ["病名、就診日期、醫師簽章"]
        session.add(template)
    await session.commit()
    body = await say(client, policy)

    assert len(translator.calls) == 2
    items = body["sections"][0]["documents"][0]["items"]
    assert [i["chinese"] for i in items] == ["病名、就診日期、醫師簽章"]


async def test_翻譯員出錯後再打開_會再呼叫翻譯員_失敗不存(
    client: AsyncClient,
    session: AsyncSession,
    user: User,
    translator: FakeTranslator,
) -> None:
    policy = await new_policy(session, user)
    await add_incident(session, policy, {"D01": {}})

    translator.error = RuntimeError("LLM 掛了")
    assert (
        await client.post(f"/policies/{policy.id}/assistant-say")
    ).status_code == 502
    translator.error = None
    body = await say(client, policy)

    assert listed_codes(body) == ["D01"]
    assert len(translator.calls) == 2
    # 成功之後存下；清單變了又翻譯失敗時，不覆蓋舊的那筆：清單改回去就直接用舊的，不再呼叫
    await add_incident(session, policy, {"D13": {}}, title="就醫")
    translator.error = RuntimeError("LLM 又掛了")
    assert (
        await client.post(f"/policies/{policy.id}/assistant-say")
    ).status_code == 502
    todo = (
        await session.exec(
            select(IncidentTodo).where(IncidentTodo.document_code == "D13")
        )
    ).one()
    todo.applicable = False
    session.add(todo)
    await session.commit()

    assert await say(client, policy) == body
    assert len(translator.calls) == 3
