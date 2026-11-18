# ruff: noqa: T201  # 命令列工具，結果直接印出來
"""產生合格判定埋錯集：每份樣本一個有文字層的 PDF，加上答案表 answers.csv。

    cd backend && uv run python ../tools/agent6-qualification-eval/make_samples.py

同時產生兩組：主要的 58 份（cases.py → samples/、answers.csv）與保留集
（holdout_cases.py → holdout/samples/、holdout/answers.csv）。保留集只在規則修完後跑一次。

做法和 backend/app/agents/document/samples/make_samples.py 一樣用 pymupdf 寫文字層，
但字型用 pymupdf 內建的 CJK 字型（china-t／japan），不需要 macOS 的字型檔，哪台機器都能產生。
每一行整行寫入，產生後再用 pymupdf 讀回來比對，確定讀出的每一行跟設計的一樣
（6 號讀電子 PDF 也是用 pymupdf，所以讀回一致就代表 recognize 看到的就是這些字）。
"""

import csv
import sys
from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from cases import CASES, FOOTER, Case  # noqa: E402
from holdout_cases import HOLDOUT  # noqa: E402

FONTS = {"zh": "china-t", "en": "china-t", "ja": "japan"}
FIELDS = ["name", "code", "field", "label", "version", "expected", "lang", "why"]


def render(lines: tuple[str, ...], lang: str) -> bytes:
    doc = pymupdf.open()
    # 內建 CJK 字型的英文字是全形寬，頁面放寬，英文長句才不會超出頁面被截掉
    page = doc.new_page(width=1400, height=600)
    y = 60.0
    for i, line in enumerate((*lines, FOOTER)):
        size = 14 if i == 1 else 9.5
        page.insert_text((40, y), line, fontname=FONTS[lang], fontsize=size)
        y += 26 if i <= 1 else 20
    return doc.tobytes()


def read_back(content: bytes) -> list[str]:
    with pymupdf.open(stream=content, filetype="pdf") as doc:
        text = "\n".join(str(p.get_text("text")) for p in doc.pages())
    return [line for line in text.splitlines() if line.strip()]


def build(cases: list[Case], folder: Path) -> None:
    """產生 folder/samples/*.pdf 與 folder/answers.csv。"""
    names = [c.name for c in cases]
    if len(names) != len(set(names)):
        raise SystemExit("樣本名稱重複")
    samples, answers = folder / "samples", folder / "answers.csv"
    samples.mkdir(parents=True, exist_ok=True)
    for old in samples.glob("*.pdf"):
        old.unlink()
    for case in cases:
        content = render(case.lines, case.lang)
        got = read_back(content)
        want = [*case.lines, FOOTER]
        if got != want:
            raise SystemExit(f"{case.name} 讀回的文字和設計不同：{got!r}")
        (samples / f"{case.name}.pdf").write_bytes(content)
    with answers.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for case in cases:
            writer.writerow({k: getattr(case, k) for k in FIELDS})
    print(f"產生 {len(cases)} 份樣本 → {samples}，答案表 → {answers}")


def main() -> None:
    if {c.name for c in CASES} & {c.name for c in HOLDOUT}:
        raise SystemExit("保留集和主要樣本的名稱重複")
    build(CASES, HERE)
    build(HOLDOUT, HERE / "holdout")


if __name__ == "__main__":
    main()
