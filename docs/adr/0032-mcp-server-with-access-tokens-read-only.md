---
status: accepted
---

# 後端開放唯讀的 MCP 伺服器，用存取權杖識別使用者，輸出只給允許清單的欄位

要讓外部 AI 助理（之後是 LINE bot 轉接層）能讀使用者自己的資料，第一版至少做到「列出比較方案、列出待辦事項」。現有的登入只有 Google OAuth 加 cookie session（[ADR-0008](0008-google-oauth-cookie-session.md)、[ADR-0020](0020-google-login-and-incremental-authorization.md)），MCP 客戶端沒有瀏覽器 cookie，所以要先決定身分怎麼帶、資料開到多少。這個 MCP 是**後端服務**，和 [ADR-0031（旅遊日程）](0031-itinerary-replaces-trip-background-step.md) 提到、由瀏覽器頁面註冊工具的 WebMCP 是兩件事。

## 決定

- **身分用存取權杖**：使用者在帳號頁建立，MCP 客戶端帶 `Authorization: Bearer`。資料表 `api_token` 只存雜湊，另存前綴、名稱、建立時間、最後使用時間、到期、撤銷時間，並預留 `scopes`，目前只有 `read`。預設 90 天到期，一人最多 5 把，明文只在建立時顯示一次。撤銷與到期在每次呼叫時檢查，不做快取。
- **掛在現有 FastAPI 的 `/mcp`**：用官方 `mcp` SDK 的 streamable HTTP（`pyproject.toml` 已有這個依賴，實作時確認版本與 API），工具直接呼叫現有的領域函式與資料庫，不另起服務（[ADR-0001](0001-single-vm-docker-compose-deploy.md)）。這條路徑只收 Bearer、不吃 cookie session，不開 CORS。每把權杖限流；稽核只記權杖、工具名稱、時間、成功或失敗，不記回傳內容。
- **第一版只讀，三個工具**：
  - `list_comparisons(limit=10)`：自己的比較紀錄，最新在前。
  - `get_comparison(comparison_id)`：旅行規劃、在意事項、推薦方案與次推薦（名稱、保額、保費）。沒有推薦結果就回 `status: "incomplete"` 加缺哪一步，不替使用者觸發計算。
  - `list_todos(status?)`：沿用 `policy_todos` 的規則（未確認事件不算、不符保障事件的文件類待辦反灰不計），依行程→事件分組並附完成數／總數，預設只回未完成。
- **工具不呼叫 LLM、不寫入**：所有數字由程式算出（[ADR-0004](0004-llm-never-produces-numbers.md)）；`concern-suggestions` 會呼叫 LLM，不開放。勾選待辦、建立比較、送件、條款問答都不在第一版，理賠送件涉及 Drive 與 Gmail 授權（[ADR-0020](0020-google-login-and-incremental-authorization.md)、[ADR-0025](0025-claim-submission-fill-before-send.md)），要另做設計。
- **輸出用允許清單**：工具回傳的內容會進第三方 LLM 或 LINE 平台，而保單屬個人資料（[ADR-0009](0009-policy-document-personal-data.md)）。只回目的地、日期、推薦方案名稱與保費、待辦標題／階段／狀態；不回保單號碼、被保險人姓名、文件檔名、Drive 連結、事件的自由文字敘述。輸出模型另寫一組，不重用現有 API 的回應模型，避免日後 API 加欄位時不小心外洩。找不到與不是本人的資料一律回「找不到」。

## 取捨與沒選的做法

- **OAuth 2.1（MCP 規格的標準授權）**：客戶端可以一鍵連線，但實作與驗證成本高，Demo 用不到。工具層與身分取得解耦，之後客戶端需要時再升級，不用改工具。
- **全站共用服務金鑰加 `user_id` 參數**：持有金鑰者可以讀任何人的資料，不選。
- **獨立的 MCP 服務**：多一份部署與監控，單一 VM 不值得；代價是 MCP 與主 API 同生共死，這個規模可以接受。
- **輸出跟畫面一樣全回**：對 AI 助理更方便，但個資外洩面太大，不選。

## 後果

- 外部代理能讀到的資料只有上述三個工具的允許清單欄位；要多給欄位須改輸出模型並更新這份 ADR。
- LINE bot 不是 MCP 客戶端，需要自己的 bot 伺服器接 webhook 再呼叫 MCP；另開票，身分設計保留「LINE userId ↔ 使用者」綁定的空間（例如另一張綁定表，不改 `api_token`）。
- 正式環境部署（Caddy 多代理 `/mcp`）等條文 seed 落差處理完再一起做。
