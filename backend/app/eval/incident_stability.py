"""5 號穩定度量測：用真模型、像真人一樣打字回答，看每個劇本最後有沒有產生正確的待辦。

劇本在 `data/eval/incident_scenarios.json`：一句事故描述，加上「題目含這個關鍵字就這樣回答」的清單
（自然語句，不點選項；依序比對，所以順序要保留）。每個劇本跑 `--runs` 次，報告寫到 docs/reports/。

走的是真的 API，所以要先 `make up`，而且 `.env` 要有 `AGENT_5_IMPL=v1`、真的 LLM 供應商、
`DEV_LOGIN_ENABLED=true`。每次執行會在**開發資料庫**建立一個測試帳號，以及每個劇本的保單與事件。
會呼叫 LLM 供應商（一次完整量測約 60 段對話），不進 CI。

預期的文件代碼要對照條款與 incident_catalog.json 寫，不憑直覺。
"""

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import httpx

from app.agents.incident.agent import NEW_SITUATION_MESSAGE
from app.core.config import ROOT_DIR, settings

DEFAULT_BASE = "http://localhost:8000/api/v1"
MAX_TURNS = 15
REQUEST_TIMEOUT = 180
UNKNOWN = "不知道"
ASK_AGAIN = "沒有聽懂"  # 5 號重問同一題時的說法
# 問答退回貼條款原文時的開頭（adapters._original）；出現就表示沒有白話說明
ORIGINAL_NOTICE = "（無法產生白話說明，以下直接列出條款原文。）"
FOLLOW_UP_KINDS = ("new_situation", "qa")


@dataclass(frozen=True)
class FollowUp:
    """問完之後再送的一句，和預期的回覆種類：new_situation（固定訊息）或 qa（有出處的條款問答）。

    不論哪一種，這一句都不能改動已記下的狀況與結論，所以前後一律比對。
    """

    say: str
    expect: str


@dataclass(frozen=True)
class Scenario:
    name: str
    plan: str
    sum_insured: int
    start_date: str
    end_date: str
    description: str
    answers: list[tuple[str, str]]
    expect_documents: frozenset[str]
    follow_up: FollowUp | None = None


@dataclass
class Run:
    ok: bool
    documents: list[str]
    conclusions: list[str]
    facts: dict[str, str]
    turn_seconds: list[float]
    repeats: int
    log: list[tuple[str, str]] = field(default_factory=list)
    # 只有劇本帶後續句才有值：None 表示沒有後續句
    follow_up_ok: bool | None = None
    follow_up_problem: str | None = None

    @property
    def seconds(self) -> float:
        return sum(self.turn_seconds)


def _follow_up(row: dict[str, Any]) -> FollowUp | None:
    raw = row.get("follow_up")
    if raw is None:
        return None
    say, expect = raw.get("say"), raw.get("expect")
    if not say or expect not in FOLLOW_UP_KINDS:
        raise ValueError(
            f"劇本「{row['name']}」的 follow_up 要有 say，expect 要是 {FOLLOW_UP_KINDS} 其中之一"
        )
    return FollowUp(say=say, expect=expect)


def load_scenarios(path: Path) -> list[Scenario]:
    return [
        Scenario(
            name=row["name"],
            plan=row["plan"],
            sum_insured=row["sum_insured"],
            start_date=row["start_date"],
            end_date=row["end_date"],
            description=row["description"],
            answers=[(key, answer) for key, answer in row["answers"]],
            expect_documents=frozenset(row["expect_documents"]),
            follow_up=_follow_up(row),
        )
        for row in json.loads(path.read_text(encoding="utf-8"))
    ]


def pick(question: str, answers: list[tuple[str, str]]) -> str:
    """題目含哪個關鍵字就用哪個回答（依序比對）；都對不到就回「不知道」。"""
    return next((a for key, a in answers if key in question), UNKNOWN)


Snapshot = tuple[dict[str, str], list[str]]


def _snapshot(incident: dict[str, Any]) -> Snapshot:
    return (
        {f["fact_key"]: f["value"] for f in incident["facts"]},
        [c["conclusion"] for c in incident["clauses"]],
    )


def judge_follow_up(
    expect: str, reply: dict[str, Any], before: Snapshot, after: Snapshot
) -> str | None:
    """後續句的回覆符不符合預期；符合回 None，不符合回原因。種類由程式判斷，不靠模型。"""
    message = reply["message"]
    if expect == "new_situation":
        if message != NEW_SITUATION_MESSAGE:
            return "沒有回新狀況的固定訊息"
    elif message == NEW_SITUATION_MESSAGE:
        return "預期是條款問答，卻回了新狀況的固定訊息"
    elif not reply.get("citations"):
        return "條款問答沒有出處"
    elif message.startswith(ORIGINAL_NOTICE):
        return "條款問答退回貼條款原文"
    if before[0] != after[0]:
        return "後續句改動了已記下的狀況"
    if before[1] != after[1]:
        return "後續句改動了結論"
    return None


async def _post(client: httpx.AsyncClient, url: str, body: dict[str, Any]) -> Any:
    response = await client.post(url, json=body)
    response.raise_for_status()
    return response.json()


async def run_once(client: httpx.AsyncClient, policy_id: str, s: Scenario) -> Run:
    turn_seconds: list[float] = []

    async def timed(url: str, body: dict[str, Any], *, record: bool = True) -> Any:
        started = time.perf_counter()
        data = await _post(client, url, body)
        if record:
            turn_seconds.append(time.perf_counter() - started)
        return data

    data = await timed(
        f"/policies/{policy_id}/incidents",
        {"description": s.description, "destination": "日本"},
    )
    incident_id = data["incident"]["incident_id"]
    log = [("旅客", s.description), ("5 號", data["reply"]["message"])]
    for _ in range(MAX_TURNS):
        if data["reply"].get("finished"):
            break
        said = pick(data["reply"]["message"], s.answers)
        data = await timed(f"/incidents/{incident_id}/messages", {"content": said})
        log += [("旅客", said), ("5 號", data["reply"]["message"])]

    incident = data["incident"]
    repeats = sum(ASK_AGAIN in text for who, text in log if who == "5 號")
    follow_up_problem: str | None = None
    if s.follow_up:
        # 後續句不算進輪數與秒數，原本的待辦與結論也只看問完那一刻的事件
        if data["reply"].get("finished"):
            before = _snapshot(incident)
            data = await timed(
                f"/incidents/{incident_id}/messages",
                {"content": s.follow_up.say},
                record=False,
            )
            log += [("旅客", s.follow_up.say), ("5 號", data["reply"]["message"])]
            follow_up_problem = judge_follow_up(
                s.follow_up.expect, data["reply"], before, _snapshot(data["incident"])
            )
        else:
            follow_up_problem = "對話沒有問完，沒有送後續句"
    documents = sorted(
        {t["document_code"] for t in incident["todos"] if t["applicable"]} - {None}
    )
    return Run(
        ok=s.expect_documents <= set(documents),
        documents=documents,
        conclusions=[c["conclusion"] for c in incident["clauses"]],
        facts={f["fact_key"]: f["value"] for f in incident["facts"]},
        turn_seconds=turn_seconds,
        repeats=repeats,
        log=log,
        follow_up_ok=None if s.follow_up is None else follow_up_problem is None,
        follow_up_problem=follow_up_problem,
    )


async def measure(
    base: str, scenarios: list[Scenario], runs: int
) -> dict[str, list[Run]]:
    results: dict[str, list[Run]] = {}
    async with httpx.AsyncClient(base_url=base, timeout=REQUEST_TIMEOUT) as client:
        login = await client.post(
            "/auth/dev-login", json={"email": f"stab-{int(time.time())}@example.com"}
        )
        login.raise_for_status()
        for s in scenarios:
            policy = await _post(
                client,
                "/policies/manual",
                {
                    "plan_id": s.plan,
                    "sum_insured": s.sum_insured,
                    "start_date": s.start_date,
                    "end_date": s.end_date,
                },
            )
            results[s.name] = []
            for i in range(runs):
                run = await run_once(client, policy["id"], s)
                results[s.name].append(run)
                print(  # noqa: T201
                    f"{s.name} {i + 1}/{runs} {'OK' if run.ok else 'FAIL'} "
                    f"{len(run.turn_seconds)} 輪 {run.seconds:.1f} 秒 重問 {run.repeats}"
                    f"{_follow_up_label(run)}",
                    flush=True,
                )
    return results


def _follow_up_label(run: Run) -> str:
    if run.follow_up_ok is None:
        return ""
    return (
        " 後續句 OK" if run.follow_up_ok else f" 後續句 FAIL（{run.follow_up_problem}）"
    )


def _passed(run: Run) -> bool:
    return run.ok and run.follow_up_ok is not False


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def render_report(
    *,
    scenarios: list[Scenario],
    results: dict[str, list[Run]],
    day: date,
    provider: str,
    model: str,
) -> str:
    total = sum(len(runs) for runs in results.values())
    passed = sum(r.ok for runs in results.values() for r in runs)
    lines = [
        "# 5 號穩定度量測",
        "",
        f"- 日期：{day.isoformat()}",
        f"- 供應商：{provider}，模型：{model}",
        f"- 結果：**{passed}/{total}** 產生預期的待辦",
        "- 判定：劇本預期的文件代碼都出現在適用的待辦裡才算成功",
        "- 秒數是從送出請求到收到回覆（含平台讀寫），每一輪一次請求",
        "",
        "| 劇本 | 成功 | 平均輪數 | 平均秒數 | 每輪平均秒數 | 最慢的一輪 | 重問次數 |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in scenarios:
        runs = results[s.name]
        turns = [t for r in runs for t in r.turn_seconds]
        lines.append(
            f"| {s.name} | {sum(r.ok for r in runs)}/{len(runs)} "
            f"| {_mean([float(len(r.turn_seconds)) for r in runs]):.1f} "
            f"| {_mean([r.seconds for r in runs]):.1f} "
            f"| {_mean(turns):.1f} | {max(turns, default=0.0):.1f} "
            f"| {sum(r.repeats for r in runs)} |"
        )
    lines.append("")

    with_follow_up = [s for s in scenarios if s.follow_up]
    if with_follow_up:
        lines += [
            "## 後續句",
            "",
            "問完之後再送一句：回覆種類要對，而且不能改動已記下的狀況與結論。",
            "不算進上表的輪數與秒數，也不影響上表的成功判定。",
            "",
            "| 劇本 | 後續句 | 預期 | 通過 | 未通過的原因 |",
            "|---|---|---|---|---|",
        ]
        for s in with_follow_up:
            assert s.follow_up
            runs = results[s.name]
            problems = sorted(
                {r.follow_up_problem for r in runs if r.follow_up_problem}
            )
            lines.append(
                f"| {s.name} | {s.follow_up.say} | {s.follow_up.expect} "
                f"| {sum(r.follow_up_ok is True for r in runs)}/{len(runs)} "
                f"| {'；'.join(problems) or '—'} |"
            )
        lines.append("")

    failures = [(s, i, r) for s in scenarios for i, r in enumerate(results[s.name], 1)]
    failures = [(s, i, r) for s, i, r in failures if not _passed(r)]
    if failures:
        lines.append("## 失敗的對話")
        for s, i, r in failures:
            lines += [
                "",
                f"### {s.name} 第 {i} 次",
                "",
                f"- 預期的文件：{'、'.join(sorted(s.expect_documents))}",
                f"- 實際的文件：{'、'.join(r.documents) or '（沒有）'}",
                f"- 結論：{'、'.join(str(c) for c in r.conclusions) or '（沒有）'}",
                f"- 記下的狀況：{json.dumps(r.facts, ensure_ascii=False)}",
                "",
            ]
            lines += [f"- **{who}**：{text}" for who, text in r.log]
        lines.append("")
    return "\n".join(lines)


def _model_name() -> str:
    match settings.LLM_PROVIDER:
        case "azure_openai":
            return settings.AZURE_OPENAI_CHAT_DEPLOYMENT or ""
        case "openai":
            return settings.OPENAI_CHAT_MODEL
        case "ollama":
            return settings.OLLAMA_CHAT_MODEL
        case _:
            return settings.LLM_PROVIDER


async def main() -> int:
    parser = argparse.ArgumentParser(description="5 號穩定度量測（真模型）")
    parser.add_argument("--runs", type=int, default=10, help="每個劇本跑幾次")
    parser.add_argument("--base", default=DEFAULT_BASE, help="API 的網址")
    parser.add_argument("--only", help="只跑名稱含這段文字的劇本")
    parser.add_argument("--out", type=Path, help="報告路徑")
    args = parser.parse_args()

    scenarios = load_scenarios(settings.DATA_DIR / "eval" / "incident_scenarios.json")
    if args.only:
        scenarios = [s for s in scenarios if args.only in s.name]
    if not scenarios:
        print("沒有符合的劇本")  # noqa: T201
        return 2

    results = await measure(args.base, scenarios, args.runs)
    day = date.today()
    report = render_report(
        scenarios=scenarios,
        results=results,
        day=day,
        provider=settings.LLM_PROVIDER,
        model=_model_name(),
    )
    path = args.out or (
        ROOT_DIR / "docs" / "reports" / f"incident-stability-{day.isoformat()}.md"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report, encoding="utf-8")
    print(report.split("## 失敗的對話")[0])  # noqa: T201
    print(f"完整報告 → {path}")  # noqa: T201
    return 0 if all(_passed(r) for runs in results.values() for r in runs) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
