"""Agent 5「小助理幫你說」：把程式組好的中文句子翻成當地語言與英文，給旅客拿給櫃檯人員看。

只翻譯，不增減內容：送進來的句子 ID 要原樣帶回；不產生任何數字（ADR-0004），輸出由
`app.domain.assistant_say.validated` 再檢查一次。
"""

from collections.abc import Awaitable, Callable

from app.domain.assistant_say import Translations

# 收到「句子 ID → 中文」與目標語言代碼（None＝只要英文），回傳 LLM 的原始輸出（Translations 或同形狀的 dict）
Translator = Callable[[dict[str, str], str | None], Awaitable[object]]

# 語言代碼 → 給翻譯員提示詞用的中文名稱（對照表用到的每個代碼都要有，有測試檢查）
LANGUAGE_NAMES = {
    "ja": "日文",
    "ko": "韓文",
    "th": "泰文",
    "vi": "越南文",
    "id": "印尼文",
    "fr": "法文",
    "de": "德文",
    "it": "義大利文",
    "es": "西班牙文",
    "pt": "葡萄牙文",
    "nl": "荷蘭文",
    "cs": "捷克文",
    "pl": "波蘭文",
    "hu": "匈牙利文",
    "el": "希臘文",
    "is": "冰島文",
    "nb": "挪威文",
    "sv": "瑞典文",
    "fi": "芬蘭文",
    "da": "丹麥文",
}

SYSTEM_PROMPT = """你是旅遊保險的翻譯助理。旅客在國外出事，要把手機拿給櫃檯人員看，請對方開立理賠需要的文件。
把使用者給的每一句中文翻成指定的語言，櫃檯人員看了馬上知道要開什麼文件。

規則：
- 每一句都要翻，句子 ID 一字不改、原樣帶回；不增加、不刪減、不合併句子。
- 只翻原句的意思，不補充說明、不加原句沒有的資訊或客套話。
- 語氣禮貌、簡短，用櫃檯人員看得懂的用語；機構名稱、文件名稱用當地通用的說法。
- 數字、日期照原句，不要自己加數字，也不要改寫。
- 英文一律要有。當地語言只有在指定時才翻，沒有指定時 local 填 null。"""


def user_prompt(sentences: dict[str, str], language: str | None) -> str:
    target = (
        f"當地語言：{LANGUAGE_NAMES[language]}（填在 local）；英文填在 english。"
        if language
        else "只要英文（填在 english），local 填 null。"
    )
    lines = [f"{key}：{text}" for key, text in sentences.items()]
    return "\n".join([target, "要翻譯的句子（ID：中文）：", *lines])


async def llm_translate(sentences: dict[str, str], language: str | None) -> object:
    from app.core.llm import get_chat_model

    model = get_chat_model().with_structured_output(Translations)
    return await model.ainvoke(
        [("system", SYSTEM_PROMPT), ("human", user_prompt(sentences, language))]
    )


def get_assistant_translator() -> Translator:
    """FastAPI dependency；測試換成假的。"""
    return llm_translate
