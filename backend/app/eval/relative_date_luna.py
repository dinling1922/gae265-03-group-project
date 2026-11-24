"""gpt-6-luna 自己換算相對日期與口語時間的正確率量測（#598）。只量測，不改產品程式與設定。

做什麼：5 號「聽懂」用的 `adapters.llm_extractor` 加 `EXTRACT_PROMPT`，模型是 `app.core.llm.get_chat_model()`
（跟正式站同一款、同推理設定：不帶 reasoning_effort，沿用部署的預設）。對 7 句旅客的回答（`incident_scenarios.json`
的「班機延誤（跨日）」與「班機延誤（口語、沒給日期）」），每句各問模型 `--runs` 次，記下模型回的**原始答案**，
不套用 `agent.py` 的 `_with_explicit_date`、`_with_relative_date`、`_without_inferred_date_outside_period`。

每一題的輸入跟正式站一樣：旅客這句話；候選欄位是 `flow.pending_fields` 的順序（前面的題目當作已回答），
第一個是剛問的那題。「今天」是 `EXTRACT_PROMPT` 帶的執行當天日期，模型看不到事故日期，所以「隔天」「昨天」
只能用今天推算，這正是要量的。

「錯」分三種：沒填、格式不對（`adapters._valid` 會丟掉的）、值不對。日期與時間比的是數值（`2026/11/3` 與
`2026/11/03` 視為相同）。Azure 回 429（`no_capacity`）的那次不算對也不算錯，等一下重試到有答案為止，另外計次；
為了數得到每一次 429，量測時 SDK 的自動重試關成 0 次（`app.core.llm.MAX_RETRIES` 在這支腳本裡設成 0），重試由腳本做。
其他錯誤（逾時、連線失敗等）不重試，記成「錯誤」。

怎麼跑（不用 make up 的 API，但要連得到資料庫以外的 Azure；會呼叫 LLM 供應商約 70～80 次，不進 CI；盡量在早上跑）：

    docker compose exec backend uv run python -m app.eval.relative_date_luna \\
        --runs 10 --out /tmp/relative-date-luna.md --json /tmp/relative-date-luna.json
    docker compose cp backend:/tmp/relative-date-luna.md .

報告的「跟 gpt-4.1-mini 的對照」是人工補寫的，腳本只產生前面的量測結果。
"""

import argparse
import asyncio
import contextvars
import json
import random
import re
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime
from types import SimpleNamespace
from typing import Any, cast
from zoneinfo import ZoneInfo

import openai

from app.agents.incident import flow
from app.agents.incident.adapters import _JSON, _valid, llm_extractor
from app.contracts.common import FieldView
from app.core import llm
from app.core.config import ROOT_DIR, settings
from app.domain.incident import catalog

FLIGHT_DELAY = "flight_delay"
MAX_429_ATTEMPTS = 40  # 每一次呼叫最多等幾次 429；超過算錯誤，不算答案


@dataclass(frozen=True)
class Question:
    qid: str
    group: str
    asked: str  # 剛問的欄位代號
    say: str
    expected: str
    # 這句話裡另外要看的欄位（只在模型有填時才檢查）與它的正確答案
    side: tuple[str, str] | None = None


GROUP_A = "A 班機延誤（跨日）"
GROUP_B = "B 班機延誤（口語、沒給日期）"
QUESTIONS = [
    Question("A1", GROUP_A, "dep_plan", "晚上九點半", "21:30"),
    Question(
        "A2",
        GROUP_A,
        "dep_act",
        "隔天凌晨三點才起飛",
        "03:00",
        side=("dep_act_date", "2026/11/13"),
    ),
    Question("A3", GROUP_A, "dep_act_date", "隔天", "2026/11/13"),
    Question("B1", GROUP_B, "date", "昨天，11/12", "2026/11/12"),
    Question("B2", GROUP_B, "dep_plan", "下午兩點半的飛機", "14:30"),
    Question("B3", GROUP_B, "dep_act", "快九點才飛", "21:00"),
    Question("B4", GROUP_B, "dep_act_date", "同一天", "2026/11/12"),
]


@dataclass
class Trace:
    """一次呼叫過程中，從模型那邊記到的東西。"""

    rate_limited: int = 0
    content: str = ""
    error: str | None = None
    seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0


TRACE: contextvars.ContextVar[Trace | None] = contextvars.ContextVar(
    "trace", default=None
)


def is_rate_limited(exc: Exception) -> bool:
    text = str(exc)
    return (
        isinstance(exc, openai.RateLimitError) or "429" in text or "no_capacity" in text
    )


class Recorder:
    """包住 chat model：記下原始回覆與 token，遇到 429 等一下重試到有答案（`llm_extractor` 只會呼叫 `ainvoke`）。"""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    async def ainvoke(self, messages: list[Any]) -> Any:
        trace = TRACE.get()
        assert trace is not None
        for attempt in range(MAX_429_ATTEMPTS):
            started = time.perf_counter()
            try:
                reply = await self.inner.ainvoke(messages)
            except Exception as exc:
                if is_rate_limited(exc):
                    trace.rate_limited += 1
                    await asyncio.sleep(min(2**attempt, 30) + random.random())
                    continue
                trace.error = f"{type(exc).__name__}: {exc}"
                raise
            trace.seconds = time.perf_counter() - started
            trace.content = reply.content if isinstance(reply.content, str) else ""
            usage = getattr(reply, "usage_metadata", None) or {}
            trace.input_tokens = usage.get("input_tokens", 0)
            trace.output_tokens = usage.get("output_tokens", 0)
            trace.reasoning_tokens = (usage.get("output_token_details") or {}).get(
                "reasoning", 0
            )
            return reply
        trace.error = f"429 重試了 {MAX_429_ATTEMPTS} 次還是被拒絕"
        raise RuntimeError(trace.error)


@dataclass
class Outcome:
    kind: str  # 對／沒填／格式不對／值不對／錯誤
    value: str | None  # 模型填的值（沒填是 None）


@dataclass
class Result:
    qid: str
    n: int
    outcome: str
    value: str | None
    side_outcome: str | None
    side_value: str | None
    extras: dict[str, Any]  # 模型另外填了的欄位（原始值）
    event_type: str | None
    raw: str
    rate_limited: int
    seconds: float
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    error: str | None


def normalize(f: FieldView, value: str) -> tuple[int, ...] | str:
    """日期、時間比數值；其他比原字。"""
    nums = [int(p) for p in re.split(r"[:/]", value) if p.isdigit()]
    if f.type in ("date", "time") and nums:
        return tuple(nums)
    return value


def judge(f: FieldView, expected: str, raw_answers: Any) -> Outcome:
    value = raw_answers.get(f.key) if isinstance(raw_answers, dict) else None
    if value is None or (isinstance(value, str) and not value.strip()):
        return Outcome("沒填", None)
    shown = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    valid = _valid(f, value)
    if valid is None:
        return Outcome("格式不對", shown)
    if normalize(f, valid) == normalize(f, expected):
        return Outcome("對", valid)
    return Outcome("值不對", valid)


def flight_delay_fields() -> list[FieldView]:
    ctx = SimpleNamespace(
        event_types=catalog.event_types([]), common_fields=catalog.common_fields()
    )
    return flow.required_fields(cast(Any, ctx), FLIGHT_DELAY)


def candidates(q: Question) -> list[FieldView]:
    """正式站這一題給模型的候選欄位：前面的題目當作已回答，第一個是剛問的。"""
    required = flight_delay_fields()
    keys = [f.key for f in required]
    known = dict.fromkeys(keys[: keys.index(q.asked)], "x")
    ctx = SimpleNamespace(
        event_types=catalog.event_types([]), common_fields=catalog.common_fields()
    )
    fields = flow.pending_fields(cast(Any, ctx), FLIGHT_DELAY, known)
    assert fields[0].key == q.asked, (q.qid, [f.key for f in fields])
    return fields


async def one(
    sem: asyncio.Semaphore, extract: Any, q: Question, n: int, fields: list[FieldView]
) -> Result:
    async with sem:
        trace = Trace()
        TRACE.set(trace)
        result = await extract(q.say, fields)
        by_key = {f.key: f for f in fields}
        raw_answers: Any = {}
        data: Any = {}
        match = _JSON.search(trace.content)
        if match:
            try:
                data = json.loads(match.group(0))
            except json.JSONDecodeError:
                data = {}
        if isinstance(data, dict):
            raw_answers = data.get("answers") or {}
        if trace.error:
            outcome = Outcome("錯誤", None)
        else:
            outcome = judge(by_key[q.asked], q.expected, raw_answers)
        side_outcome = side_value = None
        if q.side and not trace.error:
            side = judge(by_key[q.side[0]], q.side[1], raw_answers)
            side_outcome, side_value = side.kind, side.value
        watched = {q.asked, q.side[0] if q.side else q.asked}
        extras = (
            {k: v for k, v in raw_answers.items() if k not in watched}
            if isinstance(raw_answers, dict)
            else {}
        )
        event_type = data.get("event_type") if isinstance(data, dict) else None
        del result  # 驗證後的答案不用；要的是原始答案
        return Result(
            qid=q.qid,
            n=n,
            outcome=outcome.kind,
            value=outcome.value,
            side_outcome=side_outcome,
            side_value=side_value,
            extras=extras,
            event_type=event_type if isinstance(event_type, str) else None,
            raw=trace.content,
            rate_limited=trace.rate_limited,
            seconds=trace.seconds,
            input_tokens=trace.input_tokens,
            output_tokens=trace.output_tokens,
            reasoning_tokens=trace.reasoning_tokens,
            error=trace.error,
        )


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _tally(values: list[str | None]) -> str:
    counts = Counter(v if v is not None else "（空）" for v in values)
    return "、".join(f"`{v}` × {c}" for v, c in counts.most_common()) or "—"


def render(
    results: list[Result], args: argparse.Namespace, meta: dict[str, Any]
) -> str:
    by_q = {q.qid: [r for r in results if r.qid == q.qid] for q in QUESTIONS}
    lines = [
        f"# gpt-6-luna 自己換算相對日期與口語時間的正確率（{meta['date']}）",
        "",
        "對應 issue #598。只量測，不改產品程式與設定。",
        "",
        "## 設定",
        "",
        f"- 執行日期：{meta['date']}，開始時間 {meta['time']}（容器的時區：{meta['tz']}；台灣時間 {meta['taipei']}）。"
        f"提示詞裡的「今天」是 {meta['today']}；保單期間 2026/11/11～11/19，事故日 2026/11/12，模型看不到。",
        f"- 模型：Azure OpenAI 部署 `{meta['deployment']}`（`app.core.llm.get_chat_model()`，跟正式站 5 號「聽懂」同一個）。",
        f"- 推理設定：不帶 `reasoning_effort`，沿用部署預設（跟正式站一樣；`INCIDENT_QA_REASONING_EFFORT` "
        f"只影響條款問答，設定值是 `{meta['qa_effort']}`，這裡沒用到）。"
        f"實測每次呼叫平均用了 {meta['reasoning_mean']:.0f} 個推理 token。",
        f"- API 版本：`{meta['api_version']}`；SDK 自動重試關成 0 次（為了數得到 429），逾時 {meta['timeout']} 秒（跟正式站一樣）。",
        "- 提示詞：`EXTRACT_PROMPT`；欄位清單依 `flow.pending_fields`，第一個是剛問的題目。",
        f"- 每題 {args.runs} 次，共 {len(results)} 次有答案的呼叫；同時最多 {args.concurrency} 個請求。",
        f"- **429 被拒絕：{meta['rate_limited']} 次**（不算對錯，重試到有答案）；其他錯誤：{meta['errors']} 次。",
        f"- 總共 {meta['requests']} 次模型請求（含 429 重試），花 {meta['minutes']:.1f} 分鐘。",
        f"- 平均每次 {meta['seconds_mean']:.1f} 秒、輸入 {meta['in_mean']:.0f} token、輸出 {meta['out_mean']:.0f} token。",
        "",
        "「答對」＝模型原始答案的剛問那一題等於正確答案（日期、時間比數值）。沒有套用程式的日期換算。",
        "",
        "## 結果",
        "",
        "| 題 | 剛問的題目 | 旅客回答 | 正確答案 | 答對 | 沒填 | 格式不對 | 值不對 | 錯誤 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    fields = {f.key: f for f in flight_delay_fields()}
    for q in QUESTIONS:
        rs = by_q[q.qid]
        c = Counter(r.outcome for r in rs)
        lines.append(
            f"| {q.qid} | {fields[q.asked].name} | 「{q.say}」 | `{q.expected}` | "
            f"**{c['對']}/{len(rs)}** | {c['沒填']} | {c['格式不對']} | {c['值不對']} | {c['錯誤']} |"
        )
    lines += ["", "## 錯的時候錯成什麼", ""]
    for q in QUESTIONS:
        rs = by_q[q.qid]
        lines.append(
            f"### {q.qid}「{q.say}」→ {fields[q.asked].name}（正確 `{q.expected}`）"
        )
        lines.append("")
        wrong = [r for r in rs if r.outcome != "對"]
        if not wrong:
            lines += [f"{len(rs)} 次全對。", ""]
        else:
            for kind in ("值不對", "格式不對", "沒填", "錯誤"):
                part = [r for r in wrong if r.outcome == kind]
                if part:
                    shown = (
                        _tally([r.value for r in part])
                        if kind in ("值不對", "格式不對")
                        else f"{len(part)} 次"
                    )
                    lines.append(f"- {kind}（{len(part)} 次）：{shown}")
        if q.side:
            side_key, side_expected = q.side
            sides = [r.side_outcome for r in rs]
            wrong_values = [
                r.side_value for r in rs if r.side_outcome in ("值不對", "格式不對")
            ]
            lines.append(
                f"- 附帶：同一句話裡模型填的「{fields[side_key].name}」（正確 `{side_expected}`）："
                f"對 {sides.count('對')}、沒填 {sides.count('沒填')}、值不對 {sides.count('值不對')}、"
                f"格式不對 {sides.count('格式不對')}"
                + (f"；錯的值：{_tally(wrong_values)}" if wrong_values else "")
            )
        extras = [r.extras for r in rs if r.extras]
        if extras:
            lines.append(
                f"- 模型另外填了別的欄位（{len(extras)} 次），例如："
                f"`{json.dumps(extras[0], ensure_ascii=False)}`"
            )
        lines.append("")
    lines += [
        "## 每次的原始答案",
        "",
        "從模型回的 JSON 取出剛問那題的值與結果；完整內容在 `--json` 輸出。",
        "",
        "| 題 | 次 | 結果 | 剛問那題的值 | 其他欄位 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for r in results:
        extra = json.dumps(r.extras, ensure_ascii=False) if r.extras else ""
        value = r.value if r.value is not None else ""
        lines.append(f"| {r.qid} | {r.n} | {r.outcome} | `{value}` | `{extra}` |")
    lines.append("")
    return "\n".join(lines)


async def main() -> int:
    parser = argparse.ArgumentParser(description="gpt-6-luna 相對日期換算量測（#598）")
    parser.add_argument("--runs", type=int, default=10, help="每題跑幾次")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument(
        "--out",
        default=str(
            ROOT_DIR / "docs" / "reports" / f"relative-date-luna-{date.today()}.md"
        ),
    )
    parser.add_argument("--json", default=None)
    args = parser.parse_args()

    llm.MAX_RETRIES = 0  # 只在這支腳本：要數得到每一次 429，重試由 Recorder 做
    model = llm.get_chat_model()
    extract = llm_extractor(cast(Any, Recorder(model)))
    sem = asyncio.Semaphore(args.concurrency)
    started = datetime.now().astimezone()
    clock = time.perf_counter()
    prompt_today = date.today()
    jobs = [
        one(sem, extract, q, n + 1, candidates(q))
        for q in QUESTIONS
        for n in range(args.runs)
    ]
    results: list[Result] = list(await asyncio.gather(*jobs))
    minutes = (time.perf_counter() - clock) / 60

    rate_limited = sum(r.rate_limited for r in results)
    meta = {
        "date": started.strftime("%Y-%m-%d"),
        "time": started.strftime("%H:%M"),
        "tz": started.tzname(),
        "taipei": started.astimezone(ZoneInfo("Asia/Taipei")).strftime("%H:%M"),
        "today": prompt_today.strftime("%Y/%m/%d"),
        "deployment": settings.AZURE_OPENAI_CHAT_DEPLOYMENT,
        "api_version": settings.AZURE_OPENAI_API_VERSION,
        "timeout": llm.REQUEST_TIMEOUT,
        "qa_effort": settings.INCIDENT_QA_REASONING_EFFORT or "（沒設定）",
        "rate_limited": rate_limited,
        "errors": sum(1 for r in results if r.error),
        "requests": len(results) + rate_limited,
        "minutes": minutes,
        "reasoning_mean": _mean([r.reasoning_tokens for r in results]),
        "seconds_mean": _mean([r.seconds for r in results]),
        "in_mean": _mean([r.input_tokens for r in results]),
        "out_mean": _mean([r.output_tokens for r in results]),
    }
    report = render(results, args, meta)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(report)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(
                {"meta": meta, "results": [asdict(r) for r in results]},
                f,
                ensure_ascii=False,
                indent=1,
            )
    print(report)  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
