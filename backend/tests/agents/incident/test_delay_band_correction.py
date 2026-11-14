"""記錄裡只有 `delay_band`（點了「延誤 4 小時以上／不到」）時，旅客更正時數（#C 與 #E 合併之後）。

E 的「記錄裡算不出起飛時間就直接改 `delay_hours`」不看 `delay_band`；judge 的順序是起飛時間、
`delay_hours`、`delay_band`，所以 `delay_hours` 先於 `delay_band`，不用刪 `delay_band`。
只透過 start／reply 測試。
"""

from tests.agents.incident.test_agent_v1 import (
    SERVICES,
    agent,
    conclusion_of,
    extractor,
    fact,
    make_ctx,
    revising,
)

# 只有「延誤時間」，沒有起飛時間、沒有 delay_hours
BASE = {
    "event_type": "flight_delay",
    "date": "2026/11/03",
    "place": "成田機場",
    "reason": "天候",
}


async def test_點延誤_4_小時以上得到可申請_再更正成只延誤_2_小時_結論變不在保障範圍() -> (
    None
):
    before = {**BASE, "delay_band": "4 小時以上"}
    # 問完是否搭第一班替代班機 → 可能適用
    first = await agent().reply(make_ctx(facts=before), "是", "quick_reply", SERVICES)
    assert conclusion_of(first) == "applicable"

    concluded = make_ctx(facts={**before, "first_alt": "是"}, type_code="flight_delay")
    reply = await agent(revise=revising()).reply(
        concluded, "我講錯了，其實只延誤 2 小時", "text", SERVICES
    )

    assert fact(reply, "delay_hours") == "2"
    assert conclusion_of(reply) == "excluded"


async def test_點延誤不到_4_小時得到不在保障範圍_再更正成延誤_6_小時_結論變可申請() -> (
    None
):
    concluded = make_ctx(
        facts={**BASE, "delay_band": "未滿 4 小時", "first_alt": "是"},
        type_code="flight_delay",
    )

    reply = await agent(revise=revising()).reply(
        concluded, "我講錯了，其實延誤了 6 小時", "text", SERVICES
    )

    assert fact(reply, "delay_hours") == "6"  # delay_hours 先於 delay_band
    assert conclusion_of(reply) == "applicable"


async def test_追問進行中點了延誤_4_小時以上_下一題前更正時數_記下新時數_接著問下一題() -> (
    None
):
    asking = make_ctx(
        facts={
            "event_type": "flight_delay",
            "date": "2026/11/03",
            "place": "成田機場",
            "delay_band": "4 小時以上",
        }
    )

    reply = await agent(extract=extractor(), revise=revising()).reply(
        asking, "我講錯了，其實只延誤 2 小時", "text", SERVICES
    )

    assert fact(reply, "delay_hours") == "2"
    assert reply.message.startswith("好的，已更正。")
    assert reply.finished is False
