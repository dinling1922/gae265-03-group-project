"""條款問答在 gpt-6-luna 上的「API × 推理強度」量測（#591）。只量測，不改產品程式與設定。

做什麼：用 Demo B（安達進階型、颱風班機延誤）走完 5 號的追問，建出一個事件，再對這個事件問 3 題條款問題，
每一題呼叫 `adapters.agent_answerer`（search_rules 工具、QA_PROMPT、數字檢查都是產品原本的流程），只換模型的建法：

- Chat Completions＋工具：reasoning_effort 設成 none、low、medium、high、xhigh。先各打 `--probe` 次第 1 題，
  把錯誤訊息原文記下來；沒有出錯的強度才和 none 一樣完整量（3 題 × `--runs` 次）。
- Responses API（`use_responses_api=True`、`store=False`）：每種強度 × 3 題 × `--runs` 次。
  API 版本只在這支腳本裡指定（`--responses-api-version`），不改 `.env` 與 `config.py`。
  Responses API 回的 content 是區塊清單，`agent_answerer` 只認字串，所以腳本把模型包一層，
  回覆的文字區塊攤平成字串（推理區塊丟掉）；不包的話每一次都會因為「沒有產生文字」退回貼原文
  （另外用 `--raw-responses` 次實測）。

怎麼跑（先 make up；會呼叫 LLM 供應商，約 150～200 次呼叫，不進 CI）：

    docker compose exec backend uv run python -m app.eval.qa_reasoning_effort \\
        --runs 10 --out /tmp/qa-reasoning-effort.md --json /tmp/qa-reasoning-effort.json
    docker compose cp backend:/tmp/qa-reasoning-effort.md .

每一次記下：白話／保單未載明／貼原文／答不了／錯誤；有沒有自己算次數（ADR-0004，第 1 題才看）；
回應秒數（同時 `--concurrency` 個請求，數字會比單打慢一些）；token 用量（輸入／輸出／其中的推理 token）。
"""

import argparse
import asyncio
import contextvars
import json
import re
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from typing import Any

import httpx
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_openai import AzureChatOpenAI
from sqlmodel.ext.asyncio.session import AsyncSession

from app.agents.incident.adapters import agent_answerer
from app.agents.incident.agent import _masked_for_answer
from app.core.config import ROOT_DIR, settings
from app.core.db import engine
from app.domain.chat.answer import NOT_COVERED
from app.domain.incident import service
from app.eval.incident_stability import _post, load_scenarios, pick
from app.models import Incident

BASE = "http://localhost:8000/api/v1"
EFFORTS = ("none", "low", "medium", "high", "xhigh")
QUESTIONS = {
    "Q1": "延誤 8 小時可以賠多少？",
    "Q2": "班機延誤申請理賠要準備哪些文件？",
    "Q3": "保險公司收到文件後，多久會把理賠金匯給我？",
}
SCENARIO = "Demo 事故 B"  # 安達進階型、颱風班機延誤
ORIGINAL_NOTICE = "（無法產生白話說明"
# 「答不了」：模型沒回 NOT_COVERED，卻說條文答不了（#591 背景裡 Responses API 預設值的情況）
CANNOT_ANSWER = re.compile(r"答不了|無法回答|無法判斷|沒有辦法回答")
# 第 1 題不能自己算給付次數：「兩次」「3 個」這類，排除條文本身的上限寫法
COUNT = re.compile(r"(?<![0-9])([兩二三四五六2-6])\s*(次|個|筆)")
CAP_CONTEXT = re.compile(r"為限|最多|上限|至多|限給付")

CALLS = [0]  # 實際呼叫 agent_answerer 的次數（含完整量測時丟掉的試打）
ERRORS: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar(
    "errors", default=None
)


class Usage(AsyncCallbackHandler):
    """加總一次回答裡所有模型呼叫的 token（agent 可能呼叫好幾次）。"""

    def __init__(self) -> None:
        self.input = self.output = self.reasoning = self.calls = 0

    async def on_llm_end(self, response: Any, **_: Any) -> None:
        for generations in response.generations:
            for g in generations:
                usage = getattr(getattr(g, "message", None), "usage_metadata", None)
                if not usage:
                    continue
                self.calls += 1
                self.input += usage.get("input_tokens", 0)
                self.output += usage.get("output_tokens", 0)
                self.reasoning += (usage.get("output_token_details") or {}).get(
                    "reasoning", 0
                )


class Probe(AzureChatOpenAI):
    """AzureChatOpenAI 加兩件事：記下模型呼叫的例外（`agent_answerer` 會把例外吞掉改貼原文，
    要靠這裡才分得出「錯誤」與「貼原文」）；`flatten` 時把 Responses API 的區塊清單攤平成字串。"""

    flatten: bool = False

    async def _agenerate(self, *args: Any, **kwargs: Any) -> Any:
        try:
            result = await super()._agenerate(*args, **kwargs)
        except Exception as exc:
            errors = ERRORS.get()
            if errors is not None:
                errors.append(f"{type(exc).__name__}: {exc}")
            raise
        if self.flatten:
            for g in result.generations:
                content = g.message.content
                if isinstance(content, list):
                    g.message.content = "".join(
                        b.get("text", "")
                        for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
        return result


def build_model(
    api: str, effort: str, usage: Usage, responses_version: str, *, flatten: bool = True
) -> Probe:
    common: dict[str, Any] = {
        "azure_endpoint": settings.AZURE_OPENAI_ENDPOINT,
        "api_key": settings.AZURE_OPENAI_API_KEY,
        "azure_deployment": settings.AZURE_OPENAI_CHAT_DEPLOYMENT,
        "timeout": 180,
        "max_retries": 1,
        "callbacks": [usage],
    }
    if api == "chat":
        return Probe(
            api_version=settings.AZURE_OPENAI_API_VERSION,
            reasoning_effort=effort,
            **common,
        )
    return Probe(
        api_version=responses_version,
        use_responses_api=True,
        store=False,
        reasoning={"effort": effort},
        flatten=flatten,
        **common,
    )


@dataclass
class Result:
    api: str
    effort: str
    qid: str
    n: int
    kind: str  # 白話／保單未載明／貼原文／答不了／錯誤
    text: str
    seconds: float
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    llm_calls: int
    counts_times: bool  # 第 1 題：白話回答裡自己算出次數（需人工複查）
    error: str | None


def classify(text: str, error: str | None) -> str:
    if error:
        return "錯誤"
    if text == NOT_COVERED:
        return "保單未載明"
    if text.startswith(ORIGINAL_NOTICE):
        return "貼原文"
    if CANNOT_ANSWER.search(text):
        return "答不了"
    return "白話"


def counts_times(qid: str, kind: str, text: str) -> bool:
    if qid != "Q1" or kind != "白話":
        return False
    return any(
        not CAP_CONTEXT.search(text[max(0, m.start() - 12) : m.end() + 12])
        for m in COUNT.finditer(text)
    )


async def setup_incident(base: str) -> str:
    """走完 Demo B 的追問，回傳事件 id。"""
    scenario = next(
        s
        for s in load_scenarios(settings.DATA_DIR / "eval" / "incident_scenarios.json")
        if SCENARIO in s.name
    )
    async with httpx.AsyncClient(base_url=base, timeout=180) as client:
        login = await client.post(
            "/auth/dev-login",
            json={"email": f"qa-effort-{int(time.time())}@example.com"},
        )
        login.raise_for_status()
        policy = await _post(
            client,
            "/policies/manual",
            {
                "plan_id": scenario.plan,
                "sum_insured": scenario.sum_insured,
                "start_date": scenario.start_date,
                "end_date": scenario.end_date,
            },
        )
        data = await _post(
            client,
            f"/policies/{policy['id']}/incidents",
            {"description": scenario.description, "destination": "日本"},
        )
        incident_id = data["incident"]["incident_id"]
        for _ in range(15):
            if data["reply"].get("finished"):
                return incident_id
            said = pick(data["reply"]["message"], scenario.answers)
            data = await _post(
                client, f"/incidents/{incident_id}/messages", {"content": said}
            )
    raise RuntimeError("Demo B 的追問沒有結束")


async def one(
    sem: asyncio.Semaphore,
    ctx: Any,
    api: str,
    effort: str,
    qid: str,
    n: int,
    responses_version: str,
    *,
    flatten: bool = True,
) -> Result:
    async with sem:
        CALLS[0] += 1
        usage = Usage()
        errors: list[str] = []
        ERRORS.set(errors)
        model = build_model(api, effort, usage, responses_version, flatten=flatten)
        started = time.perf_counter()
        text = ""
        async with AsyncSession(engine, expire_on_commit=False) as session:
            result = await agent_answerer(model)(
                QUESTIONS[qid], ctx, service.make_services(session)
            )
            text = result.text
        seconds = time.perf_counter() - started
        error = errors[0] if errors else None
        kind = classify(text, error)
        return Result(
            api=api + ("(raw)" if not flatten else ""),
            effort=effort,
            qid=qid,
            n=n,
            kind=kind,
            text=text,
            seconds=seconds,
            input_tokens=usage.input,
            output_tokens=usage.output,
            reasoning_tokens=usage.reasoning,
            llm_calls=usage.calls,
            counts_times=counts_times(qid, kind, text),
            error=error,
        )


def _short(text: str, limit: int = 240) -> str:
    return re.sub(r"\s+", " ", text).strip()[:limit]


def render(
    results: list[Result], args: argparse.Namespace, calls: int, minutes: float
) -> str:
    lines = [
        f"- 日期：{time.strftime('%Y-%m-%d')}　供應商：{settings.LLM_PROVIDER}　部署：{settings.AZURE_OPENAI_CHAT_DEPLOYMENT}",
        f"- Chat Completions 的 API 版本：{settings.AZURE_OPENAI_API_VERSION}；Responses API 的 API 版本：{args.responses_api_version}（只在腳本裡指定）",
        f"- 情境：{SCENARIO}（安達進階型、颱風班機延誤）；每題每種組合 {args.runs} 次；同時 {args.concurrency} 個請求",
        f"- 共呼叫 {calls} 次回答（每次回答可能含多次模型呼叫），約 {minutes:.0f} 分鐘",
        "",
    ]
    groups: dict[tuple[str, str], list[Result]] = {}
    for r in results:
        groups.setdefault((r.api, r.effort), []).append(r)
    kinds = ("白話", "保單未載明", "貼原文", "答不了", "錯誤")
    lines += [
        "| API | 強度 | 題目 | 白話 | 保單未載明 | 貼原文 | 答不了 | 錯誤 | 自己算次數 | 平均秒 | 最慢秒 | 平均輸入／輸出／推理 token |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    apis = {"chat": 0, "responses": 1, "responses(raw)": 2}
    for (api, effort), rs in sorted(
        groups.items(), key=lambda kv: (apis.get(kv[0][0], 9), EFFORTS.index(kv[0][1]))
    ):
        for qid in QUESTIONS:
            q = [r for r in rs if r.qid == qid]
            if not q:
                continue
            counts = [sum(r.kind == k for r in q) for k in kinds]
            secs = [r.seconds for r in q]
            lines.append(
                f"| {api} | {effort} | {qid} | "
                + " | ".join(str(c) for c in counts)
                + f" | {sum(r.counts_times for r in q) if qid == 'Q1' else '—'}"
                + f" | {statistics.mean(secs):.1f} | {max(secs):.1f} | "
                + f"{statistics.mean(r.input_tokens for r in q):.0f}／{statistics.mean(r.output_tokens for r in q):.0f}／{statistics.mean(r.reasoning_tokens for r in q):.0f} |"
            )
    lines += ["", "題目：" + "；".join(f"{k}＝{v}" for k, v in QUESTIONS.items()), ""]

    lines += ["## 錯誤訊息原文（每種錯誤取第一次）", ""]
    seen: set[str] = set()
    for r in results:
        if r.error and (key := f"{r.api}/{r.effort}/{r.error[:80]}") not in seen:
            seen.add(key)
            lines += [f"- {r.api}／{r.effort}：`{r.error[:600]}`"]
    if not seen:
        lines += ["- （沒有錯誤）"]

    lines += ["", "## 每種組合的代表回答", ""]
    for (api, effort), rs in sorted(
        groups.items(), key=lambda kv: (apis.get(kv[0][0], 9), EFFORTS.index(kv[0][1]))
    ):
        for qid in QUESTIONS:
            q = [r for r in rs if r.qid == qid]
            if not q:
                continue
            top = max({r.kind for r in q}, key=lambda k: sum(r.kind == k for r in q))
            rep = next(r for r in q if r.kind == top)
            body = rep.error[:200] if rep.error else rep.text
            lines += [
                f"- {api}／{effort}／{qid}（{top}，{rep.seconds:.1f} 秒）：{_short(body)}"
            ]

    flagged = [r for r in results if r.counts_times]
    lines += ["", "## 第 1 題疑似自己算次數的回答（需人工複查）", ""]
    lines += [
        f"- {r.api}／{r.effort}／第 {r.n} 次：{_short(r.text, 400)}" for r in flagged
    ] or ["- （沒有）"]
    return "\n".join(lines) + "\n"


async def main() -> int:
    parser = argparse.ArgumentParser(description="條款問答 API × 推理強度量測（#591）")
    parser.add_argument("--runs", type=int, default=10, help="每題每種組合跑幾次")
    parser.add_argument(
        "--probe", type=int, default=3, help="Chat Completions 每種強度先打幾次第 1 題"
    )
    parser.add_argument(
        "--raw-responses",
        type=int,
        default=3,
        help="不攤平 content 的 Responses API 打幾次（確認 agent_answerer 原樣用不了）",
    )
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--base", default=BASE)
    parser.add_argument("--responses-api-version", default="2025-04-01-preview")
    parser.add_argument(
        "--out", default=str(ROOT_DIR / "docs" / "reports" / "qa-reasoning-effort.md")
    )
    parser.add_argument("--json", default=None)
    args = parser.parse_args()

    started = time.perf_counter()
    incident_id = await setup_incident(args.base)
    async with AsyncSession(engine, expire_on_commit=False) as session:
        incident = await session.get(Incident, incident_id)
        assert incident is not None
        ctx = _masked_for_answer(await service.build_context(session, incident))
    sem = asyncio.Semaphore(args.concurrency)
    results: list[Result] = []

    async def run(
        jobs: list[tuple[str, str, str, int]], flatten: bool = True
    ) -> list[Result]:
        done = await asyncio.gather(
            *(
                one(
                    sem,
                    ctx,
                    api,
                    eff,
                    qid,
                    n,
                    args.responses_api_version,
                    flatten=flatten,
                )
                for api, eff, qid, n in jobs
            )
        )
        results.extend(done)
        return list(done)

    def log(label: str, rs: list[Result]) -> None:
        kinds: dict[str, int] = {}
        for r in rs:
            kinds[r.kind] = kinds.get(r.kind, 0) + 1
        print(f"{label}: {kinds}", flush=True)  # noqa: T201

    # 1. Chat Completions 逐一試：先各打 probe 次第 1 題
    chat_ok: list[str] = []
    for effort in EFFORTS:
        rs = await run([("chat", effort, "Q1", n + 1) for n in range(args.probe)])
        log(f"chat/{effort} 試打", rs)
        if any(r.kind != "錯誤" for r in rs):
            chat_ok.append(effort)
    # 2. Chat Completions 沒出錯的強度（預期只有 none）：3 題 × runs 次（試打的 Q1 已在上面，另外補滿）
    for effort in chat_ok:
        jobs = [
            ("chat", effort, qid, n + 1) for qid in QUESTIONS for n in range(args.runs)
        ]
        results[:] = [
            r for r in results if not (r.api == "chat" and r.effort == effort)
        ]
        log(f"chat/{effort} 完整", await run(jobs))
    # 3. Responses API：每種強度 × 3 題 × runs 次
    for effort in EFFORTS:
        jobs = [
            ("responses", effort, qid, n + 1)
            for qid in QUESTIONS
            for n in range(args.runs)
        ]
        log(f"responses/{effort}", await run(jobs))
    # 4. 不攤平 content：agent_answerer 原樣用 Responses API 會怎樣
    if args.raw_responses:
        rs = await run(
            [("responses", "low", "Q1", n + 1) for n in range(args.raw_responses)],
            flatten=False,
        )
        log("responses(raw)/low", rs)

    minutes = (time.perf_counter() - started) / 60
    calls = CALLS[0]
    report = render(results, args, calls, minutes)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(report)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump([asdict(r) for r in results], f, ensure_ascii=False, indent=1)
    print(report)  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
