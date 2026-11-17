"""班機延誤的結論判斷（程式決定，數字不由 LLM 產生，ADR-0004）。

依據班機延誤保險的承保範圍：實際出發時間較預定出發時間延誤四小時以上才給付；
特別不保事項第五款：未搭第一班替代交通工具不賠（不可抗力除外）。
富邦 FUBON03、安達 CHUBB05、臺產 TFMI01 是第 21、22 條，華南 HUANAN01 是第 27、28 條，文字相同。
"""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from app.contracts.common import Conclusion

THRESHOLD_HOURS = 4
DELAY_HOURS_KEY = "delay_hours"
# 旅客不記得起飛時間，只回答「延誤有沒有滿門檻」（#C）；值由門檻組出，不寫死「4」
DELAY_BAND_KEY = "delay_band"
DELAY_BAND_OVER = f"{THRESHOLD_HOURS} 小時以上"
DELAY_BAND_UNDER = f"未滿 {THRESHOLD_HOURS} 小時"
FIRST_ALT_KEY = "first_alt"
PLANNED_DATE_KEY = "date"  # 共同欄位的事故日期，當作原定起飛日期（Boris 11/07 確認）
ACTUAL_DATE_KEY = "dep_act_date"
# 事故日期是從實際起飛日期推定的（旅客沒說原定起飛日）：記推定出的日期，日期被改掉就不再成立（#451）。
# 跨午夜的延誤可能推錯一天，目前不處理
DATE_INFERRED_KEY = "date_inferred"
DATE_INFERRED_LABEL = "依實際起飛日期推定的事故日"
INFERRED_NOTE = "依實際起飛日期推定事故日"
_TIME = re.compile(r"^\s*(\d{1,2})[:：](\d{2})\s*$")
_DATE = re.compile(r"^\s*(\d{4})[/-](\d{1,2})[/-](\d{1,2})\s*$")


@dataclass(frozen=True)
class Judgement:
    conclusion: Conclusion
    reason: str


def _minutes(value: str | None) -> int | None:
    m = _TIME.match(value or "")
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    if hour > 23 or minute > 59:
        return None
    return hour * 60 + minute


def delay_minutes(
    planned_date: date | None,
    planned_time: str | None,
    actual_date: date | None,
    actual_time: str | None,
) -> int | None:
    """實際起飛減原定起飛，各用「日期 + 時間」算，所以超過 24 小時也對（#181）。

    任何一項缺少或格式不對回 None（呼叫端當成資訊不足）；實際早於原定會得到負數，
    由呼叫端當成資料有誤，不可當成「未滿四小時」。跟保險公司無關，各家共用。
    """
    start, end = _minutes(planned_time), _minutes(actual_time)
    if planned_date is None or actual_date is None or start is None or end is None:
        return None
    begin = datetime.combine(planned_date, time(), UTC) + timedelta(minutes=start)
    finish = datetime.combine(actual_date, time(), UTC) + timedelta(minutes=end)
    return int((finish - begin).total_seconds() // 60)


_DIGITS = {
    "零": 0,
    "一": 1,
    "二": 2,
    "兩": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_DURATION = re.compile(
    r"延誤了?(?:約|大約)?([0-9]+|[零一二兩三四五六七八九十]+)個?(?:小時|鐘頭)"
)
_NEGATIONS = ("沒有", "沒", "不是", "並非", "未")
_CORRECTIONS = ("不對", "其實", "應該是", "更正", "說錯", "搞錯")


def _number(word: str) -> int | None:
    if word.isdigit():
        return int(word)
    if "十" in word:
        tens, _, ones = word.partition("十")
        value = (_DIGITS.get(tens, 0) if tens else 1) * 10
        return value + (_DIGITS.get(ones, 0) if ones else 0)
    return _DIGITS.get(word) if len(word) == 1 else None


def parse_delay_hours(text: str) -> int | None:
    """旅客直接說出延誤幾小時（例：「延誤了六小時」）。由程式解析，不經 LLM。

    只接受明確肯定、只有一個時數的說法；遇到否定、更正或多個時數回 None，
    讓流程照常追問起飛時間（#180）。
    """
    found = list(_DURATION.finditer(text))
    if len(found) != 1:
        return None  # 說了多個時數（可能在更正）：不猜，照常問起飛時間
    m = found[0]
    if text[: m.start()].rstrip().endswith(_NEGATIONS):
        return None  # 「沒有延誤四小時」：否定的說法不算時數
    if any(word in text for word in _CORRECTIONS):
        return None  # 「延誤三小時，不對，是五小時」：有更正就不猜哪個才對
    return _number(m.group(1))


_NUMBER = "[0-9]+|[零一二兩三四五六七八九十]+"
_ANY_DURATION = re.compile(rf"(?:{_NUMBER})\s*個?(?:小時|鐘頭)")
_STATED_DURATION = re.compile(
    rf"延誤了?(?:只有|只|才|約|大約)*\s*({_NUMBER})\s*個?(?:小時|鐘頭)"
)


_CORRECTION_CUES = (
    "講錯",
    "說錯",
    "記錯",
    "打錯",
    "搞錯",
    "不對",
    "其實",
    "更正",
    "應該是",
    "只有",
)
_NOT_DURATION = re.compile(rf"不是\s*(?:{_NUMBER})\s*個?(?:小時|鐘頭)")
_ONLY_BEFORE_DELAY = re.compile(r"[只才]\s*延誤")  # 「只延誤 2 小時」
_QUESTION_MARKS = ("？", "?", "嗎", "多少", "幾")


def stated_delay_hours(text: str) -> int | None:
    """旅客更正延誤時數時直接說的時數（例：「我講錯了，其實只延誤 2 小時」）。

    只給更正用，所以要有更正的線索才算：句子裡有更正字眼（講錯、其實、不對…），或「只」「才」
    緊接在「延誤」前面。問句（？、嗎、多少、幾）一律不算：「延誤 4 小時可以賠多少？」是在問條款。
    允許數字前後的空白，仍然只接受剛好一個時數，遇到否定或多個時數回 None。
    `parse_delay_hours` 不動，它刻意對更正字眼回 None（#180）。
    """
    if any(mark in text for mark in _QUESTION_MARKS):
        return None
    if not (
        any(cue in text for cue in _CORRECTION_CUES) or _ONLY_BEFORE_DELAY.search(text)
    ):
        return None
    text = _NOT_DURATION.sub("", text)  # 「不是八個小時」的時數不計（#383）
    if len(_ANY_DURATION.findall(text)) != 1:
        return None  # 沒有時數，或說了多個時數：不猜
    m = _STATED_DURATION.search(text)
    if m is None or text[: m.start()].rstrip().endswith(_NEGATIONS):
        return None
    return _number(m.group(1))


_CLOCK = r"([0-9]{1,2})\s*[:：]\s*([0-9]{2})"
# 原定起飛時間的線索；「原本」只在後面接時間（中間可夾日期）時才算：「原本7/11 10:30」
_PLANNED_WORDS = r"(?:原定|原訂|預定|原本)"
_DATE_TEXT = r"(?:\d{4}\s*[/\-年]\s*)?\d{1,2}\s*[/\-月]\s*\d{1,2}\s*[日號]?"
_PLANNED_CLOCK = re.compile(
    rf"{_PLANNED_WORDS}[^0-9]{{0,6}}?(?:{_DATE_TEXT}\s*)?{_CLOCK}"
)
_PLANNED_DATED_CLOCK = re.compile(
    rf"{_PLANNED_WORDS}[^0-9]{{0,6}}?({_DATE_TEXT})\s*{_CLOCK}"
)
_ACTUAL_CLOCK = re.compile(rf"實際[^0-9]{{0,6}}?{_CLOCK}")
# 「班機被改為 22:00 起飛」「延到 22:00 才飛」：新的起飛時間，記成實際起飛時間。
# 「預計 22:00」不算（預計還沒發生，#394）
_CHANGED_CLOCK = re.compile(
    rf"(?:改為|改成|改到|延到|延至|延後到|延後至)[^0-9]{{0,6}}?{_CLOCK}"
)


def _clock(m: re.Match[str] | None) -> str | None:
    if m is None:
        return None
    hour, minute = int(m.group(1)), int(m.group(2))
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def stated_clock_times(text: str) -> dict[str, str]:
    """旅客直接說出的原定／實際起飛時間（例：「原訂14:00，實際16:00」），由程式取出，不經 LLM。

    只認 `HH:MM`，要有「原定／原訂／預定」或「實際」當線索，回傳 `dep_plan`、`dep_act`；
    「改為／改成／改到／延到／延至／延後到／延後至」後面接的時間也記成 `dep_act`（#433）；
    但和「原定」取到的是同一個時間（「原定改成 13:00」）時只記成原定。
    問句（？、嗎、多少、幾）一律不算：「原定 14:00 實際 16:00 可以賠嗎？」是在問條款（#382）。
    """
    if any(mark in text for mark in _QUESTION_MARKS):
        return {}
    planned = _PLANNED_CLOCK.search(text)
    # 「原定改成 13:00」是在更正原定時間：「改成」取到的若和原定是同一個位置的時間，不記成實際
    changed = next(
        (
            m
            for m in _CHANGED_CLOCK.finditer(text)
            if planned is None or m.start(1) != planned.start(1)
        ),
        None,
    )
    found = {
        "dep_plan": _clock(planned),
        "dep_act": _clock(_ACTUAL_CLOCK.search(text) or changed),
    }
    return {k: v for k, v in found.items() if v}


def stated_planned_date(
    text: str, year: int, start: date | None = None, end: date | None = None
) -> str | None:
    """旅客說原定起飛時間時一起說的日期（「原本7/11 10:30」）；事故日就是原定起飛日（#451）。

    要有「原定／原訂／預定／原本」，日期後面緊接起飛時間；問句不取（同 `stated_clock_times`）。
    沒寫年份時年份由保險期間決定（`explicit_date`）。"""
    if any(mark in text for mark in _QUESTION_MARKS):
        return None
    m = _PLANNED_DATED_CLOCK.search(text)
    return explicit_date(m.group(1), year, start, end) if m else None


@dataclass(frozen=True)
class DateBackfill:
    date: str
    inferred: bool  # True：沒有原定日期，是用實際起飛日期推定的


def backfilled_date(known: dict[str, str], planned: str | None) -> DateBackfill | None:
    """班機延誤的事故日期缺或不是明確的日期（記的是「不知道」）時，要回填的日期（#451）。

    事故日 ＝ 原定起飛日：旅客說原定起飛時間時給了日期就用它（`planned`）；沒有，就用實際起飛日期。
    已經有明確日期的不覆蓋；兩個都沒有回 None。"""
    if parse_date(known.get(PLANNED_DATE_KEY)) is not None:
        return None
    if parse_date(planned) is not None and planned:
        return DateBackfill(planned, inferred=False)
    actual = parse_date(known.get(ACTUAL_DATE_KEY))
    if actual is not None:
        return DateBackfill(f"{actual:%Y/%m/%d}", inferred=True)
    return None


def parse_date(value: str | None) -> date | None:
    m = _DATE.match(value or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


# 旅客說「隔天凌晨一點」：模型常用今天去算「隔天」，改由程式依事故日期（原定起飛日）換算
_RELATIVE_DAYS = {
    "隔天": 1,
    "隔日": 1,
    "第二天": 1,
    "次日": 1,
    "翌日": 1,
    "後天": 2,
    "當天": 0,
    "當日": 0,
    "同一天": 0,
}


_EXPLICIT_DATE = re.compile(
    r"(?:(\d{4})\s*[/\-年]\s*)?(\d{1,2})\s*[/\-月]\s*(\d{1,2})\s*[日號]?"
)


def pick_year(
    month: int, day: int, year: int, start: date | None, end: date | None
) -> int:
    """沒寫年份的月日用哪一年：配上某一年會落在保險期間內，就用那一年；都不在期間內（或沒有
    保險期間）才用 year（原本的規則）。年份由程式決定，不由 LLM（F3）。"""
    if start and end:
        for y in range(start.year, end.year + 1):
            try:
                if start <= date(y, month, day) <= end:
                    return y
            except ValueError:
                continue
    return year


def explicit_date(
    text: str,
    year: int,
    start: date | None = None,
    end: date | None = None,
) -> str | None:
    """旅客明講的日期（11/12、11月12日、2026/11/12）；剛好一個才算。

    沒寫年份時，年份由 `pick_year` 依保險期間（start、end）決定，都不在期間內才用 year。"""
    found = list(_EXPLICIT_DATE.finditer(text))
    if len(found) != 1:
        return None
    y, mo, d = found[0].groups()
    try:
        chosen = int(y) if y else pick_year(int(mo), int(d), year, start, end)
        return f"{date(chosen, int(mo), int(d)):%Y/%m/%d}"
    except ValueError:
        return None


def align_year(
    value: str, text: str, year: int, start: date | None, end: date | None
) -> str:
    """模型回的日期，如果旅客的話裡有沒寫年份、月日相同的日期，只是年份不同，以程式算的年份為準。"""
    given = parse_date(value)
    if given is None:
        return value
    for m in _EXPLICIT_DATE.finditer(text):
        y, mo, d = m.groups()
        if y is None and (int(mo), int(d)) == (given.month, given.day):
            chosen = pick_year(given.month, given.day, year, start, end)
            return f"{date(chosen, given.month, given.day):%Y/%m/%d}"
    return value


def relative_actual_date(text: str, planned: str | None) -> str | None:
    """「隔天」「當天」這類說法對應的實際起飛日期；沒有這類字眼或不知道原定日期回 None。"""
    base = parse_date(planned)
    offset = next((d for w, d in _RELATIVE_DAYS.items() if w in text), None)
    if base is None or offset is None:
        return None
    return f"{base + timedelta(days=offset):%Y/%m/%d}"


def timed_delay_minutes(known: dict[str, str]) -> int | None:
    """只用已記錄的原定與實際起飛日期、時間算出的延誤分鐘數；任何一項缺少回 None。"""
    return delay_minutes(
        parse_date(known.get(PLANNED_DATE_KEY)),
        known.get("dep_plan"),
        parse_date(known.get(ACTUAL_DATE_KEY)),
        known.get("dep_act"),
    )


def delay_minutes_of(known: dict[str, str]) -> int | None:
    """已記錄的事件狀況算出的延誤分鐘數：日期加時間相減；旅客直接說的時數當退路。算不出來回 None。

    判斷結論（`judge_flight_delay`）與問答帶入的「程式算出的結果」共用同一個算法。
    """
    delay = timed_delay_minutes(known)
    hours = known.get(DELAY_HOURS_KEY, "")
    if delay is None and hours.isdigit():
        delay = int(hours) * 60
    return delay


def _outside_period(
    known: dict[str, str], start: date | None, end: date | None
) -> Judgement | None:
    """事故日期不在保險期間內（或不知道日期）時的結論；在期間內回 None。各事故類型共用。"""
    happened = parse_date(known.get("date"))
    if start and end:
        if happened is None:
            return Judgement("insufficient", "需確認事故發生日期是否在保險期間內")
        if not start <= happened <= end:
            return Judgement("excluded", "事故不在保險期間內，依條款不在保障範圍")
    return None


_UNDER_THRESHOLD = "延誤未滿四小時，依條款不在保障範圍；如有疑問請向保險公司確認"


def _missing_for_delay(known: dict[str, str]) -> list[str]:
    """算不出延誤時，實際還缺的項目（記錄是「不知道」或格式不對也算缺）；理由只列這些（#431）。"""
    missing = []
    if _minutes(known.get("dep_plan")) is None:
        missing.append("原定起飛時間")
    if _minutes(known.get("dep_act")) is None:
        missing.append("實際起飛時間")
    if parse_date(known.get(ACTUAL_DATE_KEY)) is None:
        missing.append("實際起飛日期")
    if parse_date(known.get(PLANNED_DATE_KEY)) is None:
        missing.append("事故發生日期")  # 當作原定起飛日期
    return missing


def judge_flight_delay(
    known: dict[str, str], start: date | None = None, end: date | None = None
) -> Judgement:
    result = _judge_flight_delay(known, start, end)
    # 事故日期是推定的：在理由裡看得出來（推定的日期被旅客改掉就不再註記）
    inferred = known.get(DATE_INFERRED_KEY)
    if inferred and inferred == known.get(PLANNED_DATE_KEY):
        return Judgement(result.conclusion, f"{result.reason}；{INFERRED_NOTE}")
    return result


def _judge_flight_delay(
    known: dict[str, str], start: date | None = None, end: date | None = None
) -> Judgement:
    if outside := _outside_period(known, start, end):
        return outside
    # 判斷延誤多久的順序：起飛的日期與時間算出的分鐘數 → 旅客說的時數 → 只回答有沒有滿門檻
    delay = delay_minutes_of(known)
    if delay is None:
        band = known.get(DELAY_BAND_KEY)
        if band == DELAY_BAND_UNDER:
            return Judgement("excluded", _UNDER_THRESHOLD)
        if band == DELAY_BAND_OVER:
            delay = THRESHOLD_HOURS * 60  # 已達門檻，繼續往下判斷是否搭第一班替代班機
    if delay is None:
        return Judgement(
            "insufficient",
            f"還需要{'、'.join(_missing_for_delay(known))}，才能算出延誤多久",
        )
    if delay < 0:
        return Judgement(
            "insufficient", "實際起飛時間早於原定起飛時間，需確認起飛的時間和日期"
        )
    if delay < THRESHOLD_HOURS * 60:
        return Judgement("excluded", _UNDER_THRESHOLD)
    if known.get(FIRST_ALT_KEY) == "否":
        # 特別不保事項第五款：未搭第一班替代交通工具不賠，但因不可抗力無法搭乘者不在此限，程式判斷不了
        return Judgement(
            "insufficient",
            "未搭乘第一班替代班機依條款不賠，除非是因不可抗力無法搭乘，需向保險公司確認",
        )
    return Judgement("applicable", "延誤四小時以上可申請班機延誤保險金")


# 行李損失保險（#231）：富邦 FUBON03、富邦享樂 TA49、安達 CHUBB05、臺產 TFMI01 第 30–35 條，
# 華南 HUANAN01 第 36–41 條，文字相同（11/08 對照）。
CAUSE_KEY = "cause"
THEFT = "被偷、被搶或被扒"
CHECKED_LOST = "託運行李被航空公司弄丟"
CHECKED_DAMAGED = "託運行李被弄壞"
SELF_LOST = "自己弄丟或不清楚怎麼不見"
POLICE_KEY = "police"
REPORTED_KEY = "reported"
COMPENSATED_KEY = "compensated"
COMPENSATED_YES = "有"
# 特別不保事項（物品）：物品種類不由程式判斷，只提醒
_EXCLUDED_ITEMS = "手機、珠寶、飾品、現金、票券、證件、易碎品、行李箱本身等依條款不賠"


def judge_baggage_loss(
    known: dict[str, str], start: date | None = None, end: date | None = None
) -> Judgement:
    """承保範圍只保竊盜、強盜、搶奪造成的毀損滅失，以及託運行李被業者弄丟。"""
    if outside := _outside_period(known, start, end):
        return outside
    cause = known.get(CAUSE_KEY)
    if cause == SELF_LOST:
        # 特別不保事項（事故）第八款
        return Judgement(
            "excluded", "非因竊盜、強盜、搶奪的不明原因遺失，依條款不在保障範圍"
        )
    if cause == CHECKED_DAMAGED:
        return Judgement("excluded", "條款只保託運行李遺失，託運時被弄壞不在保障範圍")
    if cause not in (THEFT, CHECKED_LOST):
        return Judgement(
            "insufficient", "需確認行李是被偷、被搶，還是託運時被航空公司弄丟"
        )
    if cause == THEFT and known.get(POLICE_KEY) != "是":
        return Judgement(
            "insufficient",
            "被偷或被搶要在 24 小時內向當地警察報案並取得證明，請盡快報案並向保險公司確認",
        )
    if cause == CHECKED_LOST and known.get(REPORTED_KEY) != "是":
        return Judgement(
            "insufficient",
            "託運行李遺失要儘速通知航空公司並索取書面事故與損失證明，請向保險公司確認",
        )
    if known.get(COMPENSATED_KEY) == COMPENSATED_YES:
        # 特別不保事項（事故）第四款：整件不賠或只扣業者賠的部分，條文沒寫清楚（#231 待確認）
        return Judgement(
            "insufficient", "可由業者補償的損失依條款不賠，請向保險公司確認差額能否申請"
        )
    return Judgement("applicable", f"可申請行李損失保險金；{_EXCLUDED_ITEMS}")


# 就醫（安達海外突發疾病健康保險（乙型）CHUBB04）：第四條保險範圍、第五條住院、第六條門診、
# 第八條除外責任。只核對了這一個險種；傷害醫療、食物中毒慰問與其他公司的條文還沒核對，
# 對到就醫的其他險種一律資訊不足（待確認事項）。
KIND_KEY = "kind"
HOSP_KEY = "hosp"
ILLNESS = "生病"
INJURY = "受傷"
# 「九十」是條款定義的日數，不是程式算出來的數字
_MEDICAL_REMINDER = "出發前九十日內曾就同一疾病就診、或以出國就醫為目的者，依條款不賠"


def judge_medical(
    known: dict[str, str], start: date | None = None, end: date | None = None
) -> Judgement:
    """海外突發疾病：生病才適用；受傷屬傷害醫療，那部分的條款還沒核對，資訊不足。
    理由不寫金額，額度由平台從保單帶。"""
    if outside := _outside_period(known, start, end):
        return outside
    kind = known.get(KIND_KEY)
    if kind == INJURY:
        return Judgement(
            "insufficient", "受傷屬於傷害醫療，這部分的條款尚未核對，請向保險公司確認"
        )
    if kind != ILLNESS:
        return Judgement("insufficient", "需確認是生病還是受傷")
    where = {"否": "門診", "是": "住院"}.get(known.get(HOSP_KEY, ""), "就醫")
    return Judgement(
        "applicable",
        f"海外突發疾病{where}可申請醫療保險金，依收據在保單額度內理賠；{_MEDICAL_REMINDER}",
    )
