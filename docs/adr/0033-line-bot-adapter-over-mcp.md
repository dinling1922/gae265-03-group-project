---
status: accepted
---

# LINE bot 轉接層：固定指令、用綁定碼連結帳號、透過 MCP 讀資料

[ADR-0032](0032-mcp-server-with-access-tokens-read-only.md) 開放了唯讀 MCP 伺服器，並預告 LINE bot 轉接層要另做：LINE 本身不是 MCP 客戶端，需要一個接 webhook 的轉接層，把使用者在 LINE 傳來的訊息轉成 MCP 工具呼叫，再把結果回成 LINE 訊息。這份記下轉接層怎麼做、哪些先不做。LINE 官方帳號與 Messaging API 頻道由使用者另外申請，這份不處理。

## 決定

- **放在現有 FastAPI 裡**（`/line/webhook`），不另起服務（[ADR-0001](0001-single-vm-docker-compose-deploy.md)）。不裝 LINE SDK：驗簽（`X-Line-Signature`，頻道密鑰做 HMAC-SHA256，常數時間比對）與回覆 API 各只有幾十行，直接用 `httpx`。LINE 的呼叫包成一個介面，測試用假的，不連 LINE。
- **第一版是固定指令，不用 LLM 理解自由文字**：關鍵字（「比較」「待辦」「說明」「綁定」「解除綁定」）加快速回覆按鈕與圖文選單。理由：數字與內容全由程式與 MCP 工具決定（[ADR-0004](0004-llm-never-produces-numbers.md)）、沒有 LLM 成本與出錯面、Demo 可預期。能聽懂自由文字（用 Azure OpenAI 決定呼叫哪個工具）是之後另開的票，輸出仍由程式組裝。
- **用一次性綁定碼連結 LINE 帳號與使用者**，不另外申請 LINE Login 頻道：使用者在帳號頁按「綁定 LINE」，系統產生 6 位數綁定碼（10 分鐘內有效、只能用一次、同一人重新產生會讓舊的失效），使用者在 LINE 傳「綁定 123456」給 bot。綁定記在新的資料表 `line_binding`（使用者、LINE userId，兩邊各自唯一），不改 `api_token`。綁定碼用後或過期即失效，傳錯有次數限制（防暴力猜測）。
- **轉接層是 MCP 的客戶端，不繞過 MCP**：綁定成功時替該使用者建一把存取權杖（名稱「LINE 綁定」，唯讀），明文用 `TokenCipher` 加密存在 `line_binding`（轉接層每次呼叫要出示它）。呼叫走同一個 `/mcp` 守門層（驗證、限流、稽核都照樣經過），但用行程內的 ASGI 傳輸（`httpx` 的 ASGI transport），不走網路，也不需要對外網址。權杖剩不到 14 天時自動換新一把並撤銷舊的。使用者可以在帳號頁撤銷這把權杖，撤銷後 bot 回「綁定已失效，請重新綁定」。
- **解除綁定**：帳號頁按鈕、傳「解除綁定」、或 LINE 使用者封鎖／刪除官方帳號（`unfollow` 事件）都會刪除綁定並撤銷權杖。刪除我的資料時一併解除（沿用 [ADR-0009](0009-policy-document-personal-data.md) 的方向）。
- **只處理一對一聊天**：群組與多人聊天室一律忽略，只回一次簡短說明，不處理任何指令，避免個人資料被同群的人看到。
- **只用回覆 API，不主動推播**：回覆權杖一分鐘內有效，在 webhook 請求裡處理完就回覆；推播有額度與成本，第一版不做（提醒信已有 Email，[ADR-0031](0031-reminder-email-with-celery-redis.md)）。webhook 以事件 id 去重（LINE 會重送），一律快速回 200。
- **未設定就整個關閉**：`LINE_CHANNEL_SECRET` 與 `LINE_CHANNEL_ACCESS_TOKEN` 都有才啟用；沒設時 webhook 回 404，帳號頁不顯示綁定入口。新增設定同步 `config.py`、`.env.example`、`.env.prod.example`。
- **輸出的資料不增加**：bot 顯示的內容只來自三個 MCP 工具的允許清單輸出（[ADR-0032](0032-mcp-server-with-access-tokens-read-only.md)），不另開欄位。訊息會經過 LINE 平台，所以帳號頁綁定前要說明這一點。

## 取捨與沒選的做法

- **LINE Login（OAuth）綁定**：體驗較順，但要另外申請 LINE Login 頻道、處理回呼與 state，第一版用綁定碼就夠。
- **LLM 理解自由文字**：體驗最好，但第一版先求可預期；MCP 工具與綁定都已就緒，之後只是加一層「選工具」。
- **轉接層用網路呼叫 `/mcp`**：多一個對內網址設定，且 Caddy 之外的路徑要處理；行程內 ASGI 傳輸一樣經過守門層，更簡單。
- **直接呼叫工具函式、不經 MCP**：最省事，但繞過驗證、限流與稽核，也不能證明 MCP 這一層真的可被外部代理使用。
- **用官方 LINE SDK**：多一個相依套件，而我們只用到驗簽與回覆。

## 後果

- 綁定存了一把可用的存取權杖（加密），金鑰外洩的影響和 Google refresh token 同等級，沿用 `TOKEN_ENCRYPTION_KEY`。
- 要連真實 LINE 需要使用者申請頻道，並提供 HTTPS 的 webhook 網址（開發用隧道要先問使用者，正式環境等部署）。真實驗證在這之前只能用假 LINE 客戶端與簽章測試。
- 指令與快速回覆的文案、圖文選單的版面要請設計者確認；第一版只用文字與快速回覆。
