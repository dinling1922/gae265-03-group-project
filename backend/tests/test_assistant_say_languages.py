"""小助理幫你說（#572）：目的地 → 當地語言的對照表。

對照表以 `regions.json` 的地名為範圍；有人加了地名卻沒補對照，這裡要擋下來。
"""

import json

from app.agents.incident.assistant_say import LANGUAGE_NAMES
from app.core.config import settings
from app.domain.assistant_say import ENGLISH_ONLY_PLACES, LOCAL_LANGUAGES


def region_names() -> list[str]:
    table = json.loads((settings.CONFIG_DIR / "regions.json").read_text("utf-8"))
    return [name for names in table.values() for name in names]


def test_regions_json_的每個地名都有對照結果_一種當地語言或只顯示英文() -> None:
    mapped = [p for places in LOCAL_LANGUAGES.values() for p in places]
    mapped += ENGLISH_ONLY_PLACES

    assert [n for n in region_names() if n not in mapped] == []
    # 一個地名只能有一種結果，不然就是對照表自己矛盾
    assert len(mapped) == len(set(mapped))


def test_對照表用到的每個語言代碼_翻譯員的語言名稱裡都有() -> None:
    assert set(LOCAL_LANGUAGES) <= set(LANGUAGE_NAMES)
    assert all(LANGUAGE_NAMES[code].endswith("文") for code in LOCAL_LANGUAGES)
