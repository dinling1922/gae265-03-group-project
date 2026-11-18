"""LINE bot 轉接層（ADR-0033）：LINE 的 webhook 打到 `/line/webhook`，驗簽後依固定指令回覆。

- `client.py`：LINE 呼叫的介面（Protocol）與真實（httpx）實作；測試用假的（`tests/line_support.py`）
- `webhook.py`：`/line/webhook` 端點（驗簽、去重、分派事件）
- `registry.py`：指令與事件處理器的註冊；新增指令見該檔說明與 `commands/`
- `texts.py`：所有給使用者看的文案（集中在這裡，方便請設計者確認後改）
"""
