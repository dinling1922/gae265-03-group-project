"""「說明」選單卡（ADR-0035、FLEX-1）：標題、簡短說明、三個 postback 按鈕（比較、待辦、說明）。

「說明」指令與 follow 事件都回這張卡；按按鈕走 postback，和打字進同一個指令處理器。
文字內容全部來自 `texts.py` 既有的說明文字，這裡只負責排版。降級成純文字時，回既有的說明（follow 用歡迎詞）。
"""

from app.line_bot import flex, texts
from app.line_bot.registry import LineContext

# ---- 暫定文案與版面（待設計者確認；品牌色取自前端設計代幣） ----
MENU_TITLE = "好險有你 LINE 小幫手"
MENU_WELCOME_TITLE = "歡迎加入好險有你！"
MENU_INTRO = (
    "我可以幫你查自己的資料（只讀，不會改任何東西）。點下面的按鈕，或直接打字也可以。"
)
MENU_BIND_HINT = (
    "還沒綁定？到網頁帳號頁產生綁定碼，傳「綁定 123456」給我。請在一對一聊天使用。"
)
BUTTON_COMPARE = "我的比較"
BUTTON_TODO = "理賠待辦"
BUTTON_HELP = "使用說明"


def menu_card(*, welcome: bool = False) -> flex.FlexNode:
    return flex.bubble(
        title=MENU_WELCOME_TITLE if welcome else MENU_TITLE,
        body=[
            flex.text(MENU_INTRO, color=flex.INK),
            flex.text(MENU_BIND_HINT, size="xs", color=flex.MUTED, margin="md"),
        ],
        buttons=[
            flex.postback_button(
                BUTTON_COMPARE, flex.postback_data("compare"), style="primary"
            ),
            flex.postback_button(
                BUTTON_TODO, flex.postback_data("todo"), style="primary"
            ),
            flex.postback_button(BUTTON_HELP, flex.postback_data("help")),
        ],
    )


async def reply_menu(ctx: LineContext, *, welcome: bool = False) -> None:
    text = texts.WELCOME_TEXT if welcome else texts.HELP_TEXT
    await ctx.reply_flex(
        text,
        lambda: menu_card(welcome=welcome),
        fallback_quick_replies=texts.HELP_QUICK_REPLIES,  # 卡片有按鈕，降級純文字才補快速回覆
    )
