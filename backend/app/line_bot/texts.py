"""LINE bot 的文案（繁體中文）。全部集中在這裡；指令與快速回覆的用字要請設計者確認（ADR-0033）。"""

from app.line_bot.client import QuickReply

# 指令關鍵字（說明文字與快速回覆用；實際的指令登記在 commands/ 各檔）
CMD_COMPARE = "比較"
CMD_TODO = "待辦"
CMD_HELP = "說明"
CMD_BIND = "綁定"
CMD_UNBIND = "解除綁定"

HELP_TEXT = (
    "好險有你 LINE 小幫手\n"
    "我可以幫你查自己的資料（只讀，不會改任何東西）：\n"
    "・比較：看最近的保險比較紀錄\n"
    "・待辦：看理賠待辦（「待辦 已完成」看已完成的）\n"
    "・說明：再看一次這份說明\n"
    "・綁定 123456：用網頁帳號頁產生的綁定碼，連結你的帳號\n"
    "・解除綁定：解除 LINE 與帳號的連結\n"
    "\n"
    "請在一對一聊天使用，我不會在群組裡處理指令。"
)

HELP_QUICK_REPLIES = (
    QuickReply(CMD_COMPARE, CMD_COMPARE),
    QuickReply(CMD_TODO, CMD_TODO),
    QuickReply(CMD_HELP, CMD_HELP),
)

WELCOME_TEXT = "歡迎加入好險有你！\n\n" + HELP_TEXT

GROUP_NOTICE_TEXT = (
    "嗨，我只能在一對一聊天裡使用，不會在群組或多人聊天室處理指令，"
    "以免個人資料被同群的人看到。請加我好友後私訊我，傳「說明」看可用的指令。"
)

NOT_READY_TEXT = "這個指令還在準備中，請稍後再試。"

# ---- 綁定（#487） ----

BIND_USAGE_TEXT = (
    "請傳「綁定 123456」，後面接 6 位數綁定碼。\n"
    "綁定碼在網頁的帳號頁按「綁定 LINE」產生，10 分鐘內有效。"
)
BIND_SUCCESS_TEXT = (
    "綁定成功！之後可以傳「比較」看最近的保險比較紀錄、傳「待辦」看理賠待辦。\n"
    "提醒：回覆的內容會經過 LINE 平台。要解除連結請傳「解除綁定」。"
)
BIND_SUCCESS_REPLACED_TEXT = (
    "綁定成功！你的帳號原本連結的另一個 LINE 已解除，之後只有這個 LINE 能查你的資料。\n"
    "可以傳「比較」看最近的保險比較紀錄、傳「待辦」看理賠待辦。"
)
BIND_WRONG_TEXT = "綁定碼不正確，請確認後再傳一次，或回網頁帳號頁重新產生。"
BIND_EXPIRED_TEXT = "這組綁定碼已過期（只有 10 分鐘有效），請回網頁帳號頁重新產生。"
BIND_USED_TEXT = "這組綁定碼已經用過了，請回網頁帳號頁重新產生。"
BIND_THROTTLED_TEXT = "嘗試次數太多了，請過幾分鐘再試。"
BIND_LINE_TAKEN_TEXT = "這個 LINE 已經綁定了另一個帳號。請先傳「解除綁定」，再傳一次綁定碼（綁定碼還有效，不用重新產生）。"
BIND_ALREADY_TEXT = "這個 LINE 已經綁定你的帳號了，可以直接傳「比較」或「待辦」。"
BIND_TOKEN_LIMIT_TEXT = (
    "你的帳號有效的存取權杖已經滿 5 把，沒辦法再建立綁定用的權杖。"
    "請到網頁帳號頁撤銷不用的存取權杖，再傳一次同一組綁定碼（碼還有效）。"
)
BIND_ERROR_TEXT = "綁定時發生問題，請稍後再試。"

UNBIND_SUCCESS_TEXT = (
    "已解除綁定，我不再能查你的資料。要再連結請到網頁帳號頁產生新的綁定碼。"
)
UNBIND_NOT_BOUND_TEXT = "這個 LINE 目前沒有綁定帳號。"

# ---- 需要綁定的指令（比較、待辦）共用 ----

NOT_BOUND_TEXT = "這個 LINE 還沒綁定帳號。請到網頁帳號頁按「綁定 LINE」產生綁定碼，再傳「綁定 123456」給我。"
BINDING_INVALID_TEXT = (
    "綁定已失效，請重新綁定：到網頁帳號頁產生新的綁定碼，再傳「綁定 123456」給我。"
)
RATE_LIMITED_TEXT = "查詢太頻繁了，請稍後再試。"
MCP_ERROR_TEXT = "暫時查不到資料，請稍後再試。"
