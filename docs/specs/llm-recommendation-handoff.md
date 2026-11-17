# 1-3 LLM 推薦：實際 LLM 驗收交接

給接手驗收的 session。規格見 spec #457（`gh issue view 457`）、[ADR-0032](../adr/0032-recommendation-by-llm-need-levels.md)、[討論共識與試算](llm-recommendation-consensus.md)；名詞見 GLOSSARY 的「需求等級」「推薦方案」「對照方案」「推薦保額」。

## 目前狀態（2026-11-17）

- 實作在 branch `feat/llm-recommendation-spec`（commit `a443bee`），**未 push、未開 PR**。
- 開發資料庫已 `make migrate` 到 `3c1e9b7d5a20`：舊規則選的推薦已清空，下次 1-2 存檔時依新規則補算。
- `make test` 2744 passed；`make lint` 通過；`make e2e` 全過（完整跑時 4 個在負載下偶發失敗，`--last-failed` 重跑都過）。
- **還沒做**：用真的 LLM（`.env` 的 Azure）驗收需求等級與推薦分布，也就是這份文件要做的事。

## 實作重點（驗收時要知道的）

- 呼叫時機：1-2 按「下一步」的 PATCH。日程**沒有活動時不呼叫 LLM**，直接用預設等級（第 1 名越高越好、其餘有就好）。
- 退回預設等級的情況：LLM 例外、超過 `RECOMMEND_LLM_TIMEOUT`（15 秒）、concern 和使用者選的不符或重複、原因含日程以外的數字。退回時 log 有 `判斷需求等級失敗`。
- 原因的數字白名單：送給 LLM 的日程文字（含 ISO 日期、時間、時長）加上「第 1 天」到「第 N 天」。原因字數（40 字）只寫在 prompt，程式不檢查（2026-11-17 決定）。
- 推薦保額不另外存：讀取時用存下的需求等級重跑一次選法取回。調整門檻設定後，舊紀錄顯示的推薦保額可能改變。
- 程式碼：`backend/app/domain/need_levels.py`（prompt、驗證、退回）、`backend/app/domain/recommendation.py`（`pick`、推薦理由）、`backend/app/api/routes/comparisons.py`（`_recommend`）。

## 驗收步驟

前置：`mise exec -- make doctor` 全部 PASS，且顯示 `LLM_PROVIDER=azure_openai`。

1. 在 `http://localhost:5173` 建新的比較，旅遊日程頁側欄「載入範例日程 ▾」選一份（見下表）。
2. 進 1-2，選表中的在意事項，按「下一步」（會等 LLM，最多 15 秒）。
3. 在 1-3 的「推薦理由與依據」檢查：
   - 需求等級清單每項都有括號原因 → 走了 LLM；沒有括號、理由是「依你排的順序…」→ 退回預設等級，查 log。
   - 有明顯對應活動的在意事項是「越高越好」，一般風險是「有就好」。
   - 原因點名日程活動、沒有金額、大約一行。
   - 推薦與次推薦是不同方案。
4. 記錄結果到下方表格。

| 範例日程 | 在意事項（依序） | 預期 |
|---|---|---|
| 大阪＋京都 5 天（主 demo） | 突然生病就醫、信用卡盜刷、班機延誤 | 生病、盜刷「越高越好」（D1 生蠔、D3 採購），延誤「有就好」 |
| 大阪＋京都 5 天（主 demo） | 不小心弄傷別人或弄壞東西、行李延誤 | 弄傷別人「越高越好」（D2 租和服、陶藝） |
| 驗收：需要緊急送醫或家屬趕來 | 需要緊急送醫或家屬趕來、班機延誤 | 緊急送醫「越高越好」 |
| 驗收：行程取消或更改 | 行程取消或更改、突然生病就醫 | 行程取消「越高越好」（颱風季） |
| 驗收：證件遺失 | 證件遺失、信用卡盜刷 | 證件遺失「越高越好」 |
| 驗收：行李延誤、行李遺失或損壞 | 行李延誤、行李遺失或損壞 | 至少一項「越高越好」 |

另外各做一次：

- 改日程（新增或刪除一個活動）→ 回 1-2 按「下一步」：推薦重算，原因跟著變。
- 在 1-3 改保額：推薦方案不變。
- 同一份日程連跑 2～3 次：需求等級是否穩定。

## 查詢指令

```bash
# 退回預設等級的原因
docker compose logs --tail=200 backend | grep -A20 "判斷需求等級失敗"
```

```bash
# 最近的推薦與來源
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT updated_at, concerns, recommendation_source, concern_levels, recommended_plan_code, recommended_sum_insured, alternative_plan_code FROM comparison ORDER BY updated_at DESC LIMIT 10;"'
```

## 記錄（驗收時填）

| 範例日程 | 在意事項 | 來源（llm/default） | 需求等級與原因 | 推薦方案／保額／保費 | 對照方案 | 合理？ |
|---|---|---|---|---|---|---|
| | | | | | | |

## 驗收後要決定的事

- **推薦分布**：是否幾乎都推華南基礎型。若集中，討論調 `RECOMMEND_ENOUGH_RATIO`（0.5）、`RECOMMEND_HIGH_RATIO`（0.8），見 spec #457 的 Out of Scope「依推薦分布調整門檻」。
- **原因長度**：若常超過一行，改 prompt 或畫面截斷（不在程式擋，理由見上）。
- **退回比例**：若常因「原因含數字」退回，檢查是哪種寫法被擋，再決定是否放寬 `need_levels.py` 的 sources。
- 驗收通過後：push branch、開 PR（Closes #457）。

## 尚未處理的 review 意見（可選）

- `recommendation.py` 的 `_select` 與 `pick` 名稱太像。
- `need_levels.py` 在 domain 內吞例外，`concern_suggestions` 則在路由層處理，兩處 LLM 錯誤處理位置不一致。
- spec 要求的「mock 模式 e2e 確認需求等級清單」沒有做；現有 e2e 跑真後端、只涵蓋預設等級（無原因括號）。
