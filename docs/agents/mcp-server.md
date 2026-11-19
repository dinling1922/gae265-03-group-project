# MCP 伺服器：連線說明

後端開放 **MCP 伺服器**（`/mcp`），讓使用者授權的 AI 助理讀取自己的比較紀錄與理賠待辦，並（用有寫入權的權杖）用一句話建立推薦。決策見 [ADR-0032](../adr/0032-mcp-server-with-access-tokens-read-only.md)（唯讀）與 [ADR-0034](../adr/0034-mcp-write-tools-and-phases.md)（寫入、分兩階段），名詞見 [GLOSSARY](../../GLOSSARY.md)「MCP 伺服器」「存取權杖」。與瀏覽器頁面註冊工具的 WebMCP 不同。

## 1. 建立存取權杖

1. 登入網站，進帳號頁的「存取權杖」區塊。
2. 輸入名稱（例如「我的 Claude」），按「建立權杖」。
3. **明文只顯示一次**，立刻複製（`hyi_` 開頭）；關閉或重新整理後就看不到。
4. 建立時預設是**唯讀**；要讓 AI 替你建立比較紀錄，勾選「允許建立資料（write）」。可寫入的權杖只能建立你自己的比較紀錄，不能刪除或送件；LINE 綁定用的權杖一律唯讀。
5. 權杖預設 90 天到期，有效的最多 5 把；不用時按「撤銷」，下一次呼叫就失效。權杖等同密碼，只交給你信任的工具。

沒有畫面時（例如本機開發）可用 API：先登入取得 session，再 `POST /api/v1/me/api-tokens`，body `{"name": "...", "scopes": ["read", "write"]}`（`scopes` 省略時是 `["read"]`；只接受 `["read"]` 或 `["read","write"]`）。

## 2. 設定客戶端

端點：`<網址>/mcp`（本機直接跑後端是 `http://localhost:8000/mcp`；經前端代理時以實際網址為準）。每個請求都要 `Authorization: Bearer <權杖>`，不吃 cookie，不開 CORS。

**Claude Code**（官方做法，自己的電腦上）：

```bash
claude mcp add --transport http hyi http://localhost:8000/mcp \
  --header "Authorization: Bearer <權杖>"
```

只想暫時試用、不改個人設定時，寫一個 JSON 檔再 `claude --mcp-config <檔> --strict-mcp-config`：

```json
{"mcpServers": {"hyi": {"type": "http", "url": "http://localhost:8000/mcp",
  "headers": {"Authorization": "Bearer <權杖>"}}}}
```

權杖別寫進會 commit 的檔案。其他客戶端（Claude Desktop 等）只要支援 streamable HTTP 與自訂標頭，填同樣的網址與標頭即可。

## 3. 工具

| 工具 | 權限 | 輸入 | 回傳 |
|---|---|---|---|
| `list_options` | 唯讀 | 無 | 可用的目的地（16）、在意事項（9）與在意事項上限（3） |
| `create_comparison` | **write** | `request`（使用者的原話，≤500 字）、`overrides`（可選：`destination`、`depart_date`、`return_date`、`concerns`） | 後端 LLM 只做抽取，程式驗證後建立比較紀錄並算出推薦：`status: created`（附 `comparison`、`understood`）或 `needs_more_info`（附 `missing`〔欄位、反問的話、可用選項〕、`unsupported`），資訊不足時不建立 |
| `get_comparison` | 唯讀 | `comparison_id` | 旅行規劃、在意事項、推薦方案與次推薦（`plan_id`、名稱、公司、保額、保費）；還沒有推薦時 `status` 是 `incomplete` |
| `list_comparisons` | 唯讀 | `limit`（1–50，預設 10） | 自己的比較紀錄，最新在前 |
| `get_plan_details` | 唯讀 | `plan_id`、`sum_insured`（選填） | 保障項目與額度、可選保額、保費；不帶保額時只回可選保額 |
| `ask_plan_question` | 唯讀（呼叫 LLM） | `comparison_id`、`question`（≤300 字）、`history`（≤3 輪） | 同網站 1-4 方案問答：依條款回答、附條文出處，不存檔 |
| `list_todos` | 唯讀 | `status`：`pending`（預設）或 `done` | 依行程→事件分組的理賠待辦，附完成數／總數；共同文件獨立一組 |

典型用法：`create_comparison`（一句話）→ `get_plan_details`（用回傳的 `plan_id` 看明細）→ `ask_plan_question`（追問條款）。

`create_comparison` 的行為：
- LLM 只抽取目的地、日期、最多 3 個在意事項；推薦與保費由程式決定，與網頁一致。
- **不猜**：原話沒有明確的日子（只說「下個月」「年底」）、目的地不在選項內、在意事項超過 3 個或重複、天數不在 4–10 天，一律回 `needs_more_info` 並說缺什麼，由 AI 去問使用者。`overrides` 可以讓 AI 直接指定問到的欄位。
- 相對日期依台北時間的今天；「N 天」含首尾；出發日限今天到一年內。
- 不支援的需求（最便宜、年齡、預算）會列在 `unsupported`，不納入推薦。

行為與限制：

- 只回權杖擁有者自己的資料；不存在與不是本人的比較紀錄都回「找不到」。
- 數字（保額、保費、完成度）由程式算出，與網頁一致；LLM 不產生任何數字。唯讀權杖呼叫寫入工具會被拒絕並說明要用有寫入權的權杖。
- 輸出只有允許清單欄位：不含保單號碼、被保險人姓名、檔名、Drive 連結、事件的標題與敘述。沒有範本的待辦（AI 依使用者敘述加的）標題一律顯示為「其他待辦（內容請看網頁）」。
- 每把權杖每分鐘最多 60 個 HTTP 請求（`MCP_RATE_LIMIT_PER_MINUTE`；一次完整互動可能用掉 3 個以上請求）。呼叫後端 LLM 的工具（`create_comparison`、`ask_plan_question`）另有每個工具每分鐘 10 次的上限（`MCP_LLM_TOOL_RATE_LIMIT_PER_MINUTE`）。限流計數在記憶體內，多個 worker 時各算各的。
- 每次工具呼叫記一筆稽核（`mcp_call_log`：權杖、工具名稱、成功與否，不含內容）；被拒絕的請求不記。

錯誤：無權杖、壞權杖、過期、撤銷都是 `401`（不說是哪一種）；權杖沒有 `read` 範圍是 `403`；超過每把權杖的限流是 `429` 加 `Retry-After`；工具層的拒絕（缺 write 權、工具限流）是工具錯誤，訊息會說明原因。

## 4. 本機驗證紀錄（2026-11-18，#476）

用 `make e2e-fake` 同款的設定另起後端（假 5 號、測試登入），建立一個測試使用者與一組權杖，再用 Claude Code（`claude -p`，只載入暫存的 `--mcp-config`）依序呼叫三個工具。結果：

- MCP 連線狀態 `connected`，工具清單含 `list_comparisons`、`get_comparison`、`list_todos`。
- `list_comparisons`：回 1 筆（日本 2026-12-20～12-25，6 天，`has_recommendation: true`）。
- `get_comparison`：`status: complete`；在意事項「突然生病就醫」「班機延誤」；推薦富邦國外旅平險-進階型（保額 600 萬、保費 985）、次推薦基礎型（保費 785），與網頁推薦測試鎖住的數字相同。
- `list_todos`：1 個行程、完成 0／4；事件「班機延誤」下 2 項（含 1 項「其他待辦」），共同文件 2 項。
- 對照：同一組權杖直接打 `/mcp`，有效時 200、不帶權杖 401；在帳號頁 API 撤銷後立即 401。

沒驗到：正式環境（Caddy 轉發 `/mcp`）、Claude Desktop、iPhone／LINE bot、多 worker 的限流。部署等條文 seed 落差處理完再一起做。

## 4b. 階段 A 實測紀錄（2026-11-19，#512）

另起後端（假 5 號、測試登入、**真實 Azure OpenAI**），建立一個測試使用者與兩把權杖（唯讀、可寫入），用 Claude Code（`claude -p`，只載入暫存 `--mcp-config`）實測：

- 連線 `connected`，七個工具都在清單裡。
- **可寫入權杖**：`create_comparison`「12/20 出發，12/24 回來，日本，在意生病與班機延誤」→ `created`，推薦富邦進階型（保額 600 萬、保費 918）、次推薦基礎型（725）；用回傳的 `plan_id` 呼叫 `get_plan_details` 取得保障明細；`ask_plan_question`「班機延誤要怎麼申請理賠？」回答附 FUBON03 第 21、23 條出處。
- **唯讀權杖**：呼叫 `create_comparison` 被拒（「這個工具需要有寫入權（write）的存取權杖…」），`list_comparisons` 仍可用。
- **實測發現的兩個問題（已修）**：
  1. 「我想下個月去東京玩五天」被 LLM 猜成 12/01 就建立了（違反「資訊不足不猜」）。修法：原話沒有日層級的日期說法時，LLM 給的出發日一律當缺漏（程式檢查，不依賴 LLM），並補 prompt 與評估題組。
  2. `get_comparison`／`create_comparison` 回傳的方案沒有 `plan_id`，AI 無法接著呼叫 `get_plan_details`。已補上。
- **抽取評估**（`make eval-trip-extraction`，azure_openai gpt-4.1-mini，報告在 `docs/reports/trip-extraction-2026-11-19-azure_openai.md`）：修前基準 19／21；修後 23 題 × 3 輪 = 69／69。**prompt 是針對這份題組調過的，100% 不代表實際準確度**，需要更多樣的（真人說法）題組。

沒驗到：階段 B（保單、上傳票、事件與待辦建立）尚未開發；LINE bot 不新增寫入指令；正式環境；Claude Desktop；多 worker 的工具限流。

## 5. 常見問題

- **404**：後端容器還是舊程式。照 [開發環境](dev-environment.md) 重新 `make up`，用 `curl -i -X POST localhost:8000/mcp` 確認回 401（有路由）而不是 404。
- **401**：權杖打錯、過期或已撤銷；重新在帳號頁建立。
- 剛 `git pull` 後要先 `make migrate`（`api_token`、`mcp_call_log` 兩張表），見 [sync-after-pull](sync-after-pull.md)。
