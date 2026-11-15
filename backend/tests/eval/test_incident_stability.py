"""5 號穩定度量測腳本的邏輯：劇本載入、「問完後再送一句」的判斷與流程。

不連 API、不呼叫 LLM；流程用 httpx.MockTransport 假裝平台。
"""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from app.agents.incident.agent import NEW_SITUATION_MESSAGE
from app.eval.incident_stability import (
    FollowUp,
    Run,
    Scenario,
    judge_follow_up,
    load_scenarios,
    render_report,
    run_once,
)

ORIGINAL_NOTICE = (
    "（無法產生白話說明，以下直接列出條款原文。）\n\n【CHUBB05 第 21 條】\n..."
)
CITATION = {"insurance_code": "CHUBB05", "article_no": 21}
BASE_ROW: dict[str, Any] = {
    "name": "劇本",
    "plan": "chubb_advanced",
    "sum_insured": 3000000,
    "start_date": "2027-09-06",
    "end_date": "2027-09-11",
    "description": "班機延誤",
    "answers": [["哪一天", "9/11"]],
    "expect_documents": ["D01"],
}


def _load(tmp_path: Path, *rows: dict[str, Any]) -> list[Scenario]:
    path = tmp_path / "scenarios.json"
    path.write_text(json.dumps(list(rows), ensure_ascii=False), encoding="utf-8")
    return load_scenarios(path)


# ---- 載入劇本 ----


def test_劇本可以帶問完後再送的句子與預期(tmp_path: Path) -> None:
    row = {**BASE_ROW, "follow_up": {"say": "延誤 8 小時可以賠多少？", "expect": "qa"}}

    (scenario,) = _load(tmp_path, row)

    assert scenario.follow_up == FollowUp(say="延誤 8 小時可以賠多少？", expect="qa")


@pytest.mark.parametrize(
    "follow_up",
    [
        {"expect": "qa"},
        {"say": "", "expect": "qa"},
        {"say": "問一句", "expect": "亂寫"},
        {"say": "問一句"},
    ],
)
def test_後續句缺漏或預期看不懂_載入就報錯(
    tmp_path: Path, follow_up: dict[str, str]
) -> None:
    with pytest.raises(ValueError, match="follow_up"):
        _load(tmp_path, {**BASE_ROW, "follow_up": follow_up})


def test_沒有後續句的舊劇本照舊載入(tmp_path: Path) -> None:
    (scenario,) = _load(tmp_path, BASE_ROW)

    assert scenario.follow_up is None
    assert scenario.description == "班機延誤"
    assert scenario.expect_documents == frozenset({"D01"})
    assert scenario.answers == [("哪一天", "9/11")]


def test_正式的劇本檔都讀得進來_而且兩個_Demo_劇本帶後續句() -> None:
    from app.core.config import settings

    scenarios = load_scenarios(settings.DATA_DIR / "eval" / "incident_scenarios.json")

    with_follow_up = {s.name: s.follow_up for s in scenarios if s.follow_up}
    assert {n.split("（")[0] for n in with_follow_up} == {"Demo 事故 A", "Demo 事故 B"}
    assert {f.expect for f in with_follow_up.values()} == {"new_situation", "qa"}


# ---- 判斷後續句的回覆 ----

SAME = ({"a": "1"}, ["applicable"])


def test_回覆等於新狀況的固定訊息才算新狀況() -> None:
    reply = {"message": NEW_SITUATION_MESSAGE, "citations": []}

    assert judge_follow_up("new_situation", reply, SAME, SAME) is None
    assert judge_follow_up("new_situation", {**reply, "message": "好"}, SAME, SAME)
    assert judge_follow_up(
        "new_situation", {**reply, "message": NEW_SITUATION_MESSAGE + "。"}, SAME, SAME
    )


def test_條款問答要有出處_而且不是退回貼原文() -> None:
    answer = {"message": "延誤四小時以上可請領，金額依方案。", "citations": [CITATION]}

    assert judge_follow_up("qa", answer, SAME, SAME) is None
    assert "出處" in (
        judge_follow_up("qa", {**answer, "citations": []}, SAME, SAME) or ""
    )
    original = {"message": ORIGINAL_NOTICE, "citations": [CITATION]}
    assert "原文" in (judge_follow_up("qa", original, SAME, SAME) or "")


def test_問答不能是新狀況的固定訊息() -> None:
    reply = {"message": NEW_SITUATION_MESSAGE, "citations": [CITATION]}

    assert judge_follow_up("qa", reply, SAME, SAME)


def test_後續句前後狀況或結論有變動就失敗() -> None:
    reply = {"message": NEW_SITUATION_MESSAGE, "citations": []}

    facts_changed = judge_follow_up(
        "new_situation", reply, SAME, ({"a": "1", "b": "2"}, ["applicable"])
    )
    conclusion_changed = judge_follow_up(
        "new_situation", reply, SAME, ({"a": "1"}, ["excluded"])
    )

    assert facts_changed and "狀況" in facts_changed
    assert conclusion_changed and "結論" in conclusion_changed


# ---- 流程：假的平台 ----


def _incident(facts: dict[str, str], conclusions: list[str]) -> dict[str, Any]:
    return {
        "todos": [{"document_code": "D01", "applicable": True}],
        "clauses": [{"conclusion": c} for c in conclusions],
        "facts": [{"fact_key": k, "value": v} for k, v in facts.items()],
    }


class FakePlatform:
    """依序回覆 POST；記下旅客每一句說了什麼。"""

    def __init__(self, replies: list[dict[str, Any]]) -> None:
        self.replies = replies
        self.said: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.said.append(body.get("content") or body["description"])
        reply = self.replies[len(self.said) - 1]
        return httpx.Response(
            200,
            json={
                "incident": {"incident_id": "inc-1", **reply["incident"]},
                "reply": reply["reply"],
            },
        )

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="http://test", transport=httpx.MockTransport(self.handler)
        )


def _step(
    message: str,
    *,
    finished: bool = False,
    citations: list[dict[str, Any]] | None = None,
    facts: dict[str, str] | None = None,
    conclusions: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "incident": _incident(facts or {"day": "9/11"}, conclusions or ["applicable"]),
        "reply": {
            "message": message,
            "finished": finished,
            "citations": citations or [],
        },
    }


def _scenario(follow_up: FollowUp | None) -> Scenario:
    return Scenario(
        name="劇本",
        plan="chubb_advanced",
        sum_insured=3000000,
        start_date="2027-09-06",
        end_date="2027-09-11",
        description="班機延誤",
        answers=[("哪一天", "9/11")],
        expect_documents=frozenset({"D01"}),
        follow_up=follow_up,
    )


async def test_沒有後續句的劇本_流程與結果跟以前一樣() -> None:
    platform = FakePlatform(
        [_step("哪一天發生的？"), _step("整理好了。", finished=True)]
    )

    async with platform.client() as client:
        run = await run_once(client, "pol-1", _scenario(None))

    assert platform.said == ["班機延誤", "9/11"]
    assert run.ok is True
    assert len(run.turn_seconds) == 2
    assert run.follow_up_problem is None
    assert run.follow_up_ok is None


async def test_問完之後才送後續句_只送一次_前後比對() -> None:
    follow_up = FollowUp(say="颱風來了，我回台灣的班機延誤了", expect="new_situation")
    platform = FakePlatform(
        [
            _step("哪一天發生的？"),
            _step("整理好了。", finished=True),
            _step(NEW_SITUATION_MESSAGE, finished=True),
        ]
    )

    async with platform.client() as client:
        run = await run_once(client, "pol-1", _scenario(follow_up))

    assert platform.said == ["班機延誤", "9/11", "颱風來了，我回台灣的班機延誤了"]
    assert run.follow_up_ok is True
    assert run.follow_up_problem is None
    assert run.ok is True  # 原本的成功判定不受後續句影響
    assert run.log[-2:] == [
        ("旅客", "颱風來了，我回台灣的班機延誤了"),
        ("5 號", NEW_SITUATION_MESSAGE),
    ]


async def test_後續句回了別的東西_記下原因_但原本的成功判定不變() -> None:
    follow_up = FollowUp(say="延誤 8 小時可以賠多少？", expect="qa")
    platform = FakePlatform(
        [
            _step("整理好了。", finished=True),
            _step("可以賠。", finished=True, facts={"day": "9/12"}),
        ]
    )

    async with platform.client() as client:
        run = await run_once(client, "pol-1", _scenario(follow_up))

    assert run.ok is True
    assert run.follow_up_ok is False
    assert run.follow_up_problem


async def test_一直沒問完就不送後續句() -> None:
    follow_up = FollowUp(say="延誤 8 小時可以賠多少？", expect="qa")
    platform = FakePlatform([_step("哪一天發生的？")] * 16)

    async with platform.client() as client:
        run = await run_once(client, "pol-1", _scenario(follow_up))

    assert "延誤 8 小時可以賠多少？" not in platform.said
    assert run.follow_up_ok is False
    assert run.follow_up_problem


# ---- 報告 ----


def _run(**kwargs: Any) -> Run:
    base: dict[str, Any] = {
        "ok": True,
        "documents": ["D01"],
        "conclusions": ["applicable"],
        "facts": {},
        "turn_seconds": [1.0],
        "repeats": 0,
    }
    return Run(**{**base, **kwargs})


def test_有後續句的劇本報告多一欄後續句通過_沒有的維持原表格() -> None:
    plain = _scenario(None)
    plain = Scenario(**{**plain.__dict__, "name": "舊劇本"})
    with_follow_up = Scenario(
        **{**plain.__dict__, "name": "新劇本", "follow_up": FollowUp("問", "qa")}
    )
    results = {
        "舊劇本": [_run()],
        "新劇本": [
            _run(follow_up_ok=True),
            _run(follow_up_ok=False, follow_up_problem="沒有出處"),
        ],
    }

    report = render_report(
        scenarios=[plain, with_follow_up],
        results=results,
        day=__import__("datetime").date(2026, 11, 15),
        provider="p",
        model="m",
    )

    assert (
        "| 劇本 | 成功 | 平均輪數 | 平均秒數 | 每輪平均秒數 | 最慢的一輪 | 重問次數 |"
        in report
    )
    assert "| 舊劇本 | 1/1 |" in report
    assert "## 後續句" in report
    assert "| 新劇本 | 2/2 |" in report  # 上表的成功判定不受後續句影響
    assert "| 新劇本 | 問 | qa | 1/2 | 沒有出處 |" in report
    follow_up_section = report.split("## 後續句")[1]
    assert "舊劇本" not in follow_up_section.split("\n## ")[0]
