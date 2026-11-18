# ruff: noqa: T201  # 命令列工具，結果直接印出來
"""跑合格判定埋錯集：每份樣本走完整條 DocumentAgent.recognize，對照答案表算漏判率、誤判率。

    cd backend && uv run python ../tools/agent6-qualification-eval/evaluate.py
    cd backend && uv run python ../tools/agent6-qualification-eval/evaluate.py --holdout  # 保留集

- 不呼叫 Azure（樣本都是有文字層的 PDF，ocr=None）、不呼叫 LLM（classify_fallback=None）、
  不連 Google Drive（DevFakeDrive + 假授權），也不需要資料庫。
- 規則用 backend/app/agents/document/qualification_rules.json 現在的內容（load_rules）。
- 結果寫到 results.csv，並印出 Markdown 表格（可以直接貼進 REPORT.md）。

判定：
- 「判合格」= status 是 recognized；其他（incomplete、needs_review、failed）都算「沒判合格」。
- 漏判：答案不合格，卻判合格。漏判率 = 漏判數 ÷ 答案不合格的份數。
- 誤判：答案合格，卻沒判合格。誤判率 = 誤判數 ÷ 答案合格的份數。
- 答案不合格、也沒判合格，但不是因為被測的欄位（例如回 needs_review 或缺了別的欄位），
  計為「擋下但原因不符」，不算漏判，另外列出。
"""

import asyncio
import csv
import sys
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path

from app.agents.document import DocumentAgent
from app.agents.document.rules import load_rules
from app.agents.document.storage import DevFakeDrive
from app.contracts import (
    IncidentContext,
    PolicySummary,
    TodoView,
    UploadedFile,
    UserContext,
)

HERE = Path(__file__).resolve().parent
# --holdout：跑保留集（holdout/），結果寫到 holdout/results.csv
FOLDER = HERE / "holdout" if "--holdout" in sys.argv[1:] else HERE
SAMPLES = FOLDER / "samples"
ANSWERS = FOLDER / "answers.csv"
RESULTS = FOLDER / "results.csv"

USER = UserContext(user_id=uuid.uuid4(), email="eval@example.com")
VERSIONS = {"complete": "完整", "missing": "缺欄位", "trap": "陷阱"}


class FakeGoogle:
    """假授權：有 Drive token，沒有平台提供的資料夾。"""

    async def access_token(self, user: UserContext, scope: str) -> str | None:
        return "eval-fake-token"

    async def drive_folder_id(self, user: UserContext) -> str | None:
        return None


def context() -> IncidentContext:
    """日本行程（保險期間 2026-07-05～07-11），三項待辦都帶文件代碼。"""
    todos = [
        TodoView(
            id=uuid.uuid4(),
            phase="on_site",
            title=title,
            document_code=code,
        )
        for code, title in (
            ("D01", "請航空公司開立班機延誤／取消證明"),
            ("D13", "請醫院開立診斷書"),
            ("D14", "保留醫療費用收據正本"),
        )
    ]
    return IncidentContext(
        incident_id=uuid.uuid4(),
        title="合格判定埋錯集",
        description="",
        status="open",
        destination="日本",
        occurred_at=datetime(2026, 7, 8, 3, 0, tzinfo=UTC),
        incident_type_code="medical",
        incident_type_name="生病或受傷就醫",
        policy=PolicySummary(
            plan_id="chubb_advanced",
            plan_name="安達 進階型",
            start_date=date(2026, 7, 5),
            end_date=date(2026, 7, 11),
            insurance_codes=[],
            coverages=[],
        ),
        todos=todos,
        documents=[],
    )


def outcome(row: dict[str, str], status: str, missing: list[str]) -> str:
    passed = status == "recognized"
    if row["expected"] == "pass":
        return "正確" if passed else "誤判"
    if passed:
        return "漏判"
    if status == "incomplete" and missing == [row["label"]]:
        return "正確"
    return "擋下但原因不符"


async def run() -> list[dict[str, str]]:
    agent = DocumentAgent(
        ocr=None,
        drive=DevFakeDrive(),
        rules=load_rules(),
        threshold=0.8,
        classify_fallback=None,
    )
    google = FakeGoogle()
    with ANSWERS.open(encoding="utf-8") as f:
        answers = list(csv.DictReader(f))
    results = []
    for row in answers:
        content = (SAMPLES / f"{row['name']}.pdf").read_bytes()
        file = UploadedFile(
            file_name=f"{row['name']}.pdf",
            content_type="application/pdf",
            content=content,
        )
        r = await agent.recognize(context(), file, USER, google)
        results.append(
            row
            | {
                "status": r.status,
                "document_kind": r.document_kind or "",
                "missing": "、".join(r.missing),
                "found_fields": "、".join(r.found_fields),
                "reason": r.reason or "",
                "outcome": outcome(row, r.status, r.missing),
            }
        )
    return results


def rate(num: int, den: int) -> str:
    return f"{num}/{den} = {num / den:.0%}" if den else "—（0 份）"


def summary(results: list[dict[str, str]], key) -> list[str]:
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in results:
        groups[key(r)].append(r)
    groups["全部"] = results
    out = [
        "| 分組 | 份數 | 答案合格 | 答案不合格 | 漏判 | 漏判率 | 誤判 | 誤判率 | 擋下但原因不符 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name, rows in groups.items():
        pos = [r for r in rows if r["expected"] == "pass"]
        neg = [r for r in rows if r["expected"] == "fail"]
        miss = sum(r["outcome"] == "漏判" for r in neg)
        false = sum(r["outcome"] == "誤判" for r in pos)
        other = sum(r["outcome"] == "擋下但原因不符" for r in neg)
        out.append(
            f"| {name} | {len(rows)} | {len(pos)} | {len(neg)} | {miss} | {rate(miss, len(neg))} "
            f"| {false} | {rate(false, len(pos))} | {other} |"
        )
    return out


def main() -> None:
    results = asyncio.run(run())
    with RESULTS.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)

    print("## 每份樣本\n")
    print("| 樣本 | 規則 | 版本 | 答案 | status | missing | reason | 結果 |")
    print("|---|---|---|---|---|---|---|---|")
    for r in results:
        print(
            f"| {r['name']} | {r['code']} {r['label']} | {VERSIONS[r['version']]} "
            f"| {'合格' if r['expected'] == 'pass' else '不合格'} | {r['status']} "
            f"| {r['missing'] or '—'} | {r['reason'] or '—'} | {r['outcome']} |"
        )
    print("\n## 分規則\n")
    print("\n".join(summary(results, lambda r: f"{r['code']} {r['label']}")))
    print("\n## 分版本\n")
    print("\n".join(summary(results, lambda r: VERSIONS[r["version"]])))
    print("\n## 分規則 × 版本\n")
    print(
        "\n".join(
            summary(
                results,
                lambda r: f"{r['code']} {r['label']}／{VERSIONS[r['version']]}",
            )
        )
    )
    print(f"\n結果寫到 {RESULTS}")


if __name__ == "__main__":
    main()
