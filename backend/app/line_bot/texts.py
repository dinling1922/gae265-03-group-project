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
