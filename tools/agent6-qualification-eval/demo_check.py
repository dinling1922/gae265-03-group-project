# ruff: noqa: T201  # 命令列工具，結果直接印出來
"""demo 範例防護網：改規則前後，backend/app/agents/document/samples/ 每一份範例的辨識結果都不能變。

    cd backend && uv run python ../tools/agent6-qualification-eval/demo_check.py --save   # 改規則前：記下基準
    cd backend && uv run python ../tools/agent6-qualification-eval/demo_check.py          # 改規則後：跟基準比

每份範例在 /dev 逐步展示的兩個情境（flight_delay、medical，`walkthrough.sample_context`）各跑一次
`DocumentAgent.recognize`。照片沒有 OCR 可讀（不呼叫 Azure），另外用同名 PDF 讀出的文字當 OCR 結果
（photo_med_receipt → med_receipt 等），模擬 demo 時 Azure 讀出的字，確認規則改動不影響照片。
記錄 status、document_kind、missing、found_fields、reason；有任何一項不同就列出來、結束碼 1。
"""

import asyncio
import json
import sys
import uuid
from pathlib import Path

import pymupdf
from app.agents.document import DocumentAgent
from app.agents.document.reader import Line, ReadResult
from app.agents.document.rules import load_rules
from app.agents.document.storage import DevFakeDrive
from app.agents.document.walkthrough import SAMPLES_DIR, SCENARIOS, sample_context
from app.contracts import UploadedFile, UserContext

HERE = Path(__file__).resolve().parent
BASELINE = HERE / "demo_baseline.json"
USER = UserContext(user_id=uuid.uuid4(), email="eval@example.com")
# 照片 → 拍的是哪一份 PDF（用 PDF 的文字當 OCR 結果）
PHOTO_SOURCES = {
    "photo_boarding_pass.jpg": "boarding_pass.pdf",
    "photo_claim_form.jpg": "claim_form.pdf",
    "photo_delay_glare.jpg": "delay.pdf",
    "photo_med_receipt.jpg": "med_receipt.pdf",
    "photo_ticket_blurry.jpg": "ticket.pdf",
}


class FakeGoogle:
    async def access_token(self, user: UserContext, scope: str) -> str | None:
        return "eval-fake-token"

    async def drive_folder_id(self, user: UserContext) -> str | None:
        return None


class TextOcr:
    """假 OCR：回傳同名 PDF 讀出的文字，信心度 0.97。"""

    def __init__(self, pdf: Path) -> None:
        with pymupdf.open(pdf) as doc:
            text = "\n".join(str(p.get_text("text")) for p in doc.pages())
        self.result = ReadResult(
            [Line(t, 0.97) for t in text.splitlines() if t.strip()], "ocr"
        )

    async def read(self, content: bytes, content_type: str) -> ReadResult:
        return self.result


async def run() -> dict[str, dict[str, object]]:
    rules = load_rules()
    out: dict[str, dict[str, object]] = {}
    files = sorted(
        p for p in SAMPLES_DIR.iterdir() if p.suffix.lower() in (".pdf", ".jpg")
    )
    for path in files:
        source = PHOTO_SOURCES.get(path.name)
        ocr = TextOcr(SAMPLES_DIR / source) if source else None
        agent = DocumentAgent(ocr=ocr, drive=DevFakeDrive(), rules=rules, threshold=0.8)
        content_type = (
            "image/jpeg" if path.suffix.lower() == ".jpg" else "application/pdf"
        )
        for scenario in SCENARIOS:
            r = await agent.recognize(
                sample_context([], scenario),
                UploadedFile(
                    file_name=path.name,
                    content_type=content_type,
                    content=path.read_bytes(),
                ),
                USER,
                FakeGoogle(),
            )
            out[f"{path.name} @ {scenario}"] = {
                "status": r.status,
                "failure": r.failure,
                "document_kind": r.document_kind,
                "missing": r.missing,
                "found_fields": r.found_fields,
                "reason": r.reason,
            }
    return out


def main() -> None:
    results = asyncio.run(run())
    if "--save" in sys.argv[1:]:
        BASELINE.write_text(
            json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"記下 {len(results)} 筆基準 → {BASELINE}")
        return
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    changed = [
        k
        for k in sorted(set(baseline) | set(results))
        if baseline.get(k) != results.get(k)
    ]
    for key in changed:
        print(f"改變：{key}\n  基準：{baseline.get(key)}\n  現在：{results.get(key)}")
    print(f"比對 {len(results)} 筆，改變 {len(changed)} 筆")
    sys.exit(1 if changed else 0)


if __name__ == "__main__":
    main()
