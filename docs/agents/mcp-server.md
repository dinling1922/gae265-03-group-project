# MCP 伺服器：連線說明

後端開放唯讀的 **MCP 伺服器**（`/mcp`），讓使用者授權的 AI 助理讀取自己的比較紀錄與理賠待辦。決策見 [ADR-0032](../adr/0032-mcp-server-with-access-tokens-read-only.md)，名詞見 [GLOSSARY](../../GLOSSARY.md)「MCP 伺服器」「存取權杖」。與瀏覽器頁面註冊工具的 WebMCP 不同。

## 1. 建立存取權杖

1. 登入網站，進帳號頁的「存取權杖」區塊。
2. 輸入名稱（例如「我的 Claude」），按「建立權杖」。
3. **明文只顯示一次**，立刻複製（`hyi_` 開頭）；關閉或重新整理後就看不到。
4. 權杖預設 90 天到期，有效的最多 5 把；不用時按「撤銷」，下一次呼叫就失效。權杖等同密碼，只交給你信任的工具。

沒有畫面時（例如本機開發）可用 API：先登入取得 session，再 `POST /api/v1/me/api-tokens`，body `{"name": "..."}`。

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

## 3. 三個工具（都是唯讀）

| 工具 | 輸入 | 回傳 |
|---|---|---|
| `list_comparisons` | `limit`（1–50，預設 10，超出會被拒絕） | 自己的比較紀錄，最新在前：id、目的地、日期、天數、是否已有推薦 |
| `get_comparison` | `comparison_id` | 旅行規劃、在意事項、推薦方案與次推薦（名稱、公司、保額、保費）；還沒有推薦時 `status` 是 `incomplete` 並說明缺哪一步 |
| `list_todos` | `status`：`pending`（預設）或 `done` | 依行程→事件分組的理賠待辦，附完成數／總數；共同文件獨立一組 |

行為與限制：

- 只回權杖擁有者自己的資料；不存在與不是本人的比較紀錄都回「找不到」。
- 數字（保額、保費、完成度）由程式算出，與網頁一致；工具不呼叫 LLM、不寫入。
- 輸出只有允許清單欄位：不含保單號碼、被保險人姓名、檔名、Drive 連結、事件的標題與敘述。沒有範本的待辦（AI 依使用者敘述加的）標題一律顯示為「其他待辦（內容請看網頁）」。
- 每把權杖每分鐘最多 60 個 HTTP 請求（`MCP_RATE_LIMIT_PER_MINUTE`；一次完整互動可能用掉 3 個以上請求）。限流計數在記憶體內，多個 worker 時各算各的。
- 每次工具呼叫記一筆稽核（`mcp_call_log`：權杖、工具名稱、成功與否，不含內容）；被拒絕的請求不記。

錯誤：無權杖、壞權杖、過期、撤銷都是 `401`（不說是哪一種）；權杖沒有 `read` 範圍是 `403`；超過限流是 `429` 加 `Retry-After`。

## 4. 本機驗證紀錄（2026-11-18，#476）

用 `make e2e-fake` 同款的設定另起後端（假 5 號、測試登入），建立一個測試使用者與一組權杖，再用 Claude Code（`claude -p`，只載入暫存的 `--mcp-config`）依序呼叫三個工具。結果：

- MCP 連線狀態 `connected`，工具清單含 `list_comparisons`、`get_comparison`、`list_todos`。
- `list_comparisons`：回 1 筆（日本 2026-12-20～12-25，6 天，`has_recommendation: true`）。
- `get_comparison`：`status: complete`；在意事項「突然生病就醫」「班機延誤」；推薦富邦國外旅平險-進階型（保額 600 萬、保費 985）、次推薦基礎型（保費 785），與網頁推薦測試鎖住的數字相同。
- `list_todos`：1 個行程、完成 0／4；事件「班機延誤」下 2 項（含 1 項「其他待辦」），共同文件 2 項。
- 對照：同一組權杖直接打 `/mcp`，有效時 200、不帶權杖 401；在帳號頁 API 撤銷後立即 401。

沒驗到：正式環境（Caddy 轉發 `/mcp`）、Claude Desktop、iPhone／LINE bot、多 worker 的限流。部署等條文 seed 落差處理完再一起做。

## 5. 常見問題

- **404**：後端容器還是舊程式。照 [開發環境](dev-environment.md) 重新 `make up`，用 `curl -i -X POST localhost:8000/mcp` 確認回 401（有路由）而不是 404。
- **401**：權杖打錯、過期或已撤銷；重新在帳號頁建立。
- 剛 `git pull` 後要先 `make migrate`（`api_token`、`mcp_call_log` 兩張表），見 [sync-after-pull](sync-after-pull.md)。
