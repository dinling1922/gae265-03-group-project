"""提醒信的內容（規格書 4.9）：固定範本，由程式帶入，不經 AI。"""

import uuid
from datetime import UTC, date, datetime

from app.domain.reminder.letter import (
    LetterInput,
    MissingGroup,
    build_letter,
    message_id,
    trip_name,
)

FLIGHT = MissingGroup(
    label="班機取消 · 2026/07/10",
    documents=["航空公司開立的班機取消證明", "住宿收據"],
)


def sample(**overrides: object) -> LetterInput:
    values: dict[str, object] = {
        "user_name": "王小明",
        "trip_name": "日本 2026/07/05～07/10",
        "groups": [FLIGHT],
        "company": "富邦產物",
        "upload_url": "https://example.com/incident/abc",
    }
    values.update(overrides)
    return LetterInput(**values)  # pyright: ignore[reportArgumentType]


def test_主旨寫出行程名稱與還缺幾份() -> None:
    letter = build_letter(sample())
    assert letter.subject == "【好險有你】日本 2026/07/05～07/10 還缺 2 份理賠文件"


def test_純文字內文照規格的範本() -> None:
    assert build_letter(sample()).text == (
        "王小明你好，\n"
        "\n"
        "你的行程「日本 2026/07/05～07/10」還有以下理賠文件尚未上傳：\n"
        "\n"
        "班機取消 · 2026/07/10\n"
        "・航空公司開立的班機取消證明\n"
        "・住宿收據\n"
        "\n"
        "上傳完成後，我們可以協助你把文件寄給富邦產物。理賠申請有期限，請盡早準備。\n"
        "\n"
        "前往上傳：https://example.com/incident/abc\n"
        "\n"
        "此信由系統自動寄出，請勿直接回覆。\n"
    )


def test_HTML_內文的連結是前往上傳按鈕_使用者輸入的文字會跳脫() -> None:
    html = build_letter(sample(user_name="<b>小明</b>")).html
    assert '<a href="https://example.com/incident/abc"' in html
    assert ">前往上傳</a>" in html
    assert "&lt;b&gt;小明&lt;/b&gt;你好" in html
    assert "<b>小明</b>" not in html
    assert "<li>航空公司開立的班機取消證明</li>" in html


def test_行程名稱是目的地加保險期間() -> None:
    assert (
        trip_name("日本", date(2026, 7, 5), date(2026, 7, 10))
        == "日本 2026/07/05～07/10"
    )


def test_跨年的行程迄日加上年份() -> None:
    assert (
        trip_name("韓國", date(2026, 12, 30), date(2027, 1, 3))
        == "韓國 2026/12/30～2027/01/03"
    )


def test_沒有保險期間或目的地時只用有的部分_都沒有就叫行程() -> None:
    # 手選保單可能沒有日期，也可能沒填目的地
    assert trip_name("日本", None, None) == "日本"
    assert trip_name(None, date(2026, 7, 5), date(2026, 7, 10)) == "2026/07/05～07/10"
    assert trip_name(None, None, None) == "行程"


def test_Message_ID_由保單_這一輪的起點與第幾封組成_重寄時相同() -> None:
    policy = uuid.UUID("12345678-1234-5678-1234-567812345678")
    started = datetime(2026, 7, 11, 9, 0, tzinfo=UTC)
    sender = "DoNotReply@abc.azurecomm.net"
    assert (
        message_id(policy, started, 2, sender)
        == "<reminder-12345678-1234-5678-1234-567812345678-1783760400-2@abc.azurecomm.net>"
    )
