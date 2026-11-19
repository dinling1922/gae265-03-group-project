# ruff: noqa: T201  # 命令列腳本，輸出就是印到終端機
"""`make line-richmenu` 的入口：建立（或重建）LINE 圖文選單。用法見 docs/agents/line-bot.md。

make line-richmenu image=path/to/menu.png                  # 建立並設為預設，刪掉同名舊選單
make line-richmenu image=path/to/menu.png args=--dry-run   # 只驗證、不送出
make line-richmenu args="--placeholder /tmp/menu.png"      # 產生本機驗證用的佔位圖（不可上線）
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlparse

from app.core.config import settings
from app.line_bot import richmenu
from app.line_bot.richmenu import (
    HttpRichMenuClient,
    RichMenuClient,
    RichMenuError,
    build_menu_request,
    deploy,
    make_placeholder_png,
    validate_image,
)


def _site_url(base: str | None) -> str:
    url = (base or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise RichMenuError(
            "PUBLIC_BASE_URL 沒設定或格式不對（要是 https://… 的網址）：「開啟網站」按鈕需要網址，"
            "請在 .env 設定後再執行。"
        )
    return url


def run(
    argv: Sequence[str],
    *,
    token: str,
    public_base_url: str | None,
    client: RichMenuClient | None = None,
) -> int:
    """回傳結束碼；訊息印到 stdout／stderr。`client` 給測試注入假的。"""
    parser = argparse.ArgumentParser(
        prog="line-richmenu", description="建立 LINE 圖文選單"
    )
    parser.add_argument(
        "image",
        nargs="?",
        help="選單圖路徑（PNG／JPEG，2500×1686 或 2500×843，≤ 1 MB）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只驗證圖片與設定、印出將送出的按鈕，不呼叫 LINE",
    )
    parser.add_argument(
        "--placeholder",
        metavar="路徑",
        help="在該路徑產生本機驗證用的佔位 PNG（2500×1686）後結束",
    )
    args = parser.parse_args(list(argv))

    try:
        if args.placeholder:
            Path(args.placeholder).write_bytes(make_placeholder_png())
            print(f"已產生佔位圖 {args.placeholder}（只供本機驗證，不可上線）")
            return 0
        if not args.image:
            raise RichMenuError(
                "請指定選單圖：make line-richmenu image=path/to/menu.png"
            )
        path = Path(args.image)
        if not path.is_file():
            raise RichMenuError(f"找不到圖片：{path}")
        image = path.read_bytes()
        info = validate_image(image)
        site_url = _site_url(public_base_url)
        if args.dry_run:
            request = build_menu_request(info.width, info.height, site_url)
            print(
                f"圖片 OK：{info.content_type}、{info.width}×{info.height}、{info.size} bytes"
            )
            print(
                f"將建立選單「{richmenu.MENU_NAME}」，{len(request['areas'])} 個按鈕："
            )
            for area in request["areas"]:
                action = area["action"]
                print(f"  {action['label']}：{action.get('data') or action.get('uri')}")
            print("（--dry-run：沒有呼叫 LINE）")
            return 0
        if not token:
            raise RichMenuError(
                "LINE_CHANNEL_ACCESS_TOKEN 沒設定：請在 .env 填入 Channel access token 後再執行。"
            )
        result = deploy(client or HttpRichMenuClient(token), image, site_url, print)
        print(f"完成：預設選單是 {result.new_id}")
        return 0
    except RichMenuError as exc:
        print(f"錯誤：{exc}", file=sys.stderr)
        return 1


def main() -> None:
    sys.exit(
        run(
            sys.argv[1:],
            token=settings.LINE_CHANNEL_ACCESS_TOKEN.get_secret_value(),
            public_base_url=settings.PUBLIC_BASE_URL,
        )
    )


if __name__ == "__main__":
    main()
