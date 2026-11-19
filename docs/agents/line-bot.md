# LINE bot：連線說明

LINE bot 轉接層（[ADR-0033](../adr/0033-line-bot-adapter-over-mcp.md)）讓使用者在 LINE 一對一聊天傳固定指令，查自己的比較紀錄與理賠待辦。bot 本身是 [MCP 伺服器](mcp-server.md) 的客戶端，只讀。名詞見 [GLOSSARY](../../GLOSSARY.md)「LINE bot 轉接層」「LINE 綁定」「綁定碼」。

## 指令

| 傳這個 | 做什麼 |
|---|---|
| `說明`（加好友時也是） | 選單卡：三個按鈕（我的比較、理賠待辦、使用說明），降級時回純文字說明 |
| 任何不認得的內容 | 純文字指令說明 |
| `綁定 123456` | 用帳號頁產生的綁定碼綁定（6 位數、10 分鐘內有效、只能用一次） |
| `比較` | 最近的比較紀錄（最新在前，最多 10 筆） |
| `比較 2` | 第 2 筆的推薦方案與次推薦（編號以最新列表為準） |
| `待辦` / `待辦 已完成` | 未完成／已完成的理賠待辦，依行程與事件分組 |
| `解除綁定` | 解除綁定並撤銷那把存取權杖 |

群組與多人聊天室不處理任何指令（只回一次簡短說明）。封鎖 bot 會自動解除綁定。回覆文案都是暫定，位置在 `backend/app/line_bot/texts.py` 與 `commands/*.py`。

## Flex 卡片與 postback（[ADR-0035](../adr/0035-line-flex-cards-and-rich-menu.md)）

回覆可以是 Flex 卡片；卡片按鈕用 postback，不會在聊天室多出使用者沒打的字。目前只有「說明」選單卡（FLEX-1），比較卡、待辦卡是 FLEX-2、FLEX-3。

**組裝**：`backend/app/line_bot/flex.py` 放共用積木（`bubble`、`text`、`box`、`postback_button`、`uri_button`、`carousel`、`postback_data`、`check_contents`）與 LINE 上限常數；它只管排版，不放任何一張卡的內容。內容只來自既有文字或 MCP 工具輸出，不查資料庫、不產生數字（ADR-0004）。

**送出**：處理器呼叫 `ctx.reply_flex(alt_text, contents, fallback_text=None, quick_replies=())`，一次多則用 `ctx.reply_flex_messages([(alt_text, contents), ...], fallback_text=..., quick_replies=...)`（最多 5 則）。`contents` 可以直接給 dict，也可以給回傳 dict 的函式（組裝丟例外也會降級）。`alt_text` 用既有的純文字摘要（LINE 限 400 字，超過會截短）。回覆權杖一個事件只能用一次，和 `ctx.reply` 共用。

**降級規則**：組裝失敗、超過上限（單則 JSON 50 KB、輪播 12 張、一次回覆 5 則）、或 LINE 回錯誤（4xx，`LineClient.reply` 回 `False`）時，改回 `fallback_text` 這則純文字（沒給就用 `alt_text`），使用者一定有回應。`fallback_text` 請給原本純文字指令的回覆，這樣降級後和 Flex 之前完全一樣。

**postback data 格式**：`cmd=<指令>&<鍵>=<值>`（URL query），整串最多 300 字，只放指令與識別碼，不放個資。例如 `cmd=compare&id=<比較紀錄 uuid>`。用 `flex.postback_data("compare", id=...)` 組、`flex.parse_postback_data` 解析。`cmd` 的對應在 `registry.POSTBACK_COMMANDS`（`compare`→比較、`todo`→待辦、`help`→說明），也可以直接寫指令關鍵字。

**處理順序**：postback 事件和文字訊息一樣先驗簽、用事件 id 去重（同一個事件重送不會重複回覆），再找到**同一個指令處理器**執行：`ctx.text` 是指令關鍵字、`ctx.args` 是空字串、按鈕帶的其他鍵值在 `ctx.postback`（例如 `ctx.postback.get("id")`，沒有就是空 dict）。所以處理器不必分辨是打字還是按鈕；要支援帶 id 的按鈕，在處理器裡先看 `ctx.postback`。`cmd` 不認得或 data 格式錯誤，回選單卡。群組與聊天室的 postback 一律忽略。

**新增一張卡**（FLEX-2、FLEX-3）：
1. 在 `line_bot/cards/` 新增自己的檔案（例如 `cards/compare.py`），用 `flex` 積木寫「資料 → bubble／carousel」的函式；文案放在自己檔案裡，標註暫定。
2. 在對應的 `commands/*.py` 處理器裡，把原本的 `ctx.reply(text, ...)` 改成 `ctx.reply_flex(summary_text, lambda: build_card(data), fallback_text=text, quick_replies=...)`。純文字的組法不要動，它就是降級後的回覆。
3. 按鈕用 `flex.postback_button(label, flex.postback_data("compare", id=...))`；`compare`、`todo`、`help` 已預先對好指令，不必改 `registry.py`。新的指令代號才需要在 `POSTBACK_COMMANDS` 加一行。
4. 測試：用 `tests/line_support.py` 的 `FakeLineClient`（`line.flex` 是送出的 Flex、`FakeLineClient(reject_flex=True)` 模擬 LINE 拒絕）與 `postback_event(...)`，範例見 `tests/test_line_flex.py`。

## 你需要提供的東西

1. **LINE 官方帳號與 Messaging API 頻道**：到 [LINE Developers Console](https://developers.line.biz/console/) 建立 Provider，再建立 Messaging API 頻道（會同時建立 LINE 官方帳號）。
2. **Channel secret**：頻道的「Basic settings」分頁。
3. **Channel access token（long-lived）**：頻道的「Messaging API」分頁最下面按「Issue」。
4. **HTTPS 的 webhook 網址**：開發時用隧道（見下）；正式環境等部署。
5. `TOKEN_ENCRYPTION_KEY`：`.env` 裡本來就有（加密 Google refresh token 用，綁定的權杖也用它）；沒設時帳號頁產生綁定碼會回 503。產生方式：`uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`。

## 設定與啟動（本機）

1. 在 `.env` 填：
   ```
   LINE_CHANNEL_SECRET=<Channel secret>
   LINE_CHANNEL_ACCESS_TOKEN=<Channel access token>
   ```
   兩個都填才啟用；沒填時 `/line/webhook` 回 404、帳號頁不顯示「綁定 LINE」。選用：`LINE_BIND_MAX_FAILURES`、`LINE_BIND_FAILURE_WINDOW_MINUTES`、`LINE_BIND_GLOBAL_MAX_FAILURES`（防暴力猜測，預設 5／10 分鐘／100）。
2. **重開 `make up`**：compose 只在啟動時讀 `.env`，改了不重開，容器看不到新設定。
3. 拉了新程式後先 `make migrate`（`line_event`、`line_binding`、`line_bind_code`、`line_bind_failure` 四張表，見 [sync-after-pull](sync-after-pull.md)）。
4. 確認路由有在：`curl -i -X POST http://localhost:8000/line/webhook` 沒設定 LINE 時是 404；設定後沒有簽章是 `401`。

## 開隧道接 webhook（開發用）

webhook 在**後端**的 `/line/webhook`（不是前端、不在 `/api/v1` 底下），所以隧道要指到後端的 8000 埠：

```bash
ngrok http 8000
```

把 ngrok 給的 `https://xxxx.ngrok-free.app` 加上路徑，填到 LINE Developers Console 頻道「Messaging API」分頁的 Webhook URL：

```
https://xxxx.ngrok-free.app/line/webhook
```

接著：

1. 按「Verify」，應顯示 Success（後端對空事件回 200）。
2. 打開「Use webhook」。
3. 到 LINE 官方帳號的設定（LINE Official Account Manager 的「回應設定」）把「自動回應訊息」與「加入好友的歡迎訊息」關掉，否則會和 bot 的回覆重複。
4. 免費的 ngrok 網址每次重開都會變，要回 Console 更新 Webhook URL。隧道會讓外面的人打到你的開發後端，用完就關掉；每個請求都有簽章驗證，簽章不對一律 401。

## 驗證步驟

1. 用手機加 bot 好友 → 收到歡迎選單卡；按「我的比較」「理賠待辦」「使用說明」和打字的結果相同，聊天室不會多出你沒打的字。
2. 登入網站，帳號頁「綁定 LINE」按「產生綁定碼」。
3. 在 LINE 傳 `綁定 <碼>` → 收到綁定成功；帳號頁會自動切到「已綁定」。
4. 傳 `比較`、`比較 1`、`待辦` → 內容要和網頁一致（保額、保費、完成度）。
5. 傳 `解除綁定` → 帳號頁回到未綁定；帳號頁的「存取權杖」列表裡，名稱「LINE 綁定」的那把變成已撤銷。

## 實測紀錄（2026-11-19）

使用者用真實 LINE 官方帳號與 ngrok（隧道指到後端 8000）依上面的「驗證步驟」1 到 5 全部測過，全部通過：Verify、加好友歡迎訊息、帳號頁綁定碼綁定（帳號頁自動切到已綁定）、`比較`／`比較 1`／`待辦`、`解除綁定`。

沒驗到：快速回覆在不同手機上的呈現細節、群組與聊天室的行為、封鎖 bot（`unfollow`）自動解除、權杖自動換新（剩不到 14 天）、正式環境（HTTPS 網址與部署）。

## 常見問題

- **Verify 失敗或 404**：兩個 LINE 設定沒填、沒重開 `make up`，或隧道指到 5173 而不是 8000。
- **401**：Channel secret 填錯（簽章驗不過）。
- **bot 沒反應**：看後端 log（`docker compose logs backend`）；回覆用的 Channel access token 失敗只會記 log（只記狀態碼，不記內容），常見是過期或填錯。
- **綁定碼回 503**：`TOKEN_ENCRYPTION_KEY` 沒設。
- **回「綁定已失效」**：帳號頁的「存取權杖」裡把「LINE 綁定」那把撤銷了，或已到期；重新綁定即可。
- **有效權杖已滿 5 把**：綁定也會建一把存取權杖，先到帳號頁撤銷不用的再綁。
- **重複回覆**：LINE 官方帳號的自動回應沒關。

## 安全與限制

- 訊息會經過 LINE 平台；bot 只輸出 MCP 三個工具的允許清單欄位，不含保單號碼、姓名、檔名、事件敘述。
- 綁定碼只存雜湊（HMAC），傳錯有次數限制；綁定存的權杖是加密的（TokenCipher）。
- 只用回覆，不主動推播（提醒信仍走 Email）。
- 目前不支援自由文字（沒有 LLM）、Flex 訊息與圖文選單。
