import { expect, test } from "@playwright/test";
import {
  DEMO_ITINERARIES,
  demoActivities,
  demoTrip,
} from "../src/features/itinerary/demoItineraries";
import { addDays } from "../src/features/itinerary/tripRules";

// 純邏輯，不需要瀏覽器與後端：出發日規則與範例資料本身的一致性
const trip = (id: string, now: string) => {
  const demo = DEMO_ITINERARIES.find((d) => d.id === id);
  if (!demo) throw new Error(`沒有範例 ${id}`);
  return demoTrip(demo, now);
};

test.describe("範例日程的出發日規則", () => {
  test("2-2 用下一個 9/15", () => {
    expect(trip("2-2", "2026-11-16").depart_date).toBe("2027-09-15");
    expect(trip("2-2", "2026-11-16").depart_date).toBe("2027-09-15");
    expect(trip("2-2", "2027-09-15").depart_date).toBe("2027-09-15");
    expect(trip("2-2", "2027-03-01").depart_date).toBe("2027-09-15");
    expect(trip("2-2", "2027-09-16").depart_date).toBe("2028-09-15");
  });

  test("其他日程用今天 +30 天", () => {
    for (const demo of DEMO_ITINERARIES.filter((d) => d.id !== "2-2")) {
      expect(demoTrip(demo, "2026-11-16").depart_date).toBe("2026-12-16");
    }
  });
});

test.describe("範例資料", () => {
  test("有主 demo 與 2-1～2-4，主 demo 18 筆活動", () => {
    expect(DEMO_ITINERARIES.map((d) => d.id)).toEqual(["main", "2-1", "2-2", "2-3", "2-4"]);
    expect(DEMO_ITINERARIES[0]?.activities).toHaveLength(18);
  });

  for (const demo of DEMO_ITINERARIES) {
    test(`${demo.id}：活動日期在旅程內、同一天同一個整點只有一筆、都有座標`, () => {
      const { depart_date, return_date } = demoTrip(demo, "2026-11-16");
      expect(return_date).toBe(addDays(depart_date, demo.days - 1));
      const activities = demoActivities(demo, depart_date);
      const slots = new Set<string>();
      for (const a of activities) {
        expect(a.date >= depart_date && a.date <= return_date).toBe(true);
        expect(a.hour).toBeGreaterThanOrEqual(7);
        expect(a.hour).toBeLessThanOrEqual(22);
        // 後端限制時長 0.5–8 小時，超過會 422、整份範例載不進去
        expect(a.duration).toBeGreaterThanOrEqual(0.5);
        expect(a.duration).toBeLessThanOrEqual(8);
        expect(a.location).toBeTruthy();
        expect(a.latitude).not.toBeNull();
        expect(a.longitude).not.toBeNull();
        slots.add(`${a.date} ${a.hour}`);
      }
      expect(slots.size).toBe(activities.length);
    });
  }
});
