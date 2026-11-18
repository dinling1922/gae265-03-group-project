# 6 號合格判定埋錯集

量 6 號（文件辨識）合格規範的**漏判率**與**誤判率**：每一條 `incomplete`（缺了就不合格）的規則，各做完整版、缺欄位版、陷阱版樣本，丟進完整的 `DocumentAgent.recognize`，對照人工寫的正確答案。結果與分析見 [REPORT.md](REPORT.md)。

## 怎麼跑

在 backend/ 底下本機跑（不要用 `make test`：容器掛的是另一個 checkout）：

```bash
cd backend
uv run python ../tools/agent6-qualification-eval/make_samples.py   # 重新產生 samples/*.pdf 與 answers.csv
uv run python ../tools/agent6-qualification-eval/evaluate.py       # 跑評估，印出 Markdown 表格、寫 results.csv
uv run python ../tools/agent6-qualification-eval/evaluate.py --holdout  # 保留集（holdout/），規則修完才跑
uv run python ../tools/agent6-qualification-eval/demo_check.py     # demo 範例的結果跟 demo_baseline.json 比，有變就結束碼 1
```

- **保留集**（`holdout_cases.py` → `holdout/`）：35 份，寫法刻意和主要 58 份不同，修規則時不看、不跑，修完才跑一次，檢查規則是不是只對主要樣本調出來的。保留集失敗時記成「還沒解決的類型」，不回頭為它改規則；要改就另外做一批新的保留集。
- **demo 防護網**（`demo_check.py`）：`backend/app/agents/document/samples/` 每份範例在兩個 demo 情境各跑一次（照片用同名 PDF 的文字當 OCR 結果），跟 `demo_baseline.json` 比 status、種類、missing、found_fields、reason。基準要重記時加 `--save`（只在確定範例的新結果是對的時候）。

- 不需要 `.env`、資料庫、Azure、LLM 或 Google Drive：樣本都是有文字層的 PDF（`ocr=None`），分類不用 LLM 後援（`classify_fallback=None`），雲端用 `DevFakeDrive` 加假授權。
- 規則讀的是 `backend/app/agents/document/qualification_rules.json` 現在的內容，所以修完規則直接重跑 `evaluate.py` 就能比較前後。
- 樣本或答案要改時，改 `cases.py` 再跑 `make_samples.py`；不要直接改 `answers.csv`（會被蓋掉）。

## 檔案

| 檔案 | 內容 |
|---|---|
| `cases.py` | 每份樣本的文字內容、被測規則、版本、正確答案與理由（唯一的來源） |
| `make_samples.py` | 由 `cases.py` 產生 `samples/*.pdf` 與 `answers.csv`；產生後用 pymupdf 讀回來，確認讀出的每一行跟設計的一樣 |
| `answers.csv` | 答案表：`name, code, field, label, version, expected, lang, why` |
| `evaluate.py` | 跑 `recognize`，算漏判率與誤判率（分規則、分版本、分規則 × 版本） |
| `results.csv` | 最近一次的結果：答案、`status`、`missing`、`reason`、`found_fields`、對錯 |
| `REPORT.md` | 第一次執行的結果與分析（修規則前） |
| `REPORT-v2.md` | 修規則後的結果、保留集與 demo 範例比較 |
| `holdout_cases.py`、`holdout/` | 保留集的內容、PDF、答案表與結果 |
| `demo_check.py`、`demo_baseline.json` | demo 範例防護網與修規則前記下的基準 |

## 判定方式

- **正確答案**依照文件的實際內容和規則的用意（規則的 `source`、`missing_reason`、`clauses`，以及 `docs/agents/handoff/agent-6-document.md`）判斷，不是依照目前規則會怎麼判。
- 「判合格」＝ `status` 是 `recognized`；`incomplete`、`needs_review`、`failed` 都算沒判合格。
- **漏判**：答案不合格，卻判合格。漏判率 ＝ 漏判數 ÷ 答案不合格的份數。
- **誤判**：答案合格，卻沒判合格。誤判率 ＝ 誤判數 ÷ 答案合格的份數。
- **擋下但原因不符**：答案不合格、也沒判合格，但不是因為被測的欄位（例如回 `needs_review` 或缺了別的欄位）。不算漏判，另外列出。
- 每份樣本只針對一條規則埋錯，同一份文件的其他必要欄位都寫齊；保險期間設為 2026-07-05～07-11，樣本上的就醫日期都在期間內。

## 版本

- **完整**（應該合格）：中文、英文、日文與不同的日期、金額、欄名寫法。
- **缺欄位**（應該不合格）：直接拿掉那個欄位。
- **陷阱**（應該不合格，但容易被規則誤放）：例如延誤證明只有登機時間與原定起飛時間、收據的「Paid」後面只有卡號末四碼、「診斷：」後面是底線或「見附件」。

陷阱版是**刻意**找規則弱點做的，陷阱版的漏判率代表「這類寫法擋不擋得住」，不是真實上傳文件的漏判比例。
