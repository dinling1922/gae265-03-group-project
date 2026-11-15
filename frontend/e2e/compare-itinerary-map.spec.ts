import { expect, type Page, test } from "@playwright/test";
import { login } from "./helpers";

function day(offset: number): string {
  const d = new Date();
  d.setDate(d.getDate() + offset);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

test.beforeEach(async ({ request }) => {
  const config = await (await request.get("/api/v1/auth/config")).json();
  test.skip(!config.dev_login, "後端沒有設 DEV_LOGIN_ENABLED=true");
});

/** 建一趟 5 天的日本旅行：第 1 天 3 個有座標＋1 個沒有座標，第 2 天 1 個，第 3 天 1 個。回傳比較紀錄 id。 */
async function setup(page: Page, email: string): Promise<string> {
  // 圖磚來自外部網站：不讓測試等它（pin 與清單不靠圖磚）
  await page.route("**/tile.openstreetmap.org/**", (route) => route.abort());
  await login(page, email);
  const created = await page.request.post("/api/v1/comparisons", {
    data: { destination: "日本", city: "京都", depart_date: day(7), return_date: day(11) },
  });
  const { id } = await created.json();
  const added = await page.request.post(`/api/v1/comparisons/${id}/activities`, {
    data: {
      activities: [
        {
          date: day(7),
          hour: 8,
          title: "手沖咖啡",
          category: "coffee",
          latitude: 35.0055,
          longitude: 135.7656,
        },
        {
          date: day(7),
          hour: 10,
          title: "錦市場",
          category: "eat",
          latitude: 35.0,
          longitude: 135.75,
        },
        { date: day(7), hour: 13, title: "鴨川散步", category: "explore" }, // 沒有座標
        {
          date: day(7),
          hour: 15,
          title: "祇園小巷",
          category: "explore",
          latitude: 35.0037,
          longitude: 135.7753,
        },
        {
          date: day(8),
          hour: 8,
          title: "伏見稻荷",
          category: "culture",
          latitude: 34.9671,
          longitude: 135.7727,
        },
        {
          date: day(9),
          hour: 9,
          title: "嵐山小徑",
          category: "explore",
          latitude: 35.0094,
          longitude: 135.6668,
        },
      ],
    },
  });
  expect(added.ok()).toBeTruthy();
  await page.goto(`/compare/${id}/itinerary`);
  await expect(page.getByTestId("map-pin")).toHaveCount(3);
  return id;
}

const card = (page: Page, title: string) =>
  page.getByTestId("activity-card").filter({ hasText: title });
const pin = (page: Page, n: string) => page.locator(`[data-testid="map-pin"][data-n="${n}"]`);
const stop = (page: Page, n: number) => page.getByTestId("stop-item").nth(n - 1);

// 三處（時間軸、地圖、清單）共用選取：點任一處，三處一起標示。
test("點清單，時間軸卡片加框、地圖 pin 變成選取狀態；點時間軸卡片或 pin，其他兩處同步", async ({
  page,
}) => {
  await setup(page, "e2e-map-select@example.com");
  // 單日：只有當天有座標的 3 站，依時間編號
  await expect(page.getByTestId("map-stats")).toHaveText("3 個地點");
  await expect(page.getByTestId("stop-item")).toHaveCount(3);
  await expect(page.getByTestId("map-pin")).toHaveText(["1", "2", "3"]);

  await stop(page, 2).click();
  await expect(stop(page, 2)).toHaveAttribute("data-selected", "true");
  await expect(card(page, "錦市場")).toHaveAttribute("data-selected", "true");
  await expect(pin(page, "2")).toHaveAttribute("data-selected", "true");
  await expect(pin(page, "1")).toHaveAttribute("data-selected", "false");
  await expect(page.locator(".leaflet-popup").filter({ hasText: "錦市場" })).toBeVisible();

  // 點時間軸卡片：清單與 pin 跟著換
  await card(page, "祇園小巷").getByRole("button", { name: /^選取/ }).click();
  await expect(card(page, "祇園小巷")).toHaveAttribute("data-selected", "true");
  await expect(card(page, "錦市場")).toHaveAttribute("data-selected", "false");
  await expect(stop(page, 3)).toHaveAttribute("data-selected", "true");
  await expect(pin(page, "3")).toHaveAttribute("data-selected", "true");

  // 點地圖 pin：時間軸與清單跟著換（資訊卡打開時地圖會平移，等平移結束再點）
  await expect(page.locator(".leaflet-popup").filter({ hasText: "祇園小巷" })).toBeVisible();
  await expect(page.locator(".leaflet-map-pane.leaflet-pan-anim")).toHaveCount(0);
  await pin(page, "1").click();
  await expect(card(page, "手沖咖啡")).toHaveAttribute("data-selected", "true");
  await expect(stop(page, 1)).toHaveAttribute("data-selected", "true");
  await expect(pin(page, "1")).toHaveAttribute("data-selected", "true");
});

// 全部天數顯示所有天的 pin；點別天的項目，日期分頁跟著切換。
test("切到「全部天數」顯示所有天的 pin，點別天的項目日期分頁跟著切換", async ({ page }) => {
  await setup(page, "e2e-map-trip@example.com");
  await page.getByRole("button", { name: "全部天數" }).click();
  await expect(page.getByTestId("map-pin")).toHaveText(["1-1", "1-2", "1-3", "2-1", "3-1"]);
  await expect(page.getByTestId("map-stats")).toHaveText("5 個地點");
  await expect(page.getByRole("tab", { name: /第 1 天/ })).toHaveAttribute("aria-selected", "true");

  await page.getByTestId("stop-item").filter({ hasText: "嵐山小徑" }).click();
  await expect(page.getByRole("tab", { name: /第 3 天/ })).toHaveAttribute("aria-selected", "true");
  await expect(card(page, "嵐山小徑")).toHaveAttribute("data-selected", "true");
  await expect(pin(page, "3-1")).toHaveAttribute("data-selected", "true");
  // 看整趟時仍然是整趟
  await expect(page.getByRole("button", { name: "全部天數" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );

  // 點日期分頁回到單日
  await page.getByRole("tab", { name: /第 2 天/ }).click();
  await expect(page.getByTestId("map-pin")).toHaveText(["1"]);
  await expect(page.getByRole("button", { name: "第 2 天" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
});

// 沒有座標的活動不上地圖也不在清單，但會顯示數量與時間軸上的「沒有座標」。
test("沒有座標的活動不出現在地圖與清單，並顯示數量", async ({ page }) => {
  await setup(page, "e2e-map-nocoords@example.com");
  await expect(page.getByTestId("stop-list")).not.toContainText("鴨川散步");
  await expect(page.getByTestId("map-unmapped")).toHaveText("另有 1 個活動沒有座標，不在地圖上");
  await expect(card(page, "鴨川散步")).toContainText("沒有座標");

  // 第 2 天全都有座標：不顯示數量
  await page.getByRole("tab", { name: /第 2 天/ }).click();
  await expect(page.getByTestId("map-unmapped")).toHaveCount(0);
});

// 地圖容器大小改變後要重新計算，不能留下灰色區塊：寬度變了，圖磚層與容器一樣大。
test("視窗大小改變後地圖容器重新計算尺寸", async ({ page }) => {
  await setup(page, "e2e-map-resize@example.com");
  const sizeOf = () =>
    page.evaluate(() => {
      const container = document.querySelector(".leaflet-container") as HTMLElement;
      const pane = document.querySelector(".leaflet-map-pane") as HTMLElement;
      return { container: container.clientWidth, pane: pane.getBoundingClientRect().width };
    });
  await page.setViewportSize({ width: 1500, height: 900 });
  await page.setViewportSize({ width: 800, height: 900 });
  await expect(page.locator(".leaflet-container")).toBeVisible();
  const after = await sizeOf();
  expect(after.container).toBeGreaterThan(300);
  expect(after.container).toBeLessThanOrEqual(800);
});

// 版面：寬螢幕三欄（表單｜日程｜地圖）、768–1090px 兩欄（地圖在日程下方）、手機單欄。
test("日程頁的欄數隨寬度變化：三欄、兩欄、單欄", async ({ page }) => {
  await setup(page, "e2e-map-layout@example.com");
  const boxes = async () => {
    const form = await page.getByLabel("目的地").boundingBox();
    const banner = await page.getByTestId("trip-banner").boundingBox();
    const map = await page.getByTestId("map-panel").boundingBox();
    if (!form || !banner || !map) throw new Error("找不到版面元素");
    return { form, banner, map };
  };

  await page.setViewportSize({ width: 1400, height: 900 });
  let b = await boxes();
  expect(b.banner.x).toBeGreaterThan(b.form.x + b.form.width - 1);
  expect(b.map.x).toBeGreaterThan(b.banner.x + b.banner.width - 1);

  await page.setViewportSize({ width: 900, height: 900 });
  b = await boxes();
  expect(b.banner.x).toBeGreaterThan(b.form.x + b.form.width - 1);
  expect(b.map.y).toBeGreaterThan(b.banner.y); // 地圖在日程下方
  expect(b.map.x).toBeGreaterThan(b.form.x + b.form.width - 1);

  await page.setViewportSize({ width: 390, height: 900 });
  b = await boxes();
  expect(Math.abs(b.banner.x - b.form.x)).toBeLessThan(40); // 單欄：同一個左緣
  expect(b.banner.y).toBeGreaterThan(b.form.y);
  expect(b.map.y).toBeGreaterThan(b.banner.y);
});

// 捲動：中間欄的橫幅與日期分頁捲到頂就固定、只有時間軸捲動；右欄的地圖固定、只有「順序」清單捲動。
test("寬螢幕下，橫幅與日期分頁、地圖固定，時間軸與順序清單才捲動", async ({ page }) => {
  await setup(page, "e2e-map-sticky@example.com");
  await page.setViewportSize({ width: 1400, height: 800 });
  const top = async (testId: string) => (await page.getByTestId(testId).boundingBox())?.y ?? NaN;
  const tabsTop = async () => (await page.getByRole("tab").first().boundingBox())?.y ?? NaN;

  const mapBefore = await top("map-panel");
  await page.mouse.move(700, 500);
  await page.mouse.wheel(0, 1200);
  await page.waitForTimeout(300);
  // 橫幅與分頁固定在畫面頂端；時間軸已經捲走
  expect(await top("trip-banner")).toBeLessThan(80);
  expect(await tabsTop()).toBeLessThan(400);
  expect((await page.getByTestId("slot-7").boundingBox())?.y ?? 0).toBeLessThan(0);
  // 地圖面板跟著固定在畫面上（不會跟著頁面捲走）
  expect(await top("map-panel")).toBeLessThanOrEqual(mapBefore);
  expect(await top("map-panel")).toBeGreaterThanOrEqual(0);
  await expect(page.getByTestId("map-pin").first()).toBeInViewport();
});
