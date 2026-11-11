"""提醒信的內容（規格書 4.9、ADR-0028）：固定範本，由程式帶入，不經 AI。"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from html import escape


@dataclass(frozen=True)
class MissingGroup:
    """信裡的一組缺件：一個事故（或整個行程共用的文件）與它還缺的文件。"""

    label: str
    documents: list[str]


@dataclass(frozen=True)
class LetterInput:
    user_name: str | None
    trip_name: str
    groups: list[MissingGroup]
    company: str
    upload_url: str


@dataclass(frozen=True)
class Letter:
    subject: str
    text: str
    html: str


def trip_name(destination: str | None, start: date | None, end: date | None) -> str:
    """行程名稱：目的地＋保險期間，例如「日本 2026/07/05～07/10」。"""
    period = ""
    if start is not None and end is not None:
        end_format = "%m/%d" if end.year == start.year else "%Y/%m/%d"
        period = f"{start:%Y/%m/%d}～{end:{end_format}}"
    return " ".join(p for p in (destination, period) if p) or "行程"


def message_id(
    policy_id: uuid.UUID, started_at: datetime, number: int, sender: str
) -> str:
    """固定的 Message-ID：同一張保單、同一輪、第幾封都相同時，重寄也是同一個 ID（ADR-0028）。"""
    domain = sender.rsplit("@", 1)[-1]
    return f"<reminder-{policy_id}-{int(started_at.timestamp())}-{number}@{domain}>"


def build_letter(data: LetterInput) -> Letter:
    count = sum(len(g.documents) for g in data.groups)
    subject = f"【好險有你】{data.trip_name} 還缺 {count} 份理賠文件"
    groups = "\n\n".join(
        "\n".join([g.label, *(f"・{d}" for d in g.documents)]) for g in data.groups
    )
    text = (
        f"{data.user_name or ''}你好，\n"
        "\n"
        f"你的行程「{data.trip_name}」還有以下理賠文件尚未上傳：\n"
        "\n"
        f"{groups}\n"
        "\n"
        f"上傳完成後，我們可以協助你把文件寄給{data.company}。理賠申請有期限，請盡早準備。\n"
        "\n"
        f"前往上傳：{data.upload_url}\n"
        "\n"
        "此信由系統自動寄出，請勿直接回覆。\n"
    )
    return Letter(subject=subject, text=text, html=_html(data))


def _html(data: LetterInput) -> str:
    """和純文字同一份內容；名稱、行程、文件名稱都可能來自使用者，一律跳脫。"""
    groups = "".join(
        f"<p><strong>{escape(g.label)}</strong></p>"
        f"<ul>{''.join(f'<li>{escape(d)}</li>' for d in g.documents)}</ul>"
        for g in data.groups
    )
    url = escape(data.upload_url, quote=True)
    return (
        '<div style="font-family:sans-serif;line-height:1.6">'
        f"<p>{escape(data.user_name or '')}你好，</p>"
        f"<p>你的行程「{escape(data.trip_name)}」還有以下理賠文件尚未上傳：</p>"
        f"{groups}"
        f"<p>上傳完成後，我們可以協助你把文件寄給{escape(data.company)}。"
        "理賠申請有期限，請盡早準備。</p>"
        f'<p><a href="{url}" style="display:inline-block;padding:10px 20px;'
        'background:#2563eb;color:#fff;border-radius:6px;text-decoration:none">'
        "前往上傳</a></p>"
        '<p style="color:#666;font-size:12px">此信由系統自動寄出，請勿直接回覆。</p>'
        "</div>"
    )
