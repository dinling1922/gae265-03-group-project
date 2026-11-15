---
status: proposed
---

# 7 號送件前小助理：LLM 只寫總結與提議修正，執行由使用者按下

延伸 [ADR-0029](0029-trip-level-claim-submission-and-shared-documents.md)（契約 v2.14 的 `prepare_trip`）與 [ADR-0025](0025-claim-submission-fill-before-send.md)（送件流程 ①②③）。7 號提案讓小助理成為 agent：依送件狀況決定往下查什麼，寫一段總結，並提出使用者可以一鍵執行的修正。

## 決定

- **契約 2.15.0（只新增、選配）**：`prepare_trip` 多選填 `mode`；`PackagePreparation` 多 `summary`、`actions`；新選配協定 `TripPackageFixer.apply_action`。沒實作的 7 號照常能用。
- **分工**：
  - 程式收集送件狀況、產生 `reminders` 與 `actions`（穩定、不重複畫面）、確認每個修正成立。
  - LLM 可以用 `create_agent` 搭唯讀工具決定往下查什麼（延續 [ADR-0024](0024-incident-agent-v1-program-decides-conclusions.md) 問答的做法），並寫 `summary`。它只能「提議」修正，不能執行。
  - 修正的流程：LLM 提議 → 程式確認 → 使用者按下 → 執行前程式再確認。寄信永遠由使用者決定。
- **只對 `summary` 放寬兩條規則**：字句可以不同（內容依據相同）；可以提到畫面已提示的事。`reminders`、`actions` 不放寬。
- **數字**：`summary` 的數字必須能在程式給的資料裡找到（ADR-0004 白名單），不通過就回 `None`。
- **隱私**：LLM 只看事故名稱、待辦名稱、附件檔名、空格的項目名稱與授權狀態；不看事故經過、facts 與空格填的值（[ADR-0009](0009-policy-document-personal-data.md)）。
- **第一版修正只有 `restore_file`**：把 Google Drive 垃圾桶裡的附件移出來。檔案是 6 號用同一個應用程式的 `drive.file` 上傳的（[ADR-0021](0021-claim-documents-in-user-google-drive.md)），7 號用同一組授權即可還原；還原後檔案編號不變，不寫資料庫，6 號不用改。
- **失敗**：`prepare_trip` 失敗回空的 `summary` 與 `actions`、`reminders` 照常，不丟例外；`apply_action` 失敗回 `ok=False`。每輪限制 LLM 呼叫次數與總時間；平台另加逾時由平台決定。

## Considered Options

- **LLM 直接執行修正**：少一次點擊，但使用者沒同意就動 Drive，也違反 ADR-0015「agent 回傳結果、由平台與使用者決定」。沒採用。
- **整個總結由程式模板產生**：完全穩定、最便宜（約 2 秒），但要整合多種狀況時句子很僵。保留為 LLM 失敗或數字不通過時的退路。
- **把還原放在 6 號**：6 號是存檔的人，但兩個 agent 多一層依賴；ADR-0021 已決定由 7 號自己取檔。沒採用。

## Consequences

- 平台新增 `POST /policies/{id}/package/actions/{action_id}`；`prepare` 多 `mode` query。前端 F4 在 ①②③ 呼叫 prepare，修正後重新檢查；② 按「還原」後不能直接結案。
- 每個送件畫面多一次 LLM 呼叫（預估每輪 4～8 秒，無問題約 2 秒）：前端不要讓它擋住按鈕。
- 序號更正為半形「(2)」，見 contract-changelog 2.15.0。
- 待驗：`drive.file` 對垃圾桶檔案的 `files.update(trashed=false)` 要用真實帳號確認一次（含整個資料夾在垃圾桶的情況）。
