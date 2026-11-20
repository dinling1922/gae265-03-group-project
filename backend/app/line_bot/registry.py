"""指令與事件處理器的註冊處：每個指令一個檔案，放在 `commands/` 底下。

新增指令（LINE-2 綁定、LINE-4 比較、LINE-5 待辦…）：在 `commands/` 新增（或改寫）自己的那一個檔案，
用 `@line_command` 裝飾一個 async 函式即可；不必改這裡、`webhook.py` 或 `texts.py`（說明文字已列出全部指令），
`load_command_modules` 會自動載入 `commands/` 下所有模組。

    @line_command("比較")
    async def compare(ctx: LineContext) -> None:
        await ctx.reply("…", quick_replies=[QuickReply("看第 1 筆", "比較 1")])

規則：
- 指令比對：訊息去頭尾空白後，等於關鍵字，或以「關鍵字 + 空白」開頭（`ctx.args` 是後面剩下的字）；
  同一個處理器可登記多個關鍵字；多個關鍵字重複登記會在載入時報錯；越長的關鍵字越優先
  （「解除綁定」先於「綁定」，不過以空白分隔的比對本來就不會誤判）
- 指令只在一對一聊天分派；群組與聊天室由 webhook 擋掉
- 非訊息事件（例如 `unfollow`）用 `@line_event("unfollow")` 登記，可登記多個處理器，依載入順序執行
- 處理器丟出例外時 webhook 會記 log 並吞掉，不影響其他事件
- 處理器只用回覆（`ctx.reply` 回文字、`ctx.reply_flex` 回 Flex 卡，一個事件只能回一次）；不推播
- postback（卡片按鈕，ADR-0035）和純文字走同一個處理器：`cmd` 對到指令關鍵字（見 `POSTBACK_COMMANDS`），
  處理器照常用 `ctx.reply`／`ctx.reply_flex`；按鈕帶的識別碼在 `ctx.postback`（例如 `ctx.postback.get("id")`）
"""

import importlib
import logging
import pkgutil
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.line_bot import commands
from app.line_bot.client import FlexMessage, LineClient, QuickReply, TextMessage
from app.line_bot.flex import MAX_MESSAGES_PER_REPLY, FlexError, check_contents
from app.line_bot.limits import clamp_quick_replies, fit_text

logger = logging.getLogger(__name__)

SessionMaker = Callable[[], AsyncSession]


@dataclass
class LineContext:
    """處理器拿到的上下文。小而穩定：之後只加欄位，不改名、不刪。"""

    event: dict[
        str, Any
    ]  # LINE 的 event 物件原樣（type、source、message、replyToken…）
    line_user_id: str  # event.source.userId（綁定用）；LINE 沒給時空字串
    text: str  # 訊息文字（去頭尾空白）；非文字訊息或非訊息事件為空字串
    args: str  # 指令關鍵字後面剩下的字（去頭尾空白）；沒有指令時為空字串
    session_maker: SessionMaker  # 用法：`async with ctx.session_maker() as session:`
    _client: LineClient
    _replied: bool = field(default=False, repr=False)
    # postback 事件才有：`data` 解析後、去掉 `cmd` 剩下的鍵值（例如 {"id": "<uuid>"}）；其他事件為空
    postback: dict[str, str] = field(default_factory=dict)

    async def reply(self, text: str, quick_replies: Sequence[QuickReply] = ()) -> None:
        """回覆一則文字（可附快速回覆）。回覆權杖只能用一次，同一事件第二次呼叫會被忽略並記 log。
        LINE 呼叫失敗只記 log，不會丟例外。"""
        token = self.event.get("replyToken")
        if self._replied or not token:
            logger.warning("LINE 事件沒有可用的回覆權杖或已回覆過，略過回覆")
            return
        self._replied = True
        await self._client.reply(token, [TextMessage(text, tuple(quick_replies))])

    async def reply_flex(
        self,
        alt_text: str,
        contents: dict[str, Any] | Callable[[], dict[str, Any]],
        *,
        fallback_text: str | None = None,
        quick_replies: Sequence[QuickReply] = (),
        fallback_quick_replies: Sequence[QuickReply] | None = None,
    ) -> None:
        """回覆一則 Flex 卡（bubble 或 carousel，用 `app.line_bot.flex` 組）。

        `alt_text` 是通知與舊版 LINE 顯示的文字，用既有的純文字摘要；`contents` 也可以給一個
        回傳卡片的函式，組裝時丟任何例外都會降級。降級規則（ADR-0035）：組裝失敗、超過 LINE 上限
        （單則 50 KB、輪播 12 張）或 LINE 拒絕時，改回純文字 `fallback_text`（沒給就用 `alt_text`），
        使用者一定有回應。`quick_replies` 附在 Flex 上；降級純文字時附 `fallback_quick_replies`
        （沒給就同 `quick_replies`）。卡片本身有按鈕時，通常只給 `fallback_quick_replies`：
        卡片送成功就不重複附快速回覆，降級成沒有按鈕的純文字時才補上。"""
        await self.reply_flex_messages(
            [(alt_text, contents)],
            fallback_text=fallback_text or alt_text,
            quick_replies=quick_replies,
            fallback_quick_replies=fallback_quick_replies,
        )

    async def reply_flex_messages(
        self,
        cards: Sequence[tuple[str, dict[str, Any] | Callable[[], dict[str, Any]]]],
        *,
        fallback_text: str,
        quick_replies: Sequence[QuickReply] = (),
        fallback_quick_replies: Sequence[QuickReply] | None = None,
    ) -> None:
        """一次回多則 Flex（每則 `(alt_text, contents)`，最多 5 則）；降級規則同 `reply_flex`，降級成一則純文字。
        快速回覆掛在最後一則。"""
        token = self.event.get("replyToken")
        if self._replied or not token:
            logger.warning("LINE 事件沒有可用的回覆權杖或已回覆過，略過回覆")
            return
        self._replied = True
        quick = tuple(quick_replies)
        fallback_quick = (
            quick if fallback_quick_replies is None else tuple(fallback_quick_replies)
        )
        sent = False
        try:
            if not 1 <= len(cards) <= MAX_MESSAGES_PER_REPLY:
                raise FlexError(f"一次回覆最多 {MAX_MESSAGES_PER_REPLY} 則 Flex")
            messages: list[FlexMessage] = []
            for index, (alt_text, contents) in enumerate(cards):
                built = contents() if callable(contents) else contents
                check_contents(built)
                last = index == len(cards) - 1
                messages.append(
                    FlexMessage(
                        alt_text, built, clamp_quick_replies(quick) if last else ()
                    )
                )
        except Exception as exc:  # 組裝失敗一律降級，使用者不能沒回應
            logger.warning("Flex 組裝失敗，降級為純文字：%s", exc)
        else:
            sent = await self._client.reply(token, messages)
            if not sent:
                logger.warning("LINE 沒收下 Flex，降級為純文字")
        if not sent:
            await self._client.reply(
                token,
                [
                    TextMessage(
                        fit_text(fallback_text), clamp_quick_replies(fallback_quick)
                    )
                ],
            )


Handler = Callable[[LineContext], Awaitable[None]]

COMMANDS: dict[str, Handler] = {}

# postback `cmd` → 指令關鍵字：卡片按鈕用英文代號，和打字走同一個處理器（ADR-0035）。
# 預先列好三個，新卡片不必改這裡；`cmd` 也可以直接寫指令關鍵字（`cmd=比較`）。
POSTBACK_COMMANDS: dict[str, str] = {"compare": "比較", "todo": "待辦", "help": "說明"}
EVENT_HANDLERS: dict[str, list[Handler]] = {}


def line_command(*keywords: str) -> Callable[[Handler], Handler]:
    """把函式登記成一對一聊天的指令（可給多個關鍵字）。"""

    def register(fn: Handler) -> Handler:
        for keyword in keywords:
            if keyword in COMMANDS and COMMANDS[keyword] is not fn:
                raise RuntimeError(f"LINE 指令重複登記：{keyword}")
            COMMANDS[keyword] = fn
        return fn

    return register


def line_event(event_type: str) -> Callable[[Handler], Handler]:
    """把函式登記成某種非訊息事件（例如 `unfollow`）的處理器。`follow` 由 webhook 回歡迎詞，另外也會呼叫這裡。"""

    def register(fn: Handler) -> Handler:
        EVENT_HANDLERS.setdefault(event_type, []).append(fn)
        return fn

    return register


def load_command_modules() -> None:
    for module in pkgutil.iter_modules(commands.__path__):
        importlib.import_module(f"{commands.__name__}.{module.name}")


def match_command(text: str) -> tuple[Handler, str] | None:
    """回傳 (處理器, 關鍵字後面的字)；不認得回 None。"""
    for keyword in sorted(COMMANDS, key=len, reverse=True):
        if text == keyword:
            return COMMANDS[keyword], ""
        if text.startswith(keyword) and text[len(keyword) :][:1].isspace():
            return COMMANDS[keyword], text[len(keyword) :].strip()
    return None


def match_postback(cmd: str) -> tuple[Handler, str] | None:
    """postback 的 `cmd` 對到指令處理器與關鍵字；不認得回 None。"""
    keyword = POSTBACK_COMMANDS.get(cmd, cmd)
    handler = COMMANDS.get(keyword)
    return (handler, keyword) if handler else None
