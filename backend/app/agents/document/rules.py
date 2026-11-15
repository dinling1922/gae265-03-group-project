"""合格規範（#69）：6 號自己的規則檔，用文件代碼連到理賠文件規則。

- 每一條都要有出處：`source.file` 裡要找得到 `source.quote`，載入時由程式檢查，找不到就報錯。
- `verified` 為 true 才生效；草稿可以由 LLM 產生，但只有使用者確認後才能改成 true。
- 一個欄位可以有幾種寫法（`any_of`），符合其中一種就算有。
- 每個條件除了 `pattern`，還可以加（埋錯集量到「欄名後面任何字都算」會誤放，tools/agent6-qualification-eval/）：
  - `exclude`：這一行同時符合就不算（例如「次回受診日」不是這次的就診日期）
  - `date`：`after` 是欄名後面要讀得出日期、`line` 是這一行要讀得出日期（用 dates.py，和保險期間同一套解析）
  - `next_line`：欄名自己一行時，下一行要符合這個樣式才算（表格版面常把欄名和值讀成兩行）
- 檢查只看讀出的文字（ADR-0004），結果分四種：
  - 缺了就不合格的欄位（severity=incomplete）找不到 → 不合格（reason 寫缺什麼，不存檔）
  - 欄位信心度低於門檻 → 不通過（請重拍）
  - 欄位是手寫 → 不確定（請使用者自己確認）
  - 只提醒的欄位（severity=remind）找不到 → 照樣通過，reason 帶提醒
"""

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from app.agents.document.dates import dates_in, outside_period
from app.agents.document.reader import ReadResult
from app.core.config import ROOT_DIR

RULES_FILE = Path(__file__).with_name("qualification_rules.json")
OUTSIDE_PERIOD = (
    "「{}」不在保單的保險期間內，請確認日期是否正確；保險期間外的就醫不在理賠範圍"
)


class RuleSourceMissing(ValueError):
    """規則的出處找不到。"""


DateMode = Literal["after", "line"]


@dataclass(frozen=True)
class Condition:
    pattern: re.Pattern[str]
    min_count: int
    exclude: re.Pattern[str] | None = None
    date: DateMode | None = None
    next_line: re.Pattern[str] | None = None

    def hits(self, lines: list[str], i: int) -> list[str] | None:
        """第 i 行符合這個條件時，回傳算進去的行（有 next_line 時連下一行）；不符合回 None。"""
        line = lines[i]
        found = self.pattern.search(line)
        if found is None or (self.exclude is not None and self.exclude.search(line)):
            return None
        if self.date == "after" and not dates_in(line[found.end() :]):
            return None
        if self.date == "line" and not dates_in(line):
            return None
        if self.next_line is None:
            return [line]
        if i + 1 >= len(lines) or not self.next_line.search(lines[i + 1]):
            return None
        return [line, lines[i + 1]]


def _condition(raw: dict) -> Condition:
    date = raw.get("date")
    if date not in (None, "after", "line"):
        raise ValueError(f"合格規範的 date 只能是 after 或 line：{date}")

    def compiled(key: str) -> re.Pattern[str] | None:
        return re.compile(raw[key], re.IGNORECASE) if raw.get(key) else None

    return Condition(
        re.compile(raw["pattern"], re.IGNORECASE),
        raw.get("min_count", 1),
        exclude=compiled("exclude"),
        date=date,
        next_line=compiled("next_line"),
    )


@dataclass(frozen=True)
class FieldRule:
    code: str
    field: str
    label: str
    severity: Literal["incomplete", "remind"]
    any_of: tuple[tuple[Condition, ...], ...]  # 幾種寫法；每種寫法的條件要同時成立
    missing_reason: str
    issuer: str | None  # 開立單位，組「下一步」用（規格 v3.7 第 5 點）
    source_file: str
    source_quote: str
    verified: bool
    # 讀得出的日期要落在保單的保險期間內，不然請使用者確認（#233）
    within_policy_period: bool = False


@dataclass(frozen=True)
class Problem:
    severity: Literal["incomplete", "failed", "needs_review", "remind"]
    reason: str
    rule: FieldRule | None = None  # 缺了哪一條（不合格時組「缺少〇〇」與下一步）


# 同時有好幾個問題時先講哪一個：不合格與重拍優先，提醒最後
_PRIORITY = {"incomplete": 0, "failed": 0, "needs_review": 1, "remind": 2}


def load_rules(path: Path = RULES_FILE, root: Path = ROOT_DIR) -> list[FieldRule]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    rules = []
    for row in raw["rules"]:
        source = root / row["source"]["file"]
        quote = row["source"]["quote"]
        if not source.is_file() or quote not in source.read_text(encoding="utf-8"):
            raise RuleSourceMissing(
                f"合格規範 {row['code']}.{row['field']} 的出處找不到：{row['source']['file']}"
            )
        rules.append(
            FieldRule(
                code=row["code"],
                field=row["field"],
                label=row["label"],
                severity=row["severity"],
                any_of=tuple(
                    tuple(_condition(c) for c in way) for way in row["any_of"]
                ),
                missing_reason=row["missing_reason"],
                issuer=row.get("issuer"),
                source_file=row["source"]["file"],
                source_quote=quote,
                verified=bool(row.get("verified", False)),
                within_policy_period=bool(row.get("within_policy_period", False)),
            )
        )
    return rules


def matched_lines(rule: FieldRule, lines: list[str]) -> list[str] | None:
    """符合某一種寫法時回傳符合的那幾行；每種寫法都不符合回 None。"""
    for way in rule.any_of:
        hits: list[str] = []
        for cond in way:
            matches = [m for i in range(len(lines)) if (m := cond.hits(lines, i))]
            if len(matches) < cond.min_count:
                break
            hits += [line for m in matches for line in m if line not in hits]
        else:
            return hits
    return None


def found_labels(code: str, read: ReadResult, rules: list[FieldRule]) -> list[str]:
    """這種文件已生效的合格規範裡，文件上找得到的欄位名稱（給畫面顯示判斷依據，不含內容）。"""
    texts = [line.text for line in read.lines]
    return [
        rule.label
        for rule in rules
        if rule.code == code
        and rule.verified
        and matched_lines(rule, texts) is not None
    ]


def check(
    code: str,
    read: ReadResult,
    rules: list[FieldRule],
    threshold: float,
    period: tuple[date, date] | None = None,
) -> Problem | None:
    """回傳最該先講的問題：不合格與重拍優先，再來是不確定，最後才是提醒。

    `period` 是保單的保險期間；有給時，`within_policy_period` 的欄位讀出的日期要落在期間內。
    """
    problems: list[Problem] = []
    texts = [line.text for line in read.lines]
    for rule in rules:
        if rule.code != code or not rule.verified:
            continue
        found = matched_lines(rule, texts)
        if found is None:
            problems.append(Problem(rule.severity, rule.missing_reason, rule))
            continue
        if rule.severity == "remind":
            continue  # 只提醒的欄位不因為看不清楚或手寫而擋
        hits = [line for line in read.lines if line.text in found]
        if any(line.confidence < threshold for line in hits):
            problems.append(Problem("failed", f"「{rule.label}」看不清楚，請重拍"))
        elif any(line.handwritten for line in hits):
            problems.append(
                Problem("needs_review", f"「{rule.label}」是手寫，請確認內容正確")
            )
        elif (
            rule.within_policy_period
            and period is not None
            and outside_period(found, *period)
        ):
            problems.append(Problem("needs_review", OUTSIDE_PERIOD.format(rule.label)))
    return min(problems, key=lambda p: _PRIORITY[p.severity], default=None)
