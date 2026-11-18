"""產生 /dev 文件辨識測試台的範例文件（只在開發者的 Mac 上跑，產出的 PDF 一起 commit）。

    cd backend && uv run python -m app.agents.document.samples.make_samples
    cd backend && uv run python -m app.agents.document.samples.make_samples --medical  # 只重做醫療那幾份

版面參考真實的航空公司文件與電子機票收據，資料全部是虛構的：旅客「陳怡君」、
航空公司「福爾摩沙天空航空」都不存在，每頁底部都印「測試範例（資料虛構）」。
理賠申請書用富邦產險正式的「個人保險理賠申請書」（fubon_claim_form_blank.pdf，第 1 頁），
用手寫風格字型填上假資料。字型用 macOS 內建的黑體與翩翩體，所以要在 Mac 上產生；
backend 容器只讀產出的 PDF，不需要這些字型。
照片版（photo_*.jpg）由 PDF 轉圖再加上斜拍、陰影、反光、模糊，送 Azure 讀字。
"""

import io
import logging
import random
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any

import numpy as np
import pymupdf
from fontTools import subset
from fontTools.pens.cu2quPen import Cu2QuPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont, newTable
from PIL import Image, ImageFilter

from app.agents.document.samples.claim_medical import chubb_claim_form

logger = logging.getLogger(__name__)

HERE = Path(__file__).parent
BLANK_FORM = HERE / "fubon_claim_form_blank.pdf"

FONT_LIGHT = "/System/Library/Fonts/STHeiti Light.ttc"
FONT_BOLD = "/System/Library/Fonts/STHeiti Medium.ttc"
FONT_HAND = next(
    Path("/System/Library/AssetsV2").glob("**/Hanzipen.ttc"),
    Path("Hanzipen.ttc"),
)


def _cff_to_truetype(tt: TTFont) -> None:
    """CFF 外框轉成 TrueType（fontTools 的 otf2ttf 範例）：pymupdf 對 CFF 字型的字位編號處理不對。"""
    # fontTools 的資料表沒有型別資訊，用 Any
    glyph_set: Any = tt.getGlyphSet()
    glyf: Any = newTable("glyf")
    glyf.glyphOrder = tt.getGlyphOrder()
    glyf.glyphs = {}
    for name in glyf.glyphOrder:
        pen = TTGlyphPen(glyph_set)
        glyph_set[name].draw(Cu2QuPen(pen, max_err=1.0, reverse_direction=True))
        glyf.glyphs[name] = pen.glyph()
    tt["loca"] = newTable("loca")
    tt["glyf"] = glyf
    del tt["CFF "]
    if "VORG" in tt:
        del tt["VORG"]
    maxp: Any = newTable("maxp")
    tt["maxp"] = maxp
    maxp.tableVersion = 0x00010000
    for k in (
        "maxZones maxTwilightPoints maxStorage maxFunctionDefs maxInstructionDefs "
        "maxStackElements maxSizeOfInstructions maxComponentElements"
    ).split():
        setattr(maxp, k, 0)
    maxp.maxZones = 1
    post: Any = tt["post"]
    post.formatType = 2.0
    post.extraNames = []
    post.mapping = {}
    head: Any = tt["head"]
    head.glyphDataFormat = 0
    tt.sfntVersion = "\x00\x01\x00\x00"


@cache
def font(path: str | Path, number: int = 0) -> bytes:
    """先裁成這支程式裡出現過的字（印出的字全寫在這裡），PDF 才不會超過 500 KB。

    存檔前再用 subset_fonts 裁成每份實際用到的字。手寫字型是 CFF，pymupdf 裁 CFF 會留下
    3 萬多個空字位、裁過再嵌入又會印成亂碼，所以先轉成 TrueType。
    """
    used = set(Path(__file__).read_text()) | {chr(c) for c in range(0x20, 0x7F)}
    tt = TTFont(str(path), fontNumber=number)
    sub = subset.Subsetter(subset.Options())
    sub.populate(text="".join(used))
    sub.subset(tt)
    if "CFF " in tt:
        _cff_to_truetype(tt)
    out = io.BytesIO()
    tt.save(out)
    return out.getvalue()


NAVY = (0.07, 0.18, 0.38)
GRAY = (0.45, 0.45, 0.45)
LINE = (0.75, 0.75, 0.75)
RED = (0.78, 0.12, 0.12)
INK = (0.09, 0.13, 0.46)  # 原子筆的藍

AIRLINE = "福爾摩沙天空航空"
AIRLINE_EN = "FORMOSA SKY AIRWAYS"
FOOTER = "測試範例（資料虛構）　SAMPLE ONLY — NOT A REAL DOCUMENT"


class Sheet:
    """一頁文件。字一律用整行寫入（「標籤：值」不拆開），去識別化才認得到標籤。"""

    def __init__(self, doc: pymupdf.Document, width: float = 595, height: float = 842):
        self.page = doc.new_page(width=width, height=height)
        self.page.insert_font(fontname="L", fontbuffer=font(FONT_LIGHT))
        self.page.insert_font(fontname="B", fontbuffer=font(FONT_BOLD))

    def text(
        self,
        x: float,
        y: float,
        s: str,
        size: float = 10,
        bold: bool = False,
        color: tuple[float, float, float] = (0, 0, 0),
    ) -> None:
        self.page.insert_text(
            (x, y), s, fontname="B" if bold else "L", fontsize=size, color=color
        )

    def box(
        self,
        r: tuple[float, float, float, float],
        fill: tuple[float, float, float] | None = None,
        color: tuple[float, float, float] | None = LINE,
        width: float = 0.7,
    ) -> None:
        self.page.draw_rect(pymupdf.Rect(r), color=color, fill=fill, width=width)

    def line(
        self, x0: float, y0: float, x1: float, y1: float, width: float = 0.6
    ) -> None:
        self.page.draw_line((x0, y0), (x1, y1), color=LINE, width=width)

    def logo(self, x: float, y: float, r: float = 13) -> None:
        """虛構航空公司的標誌：圓形裡一道斜線。"""
        self.page.draw_circle((x, y), r, color=(1, 1, 1), fill=NAVY, width=1.2)
        self.page.draw_line(
            (x - r * 0.6, y + r * 0.4),
            (x + r * 0.6, y - r * 0.5),
            color=(1, 1, 1),
            width=2,
        )

    def stamp(self, x: float, y: float, top: str, mid: str, bottom: str) -> None:
        """紅色圓形章（虛構單位）。"""
        self.page.draw_circle((x, y), 34, color=RED, width=1.6)
        self.page.draw_circle((x, y), 30, color=RED, width=0.6)
        for s, dy, size in ((top, -10, 7.5), (mid, 4, 9), (bottom, 16, 7)):
            w = (
                pymupdf.get_text_length(s, fontname="helv", fontsize=size)
                if s.isascii()
                else len(s) * size
            )
            self.page.insert_text(
                (x - w / 2, y + dy), s, fontname="B", fontsize=size, color=RED
            )

    def barcode(self, x: float, y: float, w: float, h: float, seed: int) -> None:
        rnd = random.Random(seed)
        cx = x
        while cx < x + w:
            bar = rnd.choice((0.8, 1.2, 1.8, 2.6))
            if rnd.random() > 0.35:
                self.page.draw_rect(
                    pymupdf.Rect(cx, y, cx + bar, y + h), color=None, fill=(0, 0, 0)
                )
            cx += bar + rnd.choice((0.8, 1.2, 1.6))

    def footer(self) -> None:
        h = self.page.rect.height
        self.text(24, h - 12, FOOTER, size=6.5, color=GRAY)


def airline_header(s: Sheet, title: str, title_en: str) -> None:
    s.box((0, 0, 595, 70), fill=NAVY, color=None)
    s.logo(48, 35)
    s.text(70, 32, AIRLINE, size=15, bold=True, color=(1, 1, 1))
    s.text(70, 48, AIRLINE_EN, size=8.5, color=(0.85, 0.9, 1))
    s.text(380, 32, title, size=15, bold=True, color=(1, 1, 1))
    s.text(380, 48, title_en, size=8.5, color=(0.85, 0.9, 1))


def rows(
    s: Sheet,
    x: float,
    y: float,
    items: list[tuple[str, str]],
    w: float = 515,
    h: float = 26,
) -> float:
    """表格：左邊英文欄名（小字），右邊「中文欄名：值」。"""
    for en, zh_value in items:
        s.box((x, y, x + w, y + h))
        s.box((x, y, x + 150, y + h), fill=(0.95, 0.96, 0.98))
        s.text(x + 6, y + 16, en, size=7.5, color=GRAY)
        s.text(x + 158, y + 17, zh_value, size=10.5)
        y += h
    return y


# demo 保單的保險期間是 2026-07-05～07-11；「保險期間外」的範例改用這一天
OUT_OF_PERIOD = date(2026, 6, 20)


def delay_certificate(
    doc: pymupdf.Document, with_times: bool, flight: date = date(2026, 7, 10)
) -> None:
    s = Sheet(doc)
    airline_header(s, "班機延誤證明書", "FLIGHT DELAY CERTIFICATE")
    s.text(
        40,
        100,
        f"證明編號 Certificate No.：NRT-DLY-{flight:%Y%m%d}-0412",
        size=9,
        color=GRAY,
    )
    s.text(40, 128, "茲證明下列旅客搭乘本公司班機，因故延誤起飛，特此證明。", size=11)
    s.text(
        40,
        144,
        "This is to certify that the passenger below was on the following flight, which was delayed.",
        size=8.5,
        color=GRAY,
    )
    items = [
        ("Passenger Name", "旅客姓名：陳怡君　CHEN/YICHUN MS"),
        ("Booking Reference", "訂位代號：K7Q2MD"),
        ("Flight No.", "班機號碼：ZZ 198"),
        ("Flight Date", f"班機日期：{flight.isoformat()}"),
        ("Sector", "航段：東京成田 NRT → 台北桃園 TPE"),
    ]
    if with_times:
        items += [
            ("Scheduled Departure (STD)", "原定起飛時間：10:30（當地時間）"),
            ("Actual Departure (ATD)", "實際起飛時間：16:45（當地時間）"),
            ("Length of Delay", "延誤時間：6 小時 15 分"),
        ]
    else:
        items += [("Length of Delay", "延誤時間：約 6 小時")]
    items += [
        ("Reason", "延誤原因：天候不佳（成田機場強風）Adverse weather"),
        ("Issued at", "開立單位：成田機場站 NRT Airport Station"),
        ("Date of Issue", f"開立日期：{flight.isoformat()}"),
    ]
    y = rows(s, 40, 166, items)
    s.text(
        40,
        y + 30,
        "註：本證明僅證明班機延誤事實，供旅客向保險公司申請理賠使用。",
        size=9,
        color=GRAY,
    )
    s.text(
        40,
        y + 44,
        "Note: This certificate is issued for insurance claim purposes only.",
        size=8,
        color=GRAY,
    )
    s.text(330, y + 92, "站務主管 Duty Manager：", size=10)
    s.page.insert_font(fontname="H", fontbuffer=font(FONT_HAND))
    s.page.insert_text((450, y + 92), "林志明", fontname="H", fontsize=15, color=INK)
    s.stamp(500, y + 140, AIRLINE_EN[:6], "成田站", f"{flight:%Y.%m.%d}")
    s.footer()


def boarding_pass(doc: pymupdf.Document) -> None:
    s = Sheet(doc, 595, 250)
    s.box((10, 10, 585, 240), color=NAVY, width=1.2)
    s.box((10, 10, 585, 48), fill=NAVY, color=None)
    s.logo(34, 29, 11)
    s.text(52, 34, f"{AIRLINE}  {AIRLINE_EN}", size=11, bold=True, color=(1, 1, 1))
    s.text(430, 34, "BOARDING PASS 登機證", size=11, bold=True, color=(1, 1, 1))
    s.page.draw_line((430, 48), (430, 240), color=LINE, width=0.8, dashes="[3 3] 0")
    s.text(24, 70, "姓名：CHEN/YICHUN MS", size=12, bold=True)
    s.text(24, 96, "FROM 出發", size=7, color=GRAY)
    s.text(24, 116, "東京成田 NRT", size=16, bold=True)
    s.text(200, 96, "TO 抵達", size=7, color=GRAY)
    s.text(200, 116, "台北桃園 TPE", size=16, bold=True)
    for i, (k, v) in enumerate(
        [
            ("FLIGHT 班機", "ZZ198"),
            ("DATE 日期", "10JUL"),
            ("BOARDING 登機時間", "10:00"),
            ("GATE 登機門", "21"),
            ("SEAT 座位", "32A"),
        ]
    ):
        x = 24 + i * 80
        s.text(x, 142, k, size=6.5, color=GRAY)
        s.text(x, 160, v, size=13, bold=True)
    s.text(24, 186, "CLASS 艙等：Y　SEQ：087　ETKT 999 2100000000", size=8, color=GRAY)
    s.text(
        24,
        200,
        "登機門於起飛前 20 分鐘關閉 Gate closes 20 minutes before departure",
        size=7,
        color=GRAY,
    )
    s.barcode(24, 206, 220, 26, seed=198)
    s.text(442, 70, "CHEN/YICHUN MS", size=9, bold=True)
    s.text(442, 92, "NRT → TPE", size=12, bold=True)
    s.text(442, 112, "ZZ198　10JUL", size=10)
    s.text(442, 130, "SEAT 32A　GATE 21", size=10)
    s.text(442, 148, "SEQ 087", size=9, color=GRAY)
    s.barcode(442, 160, 130, 40, seed=87)
    s.text(24, 247, FOOTER, size=6, color=GRAY)


def e_ticket(doc: pymupdf.Document) -> None:
    s = Sheet(doc)
    airline_header(s, "電子機票行程表暨收據", "E-TICKET ITINERARY RECEIPT")
    y = rows(
        s,
        40,
        92,
        [
            ("Passenger", "旅客姓名：陳怡君　CHEN/YICHUN MS"),
            ("Ticket Number", "機票號碼：999-2100000000"),
            ("Booking Reference", "訂位代號：K7Q2MD"),
            ("Issued by / Date", "開票單位：台北營業處　開票日期：2026-05-20"),
            ("Frequent Flyer", "會員號碼：FS 1234 5678"),
        ],
    )
    y += 26
    s.text(40, y, "行程 Itinerary", size=12, bold=True, color=NAVY)
    y += 10
    head = [
        "班機 Flight",
        "日期 Date",
        "出發 From",
        "抵達 To",
        "起飛 Dep",
        "抵達 Arr",
        "艙等 Class",
        "行李 Bag",
    ]
    xs = [40, 100, 170, 260, 350, 405, 460, 515]
    s.box((40, y, 555, y + 22), fill=(0.93, 0.95, 0.98))
    for x, h in zip(xs, head, strict=True):
        s.text(x + 4, y + 14, h, size=7.5, bold=True)
    y += 22
    for flight in (
        [
            "ZZ197",
            "2026-07-05",
            "台北 TPE",
            "東京 NRT",
            "08:30",
            "12:45",
            "Y 經濟",
            "23KG",
        ],
        [
            "ZZ198",
            "2026-07-10",
            "東京 NRT",
            "台北 TPE",
            "10:30",
            "13:30",
            "Y 經濟",
            "23KG",
        ],
    ):
        s.box((40, y, 555, y + 24))
        for x, v in zip(xs, flight, strict=True):
            s.text(x + 4, y + 16, v, size=9)
        y += 24
    y += 30
    s.text(40, y, "票價明細 Fare Details", size=12, bold=True, color=NAVY)
    y = rows(
        s,
        40,
        y + 10,
        [
            ("Fare", "票價：TWD 15,800"),
            ("Taxes / Fees", "稅金與燃油附加費：TWD 2,350"),
            ("Total", "合計：TWD 18,150"),
            ("Form of Payment", "付款方式：信用卡 VI XXXX XXXX XXXX 4821"),
        ],
    )
    s.text(
        40,
        y + 30,
        "本行程表暨收據為電子機票之憑證，請妥善保存。出發當日請提早 2 小時至機場辦理報到。",
        size=8.5,
        color=GRAY,
    )
    s.text(
        40,
        y + 44,
        "This itinerary receipt is your record of the electronic ticket. Please keep it for your reference.",
        size=7.5,
        color=GRAY,
    )
    s.footer()


def boarding_certificate(doc: pymupdf.Document) -> None:
    s = Sheet(doc)
    airline_header(s, "搭機證明", "CERTIFICATE OF BOARDING")
    s.text(40, 100, "證明編號 No.：TPE-COB-20260712-0087", size=9, color=GRAY)
    s.text(40, 130, "茲證明下列旅客確實搭乘本公司班機，特此證明。", size=11)
    s.text(
        40,
        146,
        "This is to certify that the passenger below travelled on the following flight.",
        size=8.5,
        color=GRAY,
    )
    y = rows(
        s,
        40,
        168,
        [
            ("Passenger Name", "旅客：陳怡君　CHEN/YICHUN MS"),
            ("Ticket Number", "票號：999-2100000000"),
            ("Flight No. / Date", "班機：ZZ 198　日期：2026-07-10"),
            ("Sector", "航段：東京成田 NRT → 台北桃園 TPE"),
            ("Seat / Class", "座位：32A　艙等：經濟艙"),
            ("Date of Issue", "開立日期：2026-07-12"),
        ],
    )
    s.text(330, y + 70, "台北營業處 Taipei Office", size=10)
    s.stamp(500, y + 120, AIRLINE_EN[:6], "台北營業處", "2026.07.12")
    s.footer()


def receipt(doc: pymupdf.Document) -> None:
    s = Sheet(doc, 226, 420)
    lines = [
        ("示範便利商店", 13, True),
        ("成田機場第一航廈店", 9, False),
        ("統一編號 12345678", 8, False),
        ("電子發票證明聯", 11, True),
        ("2026-07-10 11:42:08", 8, False),
        ("發票號碼 ZZ-12345678", 9, False),
        ("隨機碼 3825　總計 64", 8, False),
    ]
    y = 30
    for t, size, bold in lines:
        w = (
            len(t) * size
            if not t.isascii()
            else pymupdf.get_text_length(t, fontsize=size)
        )
        s.text(max(12, (226 - w) / 2), y, t, size=size, bold=bold)
        y += size + 8
    s.barcode(28, y, 170, 22, seed=64)
    y += 40
    s.line(12, y, 214, y)
    y += 16
    for item, price in (("礦泉水 600ml", "25"), ("御飯糰 鮭魚", "39")):
        s.text(14, y, item, size=9)
        s.text(185, y, price, size=9)
        y += 16
    s.line(12, y, 214, y)
    y += 16
    for k, v in (("合計", "64"), ("現金", "100"), ("找零", "36")):
        s.text(14, y, k, size=9, bold=k == "合計")
        s.text(185, y, v, size=9, bold=k == "合計")
        y += 16
    s.text(14, y + 10, "退貨請持本聯及商品至原門市辦理", size=7, color=GRAY)
    s.text(8, 412, "測試範例（資料虛構）SAMPLE ONLY", size=6, color=GRAY)


def claim_form(doc: pymupdf.Document) -> None:
    """富邦「個人保險理賠申請書」第 1 頁，用手寫風格字型填上假資料。"""
    blank = pymupdf.open(BLANK_FORM)
    doc.insert_pdf(blank, from_page=0, to_page=0)
    page = doc[-1]
    page.insert_font(fontname="H", fontbuffer=font(FONT_HAND))
    rnd = random.Random(1105)

    def hand(x: float, y: float, s: str, size: float = 10.5) -> None:
        # 手寫：每段字的位置、大小、傾斜都有一點不同
        tilt = pymupdf.Matrix(rnd.uniform(-1.8, 1.8))
        at = pymupdf.Point(x + rnd.uniform(-0.8, 0.8), y + rnd.uniform(-0.8, 0.8))
        page.insert_text(
            at,
            s,
            fontname="H",
            fontsize=size + rnd.uniform(-0.4, 0.6),
            color=INK,
            morph=(at, tilt),
        )

    def tick(box_x: float, box_y: float) -> None:
        # 打勾：□ 的左上角座標
        p0 = pymupdf.Point(box_x + 1.5, box_y + 5.5)
        p1 = pymupdf.Point(box_x + 4.5, box_y + 9.5)
        p2 = pymupdf.Point(box_x + 11, box_y - 1.5)
        page.draw_polyline([p0, p1, p2], color=INK, width=1.3)

    tick(364, 74)  # 個人保險
    tick(207, 87)  # F 旅遊不便險
    tick(472, 129)  # 事故發生地點：海外
    hand(503, 138, "日本成田機場", 9)
    for x, v in ((86, "115"), (122, "7"), (148, "10"), (183, "9"), (212, "30")):
        hand(x, 156, v)  # 事故發生：年月日時分
    hand(98, 180, "陳怡君", 12)  # 被保險人
    for x, v in ((204, "79"), (241, "3"), (269, "12")):
        hand(x, 180, v)  # 出生年月日
    hand(346, 180, "A223456789", 10.5)
    tick(482, 165)  # 就診身分：健保
    hand(92, 204, "同被保險人", 10)  # 事故人
    hand(498, 205, "本人", 10)
    hand(106, 224, "02-2345-6789", 9.5)  # 市話
    hand(262, 224, "0912-345-678", 10)  # 手機
    hand(86, 239, "106", 9)  # 郵遞區號
    hand(143, 252, "台北", 10)
    hand(203, 252, "大安", 10)
    hand(264, 248, "復興南路一段 100 號 5 樓", 10)
    hand(114, 267, "yichun.chen@example.com", 9.5)
    tick(165, 289)  # 是否投保其它保險公司：否
    tick(335, 304)  # 工作中發生：否
    for i, s in enumerate(
        (
            "7/10 從東京成田機場搭 ZZ198 回台北，原定 10:30 起飛，",
            "航空公司說因為天候不佳(強風)延後，實際 16:45 才起飛，",
            "在機場等了 6 小時多，自己買了午餐和晚餐。航空公司開的證明附在後面。",
        )
    ):
        hand(38, 330 + i * 15, s, 10)
    tick(55, 392)  # 付款方式：匯款
    tick(356, 392)  # 帳戶為：受益人
    hand(92, 415, "陳怡君 / A223456789", 10)  # 戶名／身分證號
    hand(362, 427, "示範商業", 9.5)  # ○○銀行
    hand(478, 427, "大安", 9.5)  # ○○分行
    hand(128, 447, "999", 10)  # 金融機構代號
    hand(292, 447, "1234-5678-9012", 10)  # 帳號
    hand(146, 692, "陳怡君", 14)  # 立同意書人（被保險人／受益人簽章）
    hand(152, 751, "陳怡君", 11)  # 送件人
    for x, v in ((456, "115"), (507, "7"), (541, "15")):
        hand(x, 779, v)  # 中華民國年月日
    page.insert_font(fontname="L", fontbuffer=font(FONT_LIGHT))
    page.insert_text((24, 836), FOOTER, fontname="L", fontsize=6, color=GRAY)


# ---- 有問題的版本：讓 6 號在不同步驟出現不同結果 ----

PASSWORD = "0710"  # 有密碼的範例：開檔密碼（使用者自己打開看得到內容，6 號讀不到）


def baggage_delay(doc: pymupdf.Document) -> None:
    """行李延誤證明：雖然也有「延誤證明」，仍不可對到班機延誤的 D01。"""
    s = Sheet(doc)
    airline_header(s, "行李延誤證明書", "BAGGAGE DELAY CERTIFICATE")
    s.text(40, 100, "行李事故報告編號 PIR No.：TPEZZ41873", size=9, color=GRAY)
    s.text(
        40, 128, "茲證明下列旅客之託運行李未隨班機抵達，延誤送達，特此證明。", size=11
    )
    s.text(
        40,
        144,
        "This is to certify that the checked baggage of the passenger below was delayed.",
        size=8.5,
        color=GRAY,
    )
    y = rows(
        s,
        40,
        166,
        [
            ("Passenger Name", "旅客姓名：陳怡君　CHEN/YICHUN MS"),
            ("Flight No. / Date", "班機：ZZ 198　日期：2026-07-10"),
            ("Sector", "航段：東京成田 NRT → 台北桃園 TPE"),
            ("Bag Tag No.", "行李牌號碼：ZZ 412873"),
            ("Bag Type / Color", "行李：硬殼行李箱　灰色　23KG"),
            ("Reported at", "申報時間：2026-07-10 20:15　桃園機場第二航廈"),
            ("Delivered at", "送達時間：2026-07-12 14:30（送至旅客住處）"),
            ("Length of Delay", "行李延誤時間：約 42 小時"),
            ("Issued at", "開立單位：桃園機場行李服務處 Baggage Service"),
        ],
    )
    s.text(
        40,
        y + 30,
        "註：本證明僅證明行李延誤送達事實，供旅客向保險公司申請行李延誤理賠使用。",
        size=9,
        color=GRAY,
    )
    s.text(330, y + 80, "行李服務處 Baggage Service：", size=10)
    s.page.insert_font(fontname="H", fontbuffer=font(FONT_HAND))
    s.page.insert_text((480, y + 80), "王佩珊", fontname="H", fontsize=15, color=INK)
    s.stamp(500, y + 130, AIRLINE_EN[:6], "行李服務處", "2026.07.12")
    s.footer()


def ticket_boarding(doc: pymupdf.Document) -> None:
    """機票和登機證印在同一張：辨識出兩者後，這一份即可完成 D02 待辦。"""
    s = Sheet(doc)
    airline_header(s, "機票暨登機證", "E-TICKET & BOARDING PASS")
    y = rows(
        s,
        40,
        92,
        [
            ("Passenger", "旅客姓名：陳怡君　CHEN/YICHUN MS"),
            ("Ticket Number", "票號：999-2100000000"),
            ("Booking Reference", "訂位代號：K7Q2MD"),
            ("Flight / Date", "班機：ZZ 198　日期：2026-07-10"),
            ("Sector", "航段：東京成田 NRT → 台北桃園 TPE"),
            ("Departure", "起飛時間：10:30　登機時間：10:00"),
            ("Gate / Seat", "登機門：21　座位：32A　艙等：Y 經濟"),
            ("Baggage", "託運行李：23KG"),
        ],
    )
    y += 30
    s.box((40, y, 555, y + 120), color=NAVY, width=1)
    s.text(52, y + 22, "BOARDING PASS", size=11, bold=True, color=NAVY)
    s.text(52, y + 46, "CHEN/YICHUN MS", size=12, bold=True)
    s.text(52, y + 70, "NRT → TPE　ZZ198　10JUL　SEAT 32A　GATE 21", size=11)
    s.text(52, y + 90, "SEQ 087　ETKT 999 2100000000", size=8, color=GRAY)
    s.barcode(360, y + 18, 180, 60, seed=4021)
    s.text(
        40,
        y + 150,
        "本單據於自助報到機列印，請於登機時出示。Please present this document at the gate.",
        size=8.5,
        color=GRAY,
    )
    s.footer()


def ticket_locked(doc: pymupdf.Document) -> None:
    """電子機票加上開檔密碼（像有些航空公司寄來的 PDF）；存檔時才加密，見 main。"""
    e_ticket(doc)


# 日本行程 demo：旅客在東京吃壞肚子，到診所看門診（#233）。診所是虛構的。
# 日本給外國旅客的單據多半是日英並列；日期用西曆（2026年7月8日），金額用円。
# 字型用 macOS 內建的日文字型（凸版文久ゴシック）：同一個漢字，中文字型的字形一看就像中文。
FONT_JA = next(
    Path("/System/Library/AssetsV2").glob("**/ToppanBunkyuGothicPr6N.ttc"),
    Path("ToppanBunkyuGothicPr6N.ttc"),
)
JA_BOLD, JA_REGULAR = 0, 1  # Demibold、Regular
TEAL = (0.0, 0.42, 0.45)
CLINIC_CORP = "医療法人社団 デモ会"
CLINIC_JA = "東京デモ国際クリニック"
CLINIC_EN = "TOKYO DEMO INTERNATIONAL CLINIC"
CLINIC_LINES = (
    "〒100-0000 東京都千代田区デモ町1-2-3",
    "1-2-3 Demo-cho, Chiyoda-ku, Tokyo 100-0000, Japan",
    "TEL 03-0000-0000　内科・小児科　Internal Medicine / Pediatrics",
)


class JaSheet(Sheet):
    """日文單據：一般與粗體換成日文字型（欄位、印章、頁尾都跟著用）。"""

    def __init__(self, doc: pymupdf.Document, width: float = 595, height: float = 842):
        self.page = doc.new_page(width=width, height=height)
        self.page.insert_font(fontname="L", fontbuffer=font(FONT_JA, JA_REGULAR))
        self.page.insert_font(fontname="B", fontbuffer=font(FONT_JA, JA_BOLD))


def clinic_header(s: Sheet, title_ja: str, title_en: str) -> None:
    w = s.page.rect.width
    s.box((0, 0, w, 6), fill=TEAL, color=None)
    # 虛構診所的標誌：圓形裡一個十字
    s.page.draw_circle((40, 44), 16, color=None, fill=TEAL)
    s.page.draw_rect(pymupdf.Rect(37, 34, 43, 54), color=None, fill=(1, 1, 1))
    s.page.draw_rect(pymupdf.Rect(30, 41, 50, 47), color=None, fill=(1, 1, 1))
    s.text(64, 26, CLINIC_CORP, size=7.5, color=GRAY)
    s.text(64, 42, CLINIC_JA, size=14, bold=True, color=TEAL)
    s.text(64, 55, CLINIC_EN, size=8.5, bold=True, color=TEAL)
    for i, line in enumerate(CLINIC_LINES):
        s.text(64, 68 + i * 10, line, size=7, color=GRAY)
    s.line(24, 106, w - 24, 106)
    tw = len(title_ja) * 20 + (len(title_ja) - 1) * 10
    # 日本的單據標題常把字距拉開（「診　断　書」）
    s.text((w - tw) / 2, 136, "　".join(title_ja), size=20, bold=True)
    ew = pymupdf.get_text_length(title_en, fontsize=10)
    s.text((w - ew) / 2, 152, title_en, size=10, color=GRAY)


WEEKDAYS_JA = "月火水木金土日"


def _ja_date(d: date) -> str:
    return f"{d.year}年{d.month}月{d.day}日"


def medical_certificate(
    doc: pymupdf.Document, with_diagnosis: bool, visit: date = date(2026, 7, 8)
) -> None:
    """診断書（D13）：日英並列。with_diagnosis=False 拿掉病名那一列（不合格範例）。

    visit 是受診日；改成保險期間外的日期就是「需要你確認」的範例。
    """
    s = JaSheet(doc)
    clinic_header(s, "診断書", "MEDICAL CERTIFICATE")
    s.text(40, 178, f"No. MC-{visit:%Y%m%d}-0153", size=8.5, color=GRAY)
    items = [
        ("氏名 / Name", "Patient name: CHEN YICHUN 様"),
        ("生年月日 / Date of birth", "1992年3月14日生（34歳）"),
        ("国籍 / Nationality", "台湾 Taiwan（旅行者）"),
        (
            "受診日 / Date of visit",
            f"受診日：{_ja_date(visit)}（{WEEKDAYS_JA[visit.weekday()]}）",
        ),
    ]
    if with_diagnosis:
        items.append(("病名 / Diagnosis", "病名：急性胃腸炎（Acute gastroenteritis）"))
    items += [
        ("症状 / Symptoms", "嘔吐と下痢があり、軽い脱水がみられました"),
        ("治療 / Treatment", "外来にて点滴を行い、内服薬を処方しました"),
        ("備考 / Remarks", "約2日間の自宅安静を要するものと認めます"),
    ]
    y = rows(s, 40, 190, items)
    s.text(40, y + 30, "上記のとおり診断いたします。", size=10.5)
    s.text(
        40,
        y + 46,
        "I hereby certify that the above is true and correct.",
        size=8,
        color=GRAY,
    )
    s.text(300, y + 84, f"発行日：{_ja_date(visit)}", size=9.5)
    s.text(300, y + 100, f"{CLINIC_CORP}　{CLINIC_JA}", size=9)
    s.text(300, y + 120, "医師　Physician：", size=10)
    s.page.insert_font(fontname="H", fontbuffer=font(FONT_HAND))
    s.page.insert_text(
        (400, y + 122), "デモ 太郎", fontname="H", fontsize=14, color=INK
    )
    s.stamp(500, y + 165, "DEMO", "東京", f"{visit:%Y.%m.%d}")
    s.footer()


def medical_receipt(doc: pymupdf.Document, with_amount: bool) -> None:
    """領収書（D14）：日英並列的自費診療收據。with_amount=False 拿掉所有金額（不合格範例）。"""
    s = JaSheet(doc, 420, 595)
    clinic_header(s, "領収書", "RECEIPT")
    y = rows(
        s,
        20,
        170,
        [
            ("Receipt No.", "領収書番号：R-20260708-0153"),
            ("Patient", "Patient name: CHEN YICHUN 様"),
            ("Date", "診療日：2026年7月8日（水）"),
            ("Department", "診療科：内科（外来）"),
            ("Payment", "区分：自費（保険外診療）"),
        ],
        w=380,
    )
    y += 26
    s.text(24, y, "項目 Description", size=9, bold=True)
    if with_amount:
        s.text(320, y, "金額（円）", size=9, bold=True)
    y += 8
    s.line(20, y, 400, y)
    y += 18
    lines = (
        ("初診料 First visit fee", "2,880"),
        ("処置料（点滴）Treatment / IV", "3,200"),
        ("投薬料 Medicine", "1,560"),
        ("文書料（診断書）Medical certificate fee", "3,000"),
    )
    for item, price in lines:
        s.text(24, y, item, size=9.5)
        if with_amount:
            s.text(340, y, price, size=9.5)
        y += 20
    s.line(20, y - 8, 400, y - 8)
    if with_amount:
        s.text(24, y + 10, "合計 Total：¥10,640", size=12, bold=True)
        s.text(24, y + 28, "お支払い方法：クレジットカード（Credit card）", size=8.5)
    s.text(24, y + 48, "上記の金額を正に領収いたしました。", size=9)
    s.text(24, y + 62, "お大事になさってください。Get well soon.", size=8, color=GRAY)
    s.stamp(340, y + 100, "DEMO", "領収", "2026.07.08")
    s.footer()


# 證件影本（D24）：欄位、用語與排列照台灣護照資料頁、國民身分證，做成黑白影本的樣子。
# 刻意不畫國徽、底紋、照片等防偽設計（不做成可以拿去偽造的模板）；資料全部虛構，
# 機讀碼的檢查碼也沒有照規則算。
COPY_GRAY = (0.93, 0.93, 0.93)
CLAIM_ONLY = "僅供申請旅遊保險理賠使用"


def _photo_placeholder(s: Sheet, x: float, y: float, w: float, h: float) -> None:
    s.box((x, y, x + w, y + h), fill=(0.82, 0.82, 0.82), color=GRAY)
    s.text(x + w / 2 - 14, y + h / 2 + 4, "（照片）", size=9, color=GRAY)


def _field(s: Sheet, x: float, y: float, label: str, value: str) -> None:
    """證件上的欄位：小字欄名在上、值在下（整行寫入，去識別化才認得到標籤）。"""
    s.text(x, y, label, size=6.5, color=GRAY)
    s.text(x, y + 13, value, size=10, bold=True)


def _claim_only_stamp(s: Sheet, x: float, y: float) -> None:
    """影本上手寫的「僅供理賠使用」，斜斜地壓在證件上（台灣常見的影本註記）。"""
    s.page.insert_font(fontname="H", fontbuffer=font(FONT_HAND))
    s.page.insert_text(
        (x, y),
        CLAIM_ONLY,
        fontname="H",
        fontsize=20,
        color=INK,
        morph=(pymupdf.Point(x, y), pymupdf.Matrix(-12)),
    )


def passport_copy(doc: pymupdf.Document) -> None:
    """台灣護照資料頁的影本。"""
    s = Sheet(doc)
    s.text(40, 50, "護照資料頁影本", size=12, bold=True)
    x0, y0, w, h = 40, 64, 515, 330
    s.box((x0, y0, x0 + w, y0 + h), fill=COPY_GRAY, color=GRAY)
    s.text(x0 + 16, y0 + 24, "中華民國", size=13, bold=True)
    s.text(x0 + 82, y0 + 24, "REPUBLIC OF CHINA", size=10, bold=True)
    s.text(x0 + 340, y0 + 24, "護照　PASSPORT", size=12, bold=True)
    _photo_placeholder(s, x0 + 16, y0 + 44, 110, 140)
    fx, fy = x0 + 146, y0 + 50
    _field(s, fx, fy, "型式 / Type", "P")
    _field(s, fx + 90, fy, "國碼 / Code", "TWN")
    _field(s, fx + 190, fy, "護照號碼 / Passport No.", "X00000000")
    _field(s, fx, fy + 36, "姓名 / Name", "Name: CHEN, YI-CHUN")
    s.text(fx + 190, fy + 49, "中文姓名：陳怡君", size=10, bold=True)
    _field(s, fx, fy + 72, "國籍 / Nationality", "REPUBLIC OF CHINA")
    _field(s, fx, fy + 108, "出生日期 / Date of birth", "14 MAR 1992")
    _field(s, fx + 190, fy + 108, "身分證統一編號 / Personal Id. No.", "A223456789")
    _field(s, fx, fy + 144, "性別 / Sex", "F")
    _field(s, fx + 90, fy + 144, "出生地 / Place of birth", "TAIWAN")
    _field(s, fx, fy + 180, "發照日期 / Date of issue", "01 JAN 2021")
    _field(s, fx + 190, fy + 180, "效期截止日期 / Date of expiry", "01 JAN 2031")
    _field(s, x0 + 16, fy + 180, "發照機關 / Authority", "外交部")
    for i, mrz in enumerate(
        (
            "P<TWNCHEN<<YI<CHUN<<<<<<<<<<<<<<<<<<<<<<<<<<",
            "X000000000TWN9203140F3101011A223456789<<<<00",
        )
    ):
        s.page.insert_text(
            (x0 + 22, y0 + h - 34 + i * 18),
            mrz,
            fontname="cour",
            fontsize=11,
            color=(0, 0, 0),
        )
    _claim_only_stamp(s, x0 + 150, y0 + 200)
    s.footer()


POLICY_BLANK = HERE / "chubb_policy_blank.pdf"
# 真實保單的投保須知、個資告知與附的條款 6 頁（原第 2、5～11 頁，沒有個人資料），原樣放在承保內容後面；
# 字型是嵌好的，不要再裁（會變亂碼），所以這兩個檔案超過 500KB，pre-commit 與測試都已放行
POLICY_ATTACHMENTS = HERE / "chubb_policy_attachments.pdf"


def travel_policy(doc: pymupdf.Document) -> None:
    """保險單（D25）：安達網路投保的「旅行平安綜合保險保險單」進階型，填上 demo 的虛構資料（日本）。

    底稿是組員提供的真實保單第 3 頁，產生 chubb_policy_blank.pdf 時已把原本的姓名、
    保單號碼、保險期間、旅遊地區、保費與列印時間整段移除（不是蓋上白色方塊），
    出生日期與身分證字號原本就塗黑；這裡只把虛構資料寫回同樣的位置。
    """
    # 自己畫的頁面先放在 own 裁字型；附件頁的字型原本就是裁好嵌進去的，再裁一次會變亂碼，
    # 所以最後才照順序接在一起（main 對這份不再呼叫 subset_fonts）
    own = pymupdf.open()
    _policy_cover_page(own)
    own.insert_pdf(pymupdf.open(POLICY_BLANK))
    page = own[-1]
    page.insert_font(fontname="L", fontbuffer=font(FONT_LIGHT))
    for x, y, text in (
        (57.7, 87.5, "保單號碼：ECITWAA0000153"),
        (57.7, 99.5, "要保人： 陳怡君"),
        (
            57.7,
            111.6,
            "保險期間：自05-Jul-2026 00時00分至11-Jul-2026 24時00分止， 共7天",
        ),
        (57.7, 123.6, "被保險人： 陳怡君 等1人"),
        (57.7, 135.8, "旅遊地區：日本"),
        (468.3, 135.8, "總保險費：NT$658"),
        (57.7, 319.8, "陳怡君 等1 人"),
    ):
        page.insert_text((x, y), text, fontname="L", fontsize=10)
    page.insert_text(
        (407.9, 508.0),
        "列印時間: 01/07/2026 21:12:05(第1次列印)",
        fontname="L",
        fontsize=7,
    )
    page.insert_text(
        (24, page.rect.height - 12), FOOTER, fontname="L", fontsize=6.5, color=GRAY
    )
    _policy_advanced_page(own)
    _policy_receipt_page(own)
    own.subset_fonts()
    attachments = pymupdf.open(POLICY_ATTACHMENTS)
    doc.insert_pdf(own, from_page=0, to_page=0)  # 封面
    doc.insert_pdf(attachments, from_page=0, to_page=0)  # 旅遊保險網路投保投保人須知
    doc.insert_pdf(own, from_page=1)  # 承保內容、進階型續頁、保險費收據
    doc.insert_pdf(attachments, from_page=1)  # 個資告知與附的條款 6 頁


# 進階型多出來的商品（CHUBB05～08）與保障內容，金額同方案資料（保額 200 萬）
ADVANCED_ROWS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    (
        "安達產物個人海外旅行不便保險(C)",
        (
            ("旅程取消保險(實支實付)", "80,000元"),
            ("旅程更改保險(實支實付)", "80,000元"),
            ("班機延誤保險(定額給付-累進式)", "6,000元"),
            ("行李延誤保險(定額給付)", "6,000元"),
            ("行李損失保險(定額給付)", "6,000元"),
            ("旅行文件損失保險(定額給付)", "3,000元"),
        ),
    ),
    (
        "安達產物個人海外旅行綜合保險",
        (
            ("班機改降補償保險", "5,000元"),
            ("劫機補償保險", "5,000元"),
            ("第三人責任保險(無自負額)", "5,000,000元"),
            ("旅行期間居家竊盜保險", "60,000元"),
            ("現金竊盜損失保險", "3,000元"),
            ("信用卡盜用損失保險", "30,000元"),
            ("等待返國住宿費用保險", "5,000元"),
            ("旅行期間居家火災臨時住宿補償保險", "10,000元"),
            ("行動電話被竊損失保險", "2,000元"),
        ),
    ),
    (
        "安達產物個人海外旅行不便保險旅程取消費用傳染病及檢疫給付附加條款",
        (("旅程取消保險(法定傳染病)", "80,000元"),),
    ),
    (
        "安達產物個人海外旅行不便保險旅程更改費用傳染病及檢疫給付附加條款",
        (("旅程更改保險(法定傳染病)", "80,000元"),),
    ),
)


def _policy_advanced_page(doc: pymupdf.Document) -> None:
    """保險單第 2 頁：進階型加上的不便險與綜合險商品。商品名稱逐字用條款庫的全名，方案才比對得到進階型。"""
    page = doc.new_page()
    page.insert_font(fontname="L", fontbuffer=font(FONT_LIGHT))
    page.insert_text(
        (57.7, 60), "旅行平安綜合保險保險單（續）", fontname="L", fontsize=12
    )
    page.insert_text((57.7, 78), "保單號碼：ECITWAA0000153", fontname="L", fontsize=10)
    y = 104.0
    for product, rows in ADVANCED_ROWS:
        page.insert_text((57.7, y), product, fontname="L", fontsize=9.5)
        y += 14
        for name, amount in rows:
            page.insert_text((72, y), name, fontname="L", fontsize=9)
            page.insert_text((440, y), amount, fontname="L", fontsize=9)
            y += 13
        y += 8
    page.insert_text((57.7, y + 4), "(以下空白)", fontname="L", fontsize=9)
    page.insert_text(
        (24, page.rect.height - 12), FOOTER, fontname="L", fontsize=6.5, color=GRAY
    )


def _policy_cover_page(doc: pymupdf.Document) -> None:
    """保險單封面：收件地址、保單號碼與標題（真實保單的第 1 頁版面，資料虛構）。"""
    page = doc.new_page()
    page.insert_font(fontname="L", fontbuffer=font(FONT_LIGHT))
    for y, text in (
        (96, "100"),
        (108, "臺北市示範區"),
        (120, "陳怡君"),
        (132, "保單號碼：ECITWAA0000153"),
        (144, "郵寄方式：TravelForm:Epolicy"),
    ):
        page.insert_text((228, y), text, fontname="L", fontsize=8.5)
    page.insert_text((24, 170), "-" * 150, fontname="L", fontsize=5, color=GRAY)
    page.insert_text(
        (75, 205), "安達產物保險旅行平安綜合保險保險單", fontname="L", fontsize=20
    )
    page.insert_text(
        (24, page.rect.height - 12), FOOTER, fontname="L", fontsize=6.5, color=GRAY
    )


def _policy_receipt_page(doc: pymupdf.Document) -> None:
    """保險費收據正本（真實保單的第 4 頁）：保單號碼、被保險人、期間與總保費，沒有印章。"""
    page = doc.new_page()
    page.insert_font(fontname="L", fontbuffer=font(FONT_LIGHT))
    page.insert_text((300, 52), "Series No : ECITWAA0000153", fontname="L", fontsize=8)
    page.insert_text((300, 64), "Date : 01 Jul 2026", fontname="L", fontsize=8)
    page.insert_text((57.7, 52), "CHUBB　保險費收據正本", fontname="L", fontsize=11)
    for y, label, value in (
        (92, "保單號碼", "ECITWAA0000153"),
        (112, "被保險人", "陳怡君 等1 人"),
        (132, "保險期間", "05-Jul-2026 00時00分至11-Jul-2026 24時00分止，共7天"),
        (152, "繳款人", "陳怡君"),
        (172, "總保費", "NT$658.-"),
    ):
        page.insert_text((57.7, y), label, fontname="L", fontsize=9)
        page.insert_text((150, y), value, fontname="L", fontsize=9)
    page.insert_text(
        (57.7, 210),
        "茲收到上開保險費無誤，並經本公司或代收機構於收款人處蓋章為憑。",
        fontname="L",
        fontsize=8,
    )
    page.insert_text(
        (24, page.rect.height - 12), FOOTER, fontname="L", fontsize=6.5, color=GRAY
    )


def id_card_copy(doc: pymupdf.Document) -> None:
    """國民身分證正反面影本（同一頁，上正下反）。"""
    s = Sheet(doc)
    s.text(40, 50, "國民身分證正反面影本", size=12, bold=True)
    # 正面
    x0, y0, w, h = 110, 66, 375, 236
    s.box((x0, y0, x0 + w, y0 + h), fill=COPY_GRAY, color=GRAY)
    s.text(x0 + 70, y0 + 28, "中華民國國民身分證", size=15, bold=True)
    s.text(x0 + 18, y0 + 66, "姓名 陳怡君", size=13, bold=True)
    s.text(x0 + 18, y0 + 100, "出生 民國 81 年 3 月 14 日", size=10)
    s.text(x0 + 18, y0 + 126, "性別 女", size=10)
    s.text(x0 + 18, y0 + 152, "發證日期 民國 110 年 7 月 1 日（北市）換發", size=9)
    s.text(x0 + 18, y0 + 196, "統一編號 A223456789", size=12, bold=True)
    _photo_placeholder(s, x0 + 262, y0 + 46, 94, 120)
    _claim_only_stamp(s, x0 + 40, y0 + 150)
    # 反面
    y1 = y0 + h + 30
    s.box((x0, y1, x0 + w, y1 + h), fill=COPY_GRAY, color=GRAY)
    s.text(x0 + 18, y1 + 34, "父 陳○○　　母 林○○", size=10)
    s.text(x0 + 18, y1 + 62, "配偶", size=10)
    s.text(x0 + 18, y1 + 90, "役別", size=10)
    s.text(x0 + 18, y1 + 118, "出生地 臺灣省臺北市", size=10)
    s.text(x0 + 18, y1 + 146, "住址 臺北市示範區示範路一段 1 號 5 樓", size=10)
    s.barcode(x0 + 18, y1 + 176, 220, 30, seed=81)
    s.text(x0 + 250, y1 + 200, "0000000000", size=8, color=GRAY)
    _claim_only_stamp(s, x0 + 40, y1 + 130)
    s.footer()


SAMPLE_FILES = {
    "delay": lambda d: delay_certificate(d, with_times=True),
    "delay_missing": lambda d: delay_certificate(d, with_times=False),
    "delay_out_of_period": lambda d: delay_certificate(
        d, with_times=True, flight=OUT_OF_PERIOD
    ),
    "boarding_pass": boarding_pass,
    "ticket": e_ticket,
    "boarding_proof": boarding_certificate,
    "claim_form": claim_form,
    "receipt": receipt,
    "baggage_delay": baggage_delay,
    "ticket_boarding": ticket_boarding,
    "ticket_locked": ticket_locked,
    "med_certificate": lambda d: medical_certificate(d, with_diagnosis=True),
    "med_certificate_no_diagnosis": lambda d: medical_certificate(
        d, with_diagnosis=False
    ),
    "med_certificate_out_of_period": lambda d: medical_certificate(
        d, with_diagnosis=True, visit=OUT_OF_PERIOD
    ),
    "med_receipt": lambda d: medical_receipt(d, with_amount=True),
    "med_receipt_no_amount": lambda d: medical_receipt(d, with_amount=False),
    "med_passport_copy": passport_copy,
    "med_claim_form": chubb_claim_form,
    "med_id_card_copy": id_card_copy,
    "med_policy": travel_policy,
}
LOCKED = {"ticket_locked"}
NO_SUBSET = {"med_policy"}  # 附件頁的字型已經裁好，travel_policy 自己處理
MEDICAL = [name for name in SAMPLE_FILES if name.startswith("med_")]


# 從 delay.pdf 刪掉幾行做出的延誤證明（不用字型，任何電腦都能重做）：檔名 → 要刪掉的文字
DELAY_REDACTED = {
    # 原定、實際起飛時間與延誤時數都沒有：不合格（合格規範「延誤期間」）
    "delay_no_period": (
        "Scheduled Departure (STD)",
        "原定起飛時間：10:30（當地時間）",
        "Actual Departure (ATD)",
        "實際起飛時間：16:45（當地時間）",
        "Length of Delay",
        "延誤時間：6 小時 15 分",
    ),
    # 沒寫延誤原因：照樣通過，附提醒（合格規範「延誤原因」只提醒）
    "delay_no_reason": ("Reason", "延誤原因：天候不佳（成田機場強風）Adverse weather"),
}


def make_redacted() -> None:
    for name, texts in DELAY_REDACTED.items():
        doc = pymupdf.open(HERE / "delay.pdf")
        page = doc[0]
        for text in texts:
            hits = page.search_for(text)
            if not hits:
                raise ValueError(f"delay.pdf 找不到要刪的文字：{text}")
            for rect in hits:
                page.add_redact_annot(rect, fill=(1, 1, 1))
        page.apply_redactions()
        out = HERE / f"{name}.pdf"
        doc.save(out, garbage=4, deflate=True)
        logger.info("%s：%d KB", out.name, out.stat().st_size // 1024)


# 照片版：把上面的 PDF 轉成圖，模擬手機拍照會遇到的問題，存成 JPG（送 Azure 讀字）
def _page_image(name: str, dpi: int = 150) -> Image.Image:
    with pymupdf.open(HERE / f"{name}.pdf") as doc:
        pix = doc[0].get_pixmap(dpi=dpi)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def _perspective_coeffs(
    dst: list[tuple[float, float]], src: list[tuple[float, float]]
) -> list[float]:
    """Image.transform 的 PERSPECTIVE 係數：輸出圖上的 dst 四角對到原圖的 src 四角。"""
    a = []
    for (x, y), (u, v) in zip(dst, src, strict=True):
        a.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        a.append([0, 0, 0, x, y, 1, -v * x, -v * y])
    b = [c for point in src for c in point]
    return np.linalg.solve(np.array(a, float), np.array(b, float)).tolist()


def on_table(page: Image.Image, tilt: float) -> Image.Image:
    """把紙放到桌上斜斜拍：四角往內縮成梯形，紙外是灰色桌面。"""
    w, h = page.size
    pad = int(w * 0.08)
    out_w, out_h = w + 2 * pad, h + 2 * pad
    # 上緣離鏡頭遠，看起來比較窄；再整張轉一點
    corners = [
        (pad + w * tilt, pad + h * 0.02),
        (pad + w * (1 - tilt * 0.6), pad),
        (pad + w * (1 + 0.02), pad + h),
        (pad - w * 0.03, pad + h * (1 - 0.01)),
    ]
    coeffs = _perspective_coeffs(corners, [(0, 0), (w, 0), (w, h), (0, h)])
    warped = page.transform(
        (out_w, out_h), Image.Transform.PERSPECTIVE, coeffs, Image.Resampling.BICUBIC
    )
    mask = Image.new("L", page.size, 255).transform(
        (out_w, out_h), Image.Transform.PERSPECTIVE, coeffs, Image.Resampling.BICUBIC
    )
    table = Image.new("RGB", (out_w, out_h), (92, 84, 76))
    table.paste(warped, mask=mask)
    return table


def shadow(img: Image.Image, strength: float) -> Image.Image:
    """手和手機擋到光：從左下角往右上變亮的陰影。"""
    w, h = img.size
    x = np.linspace(0, 1, w)[None, :]
    y = np.linspace(0, 1, h)[:, None]
    dark = np.clip(1 - strength * np.clip((1 - x) * 0.9 + y * 0.6 - 0.5, 0, 1), 0, 1)
    arr = np.asarray(img, float) * dark[..., None]
    return Image.fromarray(arr.astype(np.uint8))


def glare(img: Image.Image, center: tuple[float, float], radius: float) -> Image.Image:
    """燈光反光：一塊白色亮斑（中間幾乎全白，字看不到）。"""
    w, h = img.size
    yy, xx = np.mgrid[0:h, 0:w]
    d = np.hypot(xx - center[0] * w, (yy - center[1] * h) * 1.4) / (radius * w)
    light = np.clip(2.2 - 1.6 * d, 0, 1) ** 1.5
    arr = np.asarray(img, float)
    arr = arr + (255 - arr) * light[..., None]
    return Image.fromarray(arr.astype(np.uint8))


def noisy(img: Image.Image, seed: int) -> Image.Image:
    """手機感光元件的雜訊，讓照片不要太乾淨。"""
    rng = np.random.default_rng(seed)
    arr = np.asarray(img, float) + rng.normal(0, 4, (*img.size[::-1], 1))
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def save_jpg(img: Image.Image, out: Path, max_side: int = 1600) -> None:
    img.thumbnail((max_side, max_side))
    img.save(out, "JPEG", quality=80, optimize=True)


PHOTO_FILES = {
    # 手機拍登機證：斜拍＋陰影，字還清楚
    "photo_boarding_pass": lambda: noisy(
        shadow(on_table(_page_image("boarding_pass"), tilt=0.06), 0.45), 21
    ),
    # 延誤證明被燈光反光：亮斑蓋在起飛、抵達時間上
    "photo_delay_glare": lambda: noisy(
        glare(on_table(_page_image("delay"), tilt=0.03), (0.5, 0.385), 0.2), 22
    ),
    # 電子機票拍糊了：手震
    "photo_ticket_blurry": lambda: (
        on_table(_page_image("ticket", dpi=100), 0.04)
        .filter(ImageFilter.GaussianBlur(4.5))
        .resize((500, 700))
    ),
    # 手寫的理賠申請書拍照
    "photo_claim_form": lambda: noisy(
        shadow(on_table(_page_image("claim_form", dpi=200), tilt=0.04), 0.3), 24
    ),
    # 手機拍診所收據：斜拍＋陰影，字還清楚（東京診所，醫療 demo）
    "photo_med_receipt": lambda: noisy(
        shadow(on_table(_page_image("med_receipt"), tilt=0.05), 0.35), 25
    ),
}
PHOTO_MEDICAL = ["photo_med_receipt"]


def make_photos(names: list[str] | None = None) -> None:
    for name, build in PHOTO_FILES.items():
        if names is not None and name not in names:
            continue
        out = HERE / f"{name}.jpg"
        save_jpg(build(), out)
        logger.info("%s：%d KB", out.name, out.stat().st_size // 1024)


def main(only_medical: bool = False) -> None:
    """產生全部範例；only_medical 只重做醫療那幾份，其他已 commit 的檔案不動。"""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    for name, build in SAMPLE_FILES.items():
        if only_medical and name not in MEDICAL:
            continue
        doc = pymupdf.open()
        build(doc)
        if name not in NO_SUBSET:
            doc.subset_fonts()  # 再裁成這一份實際用到的字
        out = HERE / f"{name}.pdf"
        if name in LOCKED:
            doc.save(
                out,
                garbage=4,
                deflate=True,
                encryption=5,  # AES-256（pymupdf 的 PDF_ENCRYPT_AES_256，型別檔沒列出）
                user_pw=PASSWORD,
                owner_pw=PASSWORD,
            )
        else:
            doc.save(out, garbage=4, deflate=True)
        logger.info("%s：%d KB", out.name, out.stat().st_size // 1024)
    if only_medical:
        make_photos(PHOTO_MEDICAL)
        return
    make_redacted()
    make_photos()


if __name__ == "__main__":
    import sys

    main(only_medical="--medical" in sys.argv)
