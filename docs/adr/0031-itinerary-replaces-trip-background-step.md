---
status: accepted
---

# 旅遊日程頁取代 1-1，日程併入比較紀錄、不收年齡

為了展示 WebMCP（瀏覽器裡的 agent 透過頁面註冊的工具讀寫資料），參考 WanderNote 做了逐時旅遊日程頁，並由 PM 決定以它取代「出發前：幫我選方案」的 1-1（輸入旅遊背景）。之後的 1-2 會依日程內容建議在意事項（另開 branch）。

## 決定

- **日程是比較紀錄的一部分**：一筆比較紀錄恰好一份旅遊日程，不建 `itinerary` 表。`comparison` 加 `city`，日程活動存 `itinerary_activity`，以 `comparison_id` 掛在比較紀錄下；`(comparison_id, date, hour)` unique，一個時段一項。
- **建立時機**：`/compare/new` 是空白的日程頁，資料庫還沒有資料；目的地與日期都有值的那一刻（人填表或 agent 呼叫 `set_trip_details`）建立比較紀錄，網址以 replace 換成 `/compare/$id/itinerary`，之後的修改即時存檔。不在進入頁面時建立，避免產生空的比較紀錄。`/compare/$id/background` 導向 `/compare/$id/itinerary`。
- **拿掉年齡**：`comparison.age` 只用於驗證、存檔與結果頁標題，推薦與保費都不看，用 migration 刪除欄位。
- **縮短日期**：範圍外的日程活動在同一個 transaction 刪除；人改時跳確認視窗，agent 改時工具先回錯誤列出會移除的活動，使用者同意後帶 `confirm_remove` 才執行（只靠工具說明，agent 不一定會照做）。
- **agent 由瀏覽器提供**：網站只註冊 WebMCP 工具、不呼叫 LLM 來排日程；地點與座標由 agent 給，人工新增的活動沒有座標、不上地圖。

## Considered Options

- **日程存 localStorage**：不用改後端，但一筆比較紀錄配一份日程只在同一個瀏覽器成立，換裝置就讀不到。不採用。
- **獨立的 `itinerary` 表，比較紀錄以外鍵連結**：日程先於比較紀錄存在時需要，但兩者一對一且同時建立，表裡的擁有者、目的地、日期都與 `comparison` 重複。不採用。
- **保留 1-1、日程頁另開入口或可跳過**：PM 決定以日程頁取代 1-1；不排活動直接按下一步即等於跳過。

## Consequences

- 既有比較紀錄沒有日程活動，視為空日程，不回補。
- 「與日程不一致」的問題不存在：目的地與日期只有一份。
