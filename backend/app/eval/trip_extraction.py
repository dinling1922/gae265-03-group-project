"""create_comparison 的抽取評估（#510）：用真的 LLM 對小型題組抽取，核對目的地、日期、在意事項與資訊不足的判斷。

    make eval-trip-extraction                      # 每題跑 1 次
    make eval-trip-extraction args="--runs 3"      # 每題重複跑，看穩定度
    make eval-trip-extraction args="--only 相對日期 --out /tmp/x.md"

題組在 `data/eval/trip_extraction_questions.json`，「今天」固定為檔案裡的日期（相對日期才有固定的標準答案）。
跑的是線上同一條路徑：`llm_extract`（prompt 與結構化輸出）加 `interpret`（程式驗證與日期計算），
不碰資料庫，所以只要 `.env` 有 LLM 供應商設定，會呼叫供應商計費、不進 CI。報告寫到 docs/reports/，
開頭註明供應商、模型與日期：不同供應商與模型的結果不能直接比較。
"""

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from app.core.config import ROOT_DIR, settings
from app.domain.trip_extraction import (
    Extractor,
    Fixed,
    Interpretation,
    extract_with,
)

QUESTIONS = settings.DATA_DIR / "eval" / "trip_extraction_questions.json"


@dataclass(frozen=True)
class Question:
    id: str
    category: str
    request: str
    expect: dict


@dataclass
class Outcome:
    question: Question
    errors: list[str] = field(default_factory=list)  # 不符的地方；空的就是通過
    failure: str | None = None  # LLM 呼叫失敗或輸出格式錯誤

    @property
    def passed(self) -> bool:
        return self.failure is None and not self.errors


def load_questions(path: Path = QUESTIONS) -> tuple[date, list[Question]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return date.fromisoformat(data["today"]), [
        Question(q["id"], q["category"], q["request"], q["expect"])
        for q in data["questions"]
    ]


def judge(q: Question, got: Interpretation) -> list[str]:
    """核對一題的結果，回傳不符的說明（空的是通過）。"""
    want = q.expect
    errors: list[str] = []
    ok = got.complete
    if (want["status"] == "ok") != ok:
        errors.append(
            f"狀態：預期 {want['status']}，實際 {'ok' if ok else 'needs_more_info'}"
        )
    if want["status"] == "ok":
        for key in ("destination", "depart_date", "return_date"):
            actual = getattr(got, key)
            actual = actual.value if hasattr(actual, "value") else actual
            actual = actual.isoformat() if isinstance(actual, date) else actual
            if actual != want[key]:
                errors.append(f"{key}：預期 {want[key]}，實際 {actual}")
        if [c.value for c in got.concerns] != want["concerns"]:
            errors.append(
                f"concerns：預期 {want['concerns']}，實際 {[c.value for c in got.concerns]}"
            )
    else:
        missing = {m.name for m in got.missing}
        if missing != set(want["missing"]):
            errors.append(
                f"缺少的欄位：預期 {sorted(want['missing'])}，實際 {sorted(missing)}"
            )
    if want.get("unsupported") and not got.unsupported:
        errors.append("應該列出不支援的需求，但沒有")
    return errors


async def run_question(q: Question, today: date, extractor: Extractor) -> Outcome:
    try:
        got = await extract_with(extractor, q.request, today, Fixed())
    except Exception as e:  # LLM 失敗或輸出格式錯誤算失敗，不中斷整份評估
        return Outcome(q, failure=f"{type(e).__name__}: {e}")
    return Outcome(q, judge(q, got))


async def evaluate(
    questions: list[Question], today: date, extractor: Extractor, runs: int = 1
) -> list[list[Outcome]]:
    """每題跑 runs 次；回傳與 questions 對應的結果。"""
    return [
        [await run_question(q, today, extractor) for _ in range(runs)]
        for q in questions
    ]


def render_report(
    results: list[list[Outcome]],
    *,
    day: date,
    today: date,
    provider: str,
    model: str,
    runs: int,
) -> str:
    flat = [o for runs_ in results for o in runs_]
    by_cat: dict[str, list[Outcome]] = {}
    for o in flat:
        by_cat.setdefault(o.question.category, []).append(o)
    passed = sum(o.passed for o in flat)
    lines = [
        f"# create_comparison 抽取評估（{day.isoformat()}）",
        "",
        f"- 供應商：{provider}　模型（deployment）：{model}",
        f"- 題數：{len(results)}　每題跑 {runs} 次　題目裡的「今天」：{today.isoformat()}",
        f"- 通過：{passed}／{len(flat)}（{passed / len(flat):.0%}）",
        "- 不同供應商與模型的結果不能直接比較。",
        "",
        "## 各類別",
        "",
        "| 類別 | 通過 | 次數 |",
        "|---|---|---|",
    ]
    for cat, outs in by_cat.items():
        lines.append(f"| {cat} | {sum(o.passed for o in outs)} | {len(outs)} |")
    lines += ["", "## 每題", "", "| 題號 | 類別 | 通過 | 原話 |", "|---|---|---|---|"]
    for runs_ in results:
        q = runs_[0].question
        lines.append(
            f"| {q.id} | {q.category} | {sum(o.passed for o in runs_)}／{len(runs_)} | {q.request} |"
        )
    bad = [o for o in flat if not o.passed]
    lines += ["", "## 不符的結果", ""]
    if not bad:
        lines.append("無。")
    for o in bad:
        lines.append(f"- {o.question.id}：{o.question.request}")
        lines += [f"  - {e}" for e in o.errors]
        if o.failure:
            lines.append(f"  - 呼叫失敗：{o.failure}")
    return "\n".join(lines) + "\n"


def _model_name() -> str:
    from app.core.llm import chat_model_name

    return chat_model_name()


async def main() -> int:
    from app.domain.trip_extraction import llm_extract

    parser = argparse.ArgumentParser(description="create_comparison 抽取評估（真 LLM）")
    parser.add_argument("--runs", type=int, default=1, help="每題跑幾次")
    parser.add_argument("--only", help="只跑類別或題號含這段文字的題目")
    parser.add_argument("--out", type=Path, help="報告路徑")
    args = parser.parse_args()

    today, questions = load_questions()
    if args.only:
        questions = [
            q for q in questions if args.only in q.category or args.only in q.id
        ]
    if not questions:
        print("沒有符合的題目")  # noqa: T201
        return 2
    results = await evaluate(questions, today, llm_extract, args.runs)
    day = date.today()
    report = render_report(
        results,
        day=day,
        today=today,
        provider=settings.LLM_PROVIDER,
        model=_model_name(),
        runs=args.runs,
    )
    path = args.out or (
        ROOT_DIR
        / "docs"
        / "reports"
        / f"trip-extraction-{day.isoformat()}-{settings.LLM_PROVIDER}.md"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report, encoding="utf-8")
    print(report)  # noqa: T201
    print(f"完整報告 → {path}")  # noqa: T201
    return 0 if all(o.passed for runs_ in results for o in runs_) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
