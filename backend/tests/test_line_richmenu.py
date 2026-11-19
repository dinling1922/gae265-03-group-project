"""LINE 圖文選單管理腳本（FLEX-4）：全用假客戶端，不連 LINE，不需要資料庫。"""

import struct
from pathlib import Path
from typing import Any

import httpx
import pytest
from app.line_bot import flex, richmenu
from app.line_bot.richmenu import (
    HttpRichMenuClient,
    RichMenuError,
    make_placeholder_png,
)
from app.line_bot.richmenu_cli import run

SITE = "https://example.test"


class FakeRichMenuClient:
    def __init__(
        self, existing: list[dict[str, Any]] | None = None, fail_on: str | None = None
    ) -> None:
        self.menus = list(existing or [])
        self.calls: list[tuple[str, Any]] = []
        self.fail_on = fail_on

    def _maybe_fail(self, name: str) -> None:
        if self.fail_on == name:
            raise RichMenuError(f"{name} 失敗")

    def create(self, request: dict[str, Any]) -> str:
        self.calls.append(("create", request))
        self._maybe_fail("create")
        menu_id = f"new-{len(self.menus)}"
        self.menus.append({"richMenuId": menu_id, "name": request["name"]})
        return menu_id

    def upload_image(self, rich_menu_id: str, image: bytes, content_type: str) -> None:
        self.calls.append(("upload", (rich_menu_id, content_type, len(image))))
        self._maybe_fail("upload")

    def set_default(self, rich_menu_id: str) -> None:
        self.calls.append(("default", rich_menu_id))
        self._maybe_fail("default")

    def list_menus(self) -> list[dict[str, Any]]:
        self.calls.append(("list", None))
        return list(self.menus)

    def delete(self, rich_menu_id: str) -> None:
        self.calls.append(("delete", rich_menu_id))
        self._maybe_fail("delete")
        self.menus = [m for m in self.menus if m["richMenuId"] != rich_menu_id]

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


def jpeg_bytes(width: int, height: int) -> bytes:
    """最小的 JPEG 檔頭（只到 SOF0，足夠讀尺寸）。"""
    sof = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    return b"\xff\xd8" + b"\xff\xe0" + struct.pack(">H", 4) + b"\x00\x00" + sof


@pytest.fixture
def png(tmp_path: Path) -> Path:
    path = tmp_path / "menu.png"
    path.write_bytes(make_placeholder_png(2500, 1686))
    return path


# ---- 圖片驗證 ----


def test_validate_accepts_both_png_sizes_and_jpeg() -> None:
    assert richmenu.validate_image(make_placeholder_png(2500, 1686)).height == 1686
    small = richmenu.validate_image(make_placeholder_png(2500, 843))
    assert (small.width, small.height, small.content_type) == (2500, 843, "image/png")
    assert richmenu.validate_image(jpeg_bytes(2500, 843)).content_type == "image/jpeg"


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"GIF89a" + b"\x00" * 40, "PNG 或 JPEG"),
        (b"", "PNG 或 JPEG"),
        (make_placeholder_png(1200, 810), "1200×810"),
        (jpeg_bytes(800, 600), "800×600"),
        (make_placeholder_png(2500, 1686) + b"\x00" * 1_000_000, "1 MB"),
    ],
)
def test_validate_rejects_bad_images(data: bytes, message: str) -> None:
    with pytest.raises(RichMenuError, match=message):
        richmenu.validate_image(data)


# ---- 建立請求 ----


def test_request_layout_2x2_for_tall_image() -> None:
    request = richmenu.build_menu_request(2500, 1686, SITE)
    assert request["size"] == {"width": 2500, "height": 1686}
    assert request["name"] == "好險有你 主選單"
    areas = request["areas"]
    assert [a["bounds"]["x"] for a in areas] == [0, 1250, 0, 1250]
    assert [a["bounds"]["y"] for a in areas] == [0, 0, 843, 843]
    assert all(
        a["bounds"]["width"] == 1250 and a["bounds"]["height"] == 843 for a in areas
    )


def test_request_layout_1x4_for_short_image() -> None:
    areas = richmenu.build_menu_request(2500, 843, SITE)["areas"]
    assert [a["bounds"]["x"] for a in areas] == [0, 625, 1250, 1875]
    assert all(a["bounds"]["y"] == 0 and a["bounds"]["height"] == 843 for a in areas)


def test_request_actions_match_flex_postbacks_and_site_url() -> None:
    actions = [
        a["action"] for a in richmenu.build_menu_request(2500, 1686, SITE)["areas"]
    ]
    assert [a["label"] for a in actions] == [
        "我的比較",
        "理賠待辦",
        "使用說明",
        "開啟網站",
    ]
    postbacks = [a for a in actions if a["type"] == "postback"]
    assert [a["data"] for a in postbacks] == [
        flex.postback_data("compare"),
        flex.postback_data("todo"),
        flex.postback_data("help"),
    ]
    assert all("displayText" not in a for a in postbacks)
    assert actions[3] == {"type": "uri", "label": "開啟網站", "uri": SITE}
    assert not any("綁定" in a["label"] for a in actions)


# ---- 部署流程 ----


def test_deploy_creates_uploads_then_sets_default() -> None:
    client = FakeRichMenuClient()
    result = richmenu.deploy(client, make_placeholder_png(), SITE)
    assert client.names() == ["list", "create", "upload", "default"]
    assert client.calls[2][1] == (
        result.new_id,
        "image/png",
        len(make_placeholder_png()),
    )
    assert client.calls[3][1] == result.new_id


def test_rerun_removes_old_same_name_menus_only() -> None:
    client = FakeRichMenuClient(
        existing=[
            {"richMenuId": "old-1", "name": richmenu.MENU_NAME},
            {"richMenuId": "other", "name": "別的選單"},
            {"richMenuId": "old-2", "name": richmenu.MENU_NAME},
        ]
    )
    result = richmenu.deploy(client, make_placeholder_png(), SITE)
    assert result.deleted_ids == ["old-1", "old-2"]
    assert sorted(m["richMenuId"] for m in client.menus) == ["new-3", "other"]
    # 舊的是在新的設成預設之後才刪
    names = client.names()
    assert names.index("default") < names.index("delete")


def test_upload_failure_deletes_half_made_menu_and_keeps_old() -> None:
    client = FakeRichMenuClient(
        existing=[{"richMenuId": "old-1", "name": richmenu.MENU_NAME}], fail_on="upload"
    )
    with pytest.raises(RichMenuError):
        richmenu.deploy(client, make_placeholder_png(), SITE)
    assert [m["richMenuId"] for m in client.menus] == ["old-1"]
    assert ("delete", "old-1") not in client.calls


def test_old_menu_delete_failure_is_reported_not_raised() -> None:
    client = FakeRichMenuClient(
        existing=[{"richMenuId": "old-1", "name": richmenu.MENU_NAME}], fail_on="delete"
    )
    result = richmenu.deploy(client, make_placeholder_png(), SITE)
    assert result.failed_deletes == ["old-1"]


# ---- 腳本入口 ----


def test_cli_success(png: Path, capsys: pytest.CaptureFixture[str]) -> None:
    client = FakeRichMenuClient()
    code = run([str(png)], token="t", public_base_url=SITE, client=client)
    assert code == 0
    assert client.names() == ["list", "create", "upload", "default"]
    assert "完成" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"not an image", "PNG 或 JPEG"),
        (make_placeholder_png(1200, 810), "1200×810"),
        (make_placeholder_png(2500, 1686) + b"\x00" * 1_000_000, "1 MB"),
    ],
)
def test_cli_bad_image_sends_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], data: bytes, message: str
) -> None:
    path = tmp_path / "bad.png"
    path.write_bytes(data)
    client = FakeRichMenuClient()
    assert run([str(path)], token="t", public_base_url=SITE, client=client) == 1
    assert client.calls == []
    assert message in capsys.readouterr().err


def test_cli_missing_file_and_missing_argument(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    client = FakeRichMenuClient()
    assert (
        run([str(tmp_path / "x.png")], token="t", public_base_url=SITE, client=client)
        == 1
    )
    assert run([], token="t", public_base_url=SITE, client=client) == 1
    assert client.calls == []
    err = capsys.readouterr().err
    assert "找不到圖片" in err and "請指定選單圖" in err


def test_cli_missing_token(png: Path, capsys: pytest.CaptureFixture[str]) -> None:
    client = FakeRichMenuClient()
    assert run([str(png)], token="", public_base_url=SITE, client=client) == 1
    assert client.calls == []
    assert "LINE_CHANNEL_ACCESS_TOKEN" in capsys.readouterr().err


@pytest.mark.parametrize("base", [None, "", "   ", "example.test"])
def test_cli_missing_public_base_url(
    png: Path, capsys: pytest.CaptureFixture[str], base: str | None
) -> None:
    client = FakeRichMenuClient()
    assert run([str(png)], token="t", public_base_url=base, client=client) == 1
    assert client.calls == []
    assert "PUBLIC_BASE_URL" in capsys.readouterr().err


def test_cli_dry_run_validates_but_sends_nothing(
    png: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    client = FakeRichMenuClient()
    # dry-run 不需要權杖
    assert (
        run([str(png), "--dry-run"], token="", public_base_url=SITE, client=client) == 0
    )
    assert client.calls == []
    out = capsys.readouterr().out
    assert "dry-run" in out and "cmd=compare" in out and SITE in out


def test_cli_dry_run_still_rejects_bad_image(tmp_path: Path) -> None:
    path = tmp_path / "bad.png"
    path.write_bytes(b"nope")
    assert run([str(path), "--dry-run"], token="", public_base_url=SITE) == 1


def test_cli_placeholder_writes_valid_png(tmp_path: Path) -> None:
    out = tmp_path / "placeholder.png"
    assert run(["--placeholder", str(out)], token="", public_base_url=None) == 0
    info = richmenu.validate_image(out.read_bytes())
    assert (info.width, info.height) == (2500, 1686)


# ---- 真實 httpx 實作（用假 transport，驗證網址與標頭） ----


def test_http_client_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/richmenu") and request.method == "POST":
            return httpx.Response(200, json={"richMenuId": "rm-1"})
        if request.url.path.endswith("/list"):
            return httpx.Response(
                200, json={"richmenus": [{"richMenuId": "a", "name": "x"}]}
            )
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handler)

    def fake_request(method: str, url: str, **kwargs: Any) -> httpx.Response:
        with httpx.Client(transport=transport) as c:
            return c.request(method, url, **kwargs)

    monkeypatch.setattr(richmenu.httpx, "request", fake_request)
    client = HttpRichMenuClient("secret-token")
    assert client.create({"name": "n"}) == "rm-1"
    client.upload_image("rm-1", b"img", "image/png")
    client.set_default("rm-1")
    assert client.list_menus() == [{"richMenuId": "a", "name": "x"}]
    client.delete("rm-1")

    assert [(r.method, str(r.url)) for r in seen] == [
        ("POST", "https://api.line.me/v2/bot/richmenu"),
        ("POST", "https://api-data.line.me/v2/bot/richmenu/rm-1/content"),
        ("POST", "https://api.line.me/v2/bot/user/all/richmenu/rm-1"),
        ("GET", "https://api.line.me/v2/bot/richmenu/list"),
        ("DELETE", "https://api.line.me/v2/bot/richmenu/rm-1"),
    ]
    assert all(r.headers["authorization"] == "Bearer secret-token" for r in seen)
    assert seen[1].headers["content-type"] == "image/png"
    assert seen[1].content == b"img"


def test_http_client_error_hides_token(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_request(method: str, url: str, **_kwargs: Any) -> httpx.Response:
        return httpx.Response(401, text="bad token", request=httpx.Request(method, url))

    monkeypatch.setattr(richmenu.httpx, "request", fake_request)
    with pytest.raises(RichMenuError) as info:
        HttpRichMenuClient("secret-token").list_menus()
    assert "401" in str(info.value) and "secret-token" not in str(info.value)
