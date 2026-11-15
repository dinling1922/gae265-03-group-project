import { expect, type Page, test } from "@playwright/test";
import { login } from "./helpers";

/** 今天往後 offset 天的 YYYY-MM-DD（瀏覽器的本地日期）。 */
function day(offset: number): string {
  const d = new Date();
  d.setDate(d.getDate() + offset);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** 在 `/compare/new` 填目的地與日期；填好 500ms 後自動建立比較紀錄，網址換成日程頁。回傳比較紀錄 id。 */
async function fillTrip(page: Page, destination: string, depart: number, ret: number) {
  await page.getByLabel("目的地").selectOption(destination);
  await page.getByLabel("出發日").fill(day(depart));
  await page.getByLabel("回程日").fill(day(ret));
  await expect(page).toHaveURL(/\/compare\/[0-9a-f-]{36}\/itinerary$/);
  return page.url().split("/").at(-2) as string;
}

test.beforeEach(async ({ request }) => {
  const config = await (await request.get("/api/v1/auth/config")).json();
  test.skip(!config.dev_login, "後端沒有設 DEV_LOGIN_ENABLED=true");
});

// 首頁 → 日程頁填寫 → 自動建立比較紀錄；重新整理資料還在；改了欄位即時存檔。
test("在日程頁填目的地與日期就建立比較紀錄，之後的修改即時存檔", async ({ page }) => {
  await login(page, "e2e-itinerary-create@example.com");

  await page.getByRole("link", { name: /出發前：幫我選方案/ }).click();
  await expect(page).toHaveURL(/\/compare\/new$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("規劃旅遊日程");
  await expect(page.getByText("選好兩個日期後顯示天數")).toBeVisible();
  await expect(page.getByTestId("itinerary-empty")).toBeVisible();

  await page.getByLabel("城市").fill("京都");
  const id = await fillTrip(page, "日本", 7, 11);
  await expect(page.getByLabel("目的地")).toHaveValue("日本");
  await expect(page.getByLabel("城市")).toHaveValue("京都");
  await expect(page.getByText("共 5 天")).toBeVisible();
  await expect(page.getByTestId("trip-banner")).toContainText("京都，日本");
  await expect(page.getByRole("tab")).toHaveCount(5);

  // 即時存檔：改目的地與城市，不按任何按鈕，重新整理後還在
  await page.getByLabel("目的地").selectOption("韓國");
  await page.getByLabel("城市").fill("首爾");
  await expect(page.getByTestId("trip-banner")).toContainText("首爾，韓國");
  await page.reload();
  await expect(page).toHaveURL(new RegExp(`/compare/${id}/itinerary$`));
  await expect(page.getByLabel("目的地")).toHaveValue("韓國");
  await expect(page.getByLabel("城市")).toHaveValue("首爾");
  await expect(page.getByLabel("出發日")).toHaveValue(day(7));
  await expect(page.getByLabel("回程日")).toHaveValue(day(11));
});

// 不合規則時顯示原因，不會存檔。
test("日程頁的日期與天數不合規則時顯示原因，也不建立比較紀錄", async ({ page }) => {
  await login(page, "e2e-itinerary-invalid@example.com");
  await page.goto("/compare/new");

  const next = page.getByRole("button", { name: "下一步" });
  await page.getByLabel("目的地").selectOption("美國");
  await expect(next).toBeDisabled();

  const cases: [string, string, string][] = [
    [day(-1), day(4), "出發日要從今天起。"],
    [day(7), day(6), "回程日不能早於出發日。"],
    [day(7), day(9), "旅遊天數最短 4 天。"],
    [day(7), day(17), "旅遊天數最長 10 天。"],
  ];
  for (const [depart, ret, message] of cases) {
    await page.getByLabel("出發日").fill(depart);
    await page.getByLabel("回程日").fill(ret);
    await expect(page.getByText(message)).toBeVisible();
    await expect(next).toBeDisabled();
  }
  await page.waitForTimeout(800); // 比自動存檔的等待久：還是沒建立
  await expect(page).toHaveURL(/\/compare\/new$/);

  // 日期選擇器只能選有效範圍：出發日從今天起，回程日是 4～10 天
  await page.getByLabel("出發日").fill(day(7));
  await expect(page.getByLabel("出發日")).toHaveAttribute("min", day(0));
  await expect(page.getByLabel("回程日")).toHaveAttribute("min", day(10));
  await expect(page.getByLabel("回程日")).toHaveAttribute("max", day(16));
});

const slot = (page: Page, hour: number) => page.getByTestId(`slot-${hour}`);

// 點空時段新增、編輯、移除活動；重新整理後結果還在。
test("新增、編輯、移除日程活動，重新整理後還在", async ({ page }) => {
  await login(page, "e2e-itinerary-activity@example.com");
  await page.goto("/compare/new");
  await fillTrip(page, "日本", 7, 11);

  await slot(page, 9).getByRole("button", { name: /空檔/ }).click();
  const dialog = page.getByTestId("activity-dialog");
  await expect(dialog.getByRole("heading", { name: "新增活動" })).toBeVisible();
  await dialog.getByLabel("要做什麼").fill("進進堂早餐");
  await dialog.getByLabel("地點").fill("京都市中京區");
  await dialog.getByLabel("分類").selectOption("coffee");
  await dialog.getByLabel("時長").selectOption("1.5");
  await dialog.getByRole("button", { name: "儲存" }).click();
  await expect(dialog).toBeHidden();

  const card = slot(page, 9).getByTestId("activity-card");
  await expect(card).toContainText("進進堂早餐");
  await expect(card).toContainText("咖啡");
  await expect(card).toContainText("沒有座標");
  await expect(card).toContainText("1.5 小時");
  await expect(page.getByRole("tab", { name: /第 1 天/ })).toContainText("1 個活動");

  await page.reload();
  await expect(slot(page, 9).getByTestId("activity-card")).toContainText("進進堂早餐");

  await slot(page, 9).getByRole("button", { name: "編輯 進進堂早餐" }).click();
  await expect(dialog.getByRole("heading", { name: "編輯活動" })).toBeVisible();
  await dialog.getByLabel("要做什麼").fill("錦市場早餐");
  await dialog.getByRole("button", { name: "儲存" }).click();
  await expect(slot(page, 9).getByTestId("activity-card")).toContainText("錦市場早餐");

  // 另一天互不影響
  await page.getByRole("tab", { name: /第 2 天/ }).click();
  await expect(page.getByTestId("activity-card")).toHaveCount(0);
  await page.getByRole("tab", { name: /第 1 天/ }).click();

  await slot(page, 9).getByRole("button", { name: "移除 錦市場早餐" }).click();
  await expect(page.getByTestId("activity-card")).toHaveCount(0);
  await expect(slot(page, 9).getByRole("button", { name: /空檔/ })).toBeVisible();
  await page.reload();
  await expect(page.getByTestId("activity-card")).toHaveCount(0);
});

// 縮短日期會刪活動：先確認；取消則不變，確認後範圍外的活動消失。
test("縮短日期會移除活動時先確認，取消不變、確認後移除", async ({ page }) => {
  await login(page, "e2e-itinerary-shorten@example.com");
  await page.goto("/compare/new");
  const id = await fillTrip(page, "日本", 7, 12); // 6 天
  const add = await page.request.post(`/api/v1/comparisons/${id}/activities`, {
    data: {
      activities: [
        { date: day(7), hour: 9, title: "第一天早餐" },
        { date: day(12), hour: 10, title: "最後一天咖啡" },
      ],
    },
  });
  expect(add.ok()).toBeTruthy();
  await page.reload();
  await expect(page.getByRole("tab")).toHaveCount(6);

  await page.getByLabel("回程日").fill(day(10));
  const dialog = page.getByTestId("shorten-dialog");
  await expect(dialog.getByRole("heading", { name: "將移除 1 個活動" })).toBeVisible();
  await expect(dialog).toContainText("最後一天咖啡");
  await dialog.getByRole("button", { name: "取消" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByLabel("回程日")).toHaveValue(day(12));
  await expect(page.getByRole("tab")).toHaveCount(6);

  await page.getByLabel("回程日").fill(day(10));
  await dialog.getByRole("button", { name: "確認移除" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByRole("tab")).toHaveCount(4);
  await page.reload();
  await expect(page.getByLabel("回程日")).toHaveValue(day(10));
  await expect(page.getByRole("tab")).toHaveCount(4);
  await expect(page.getByTestId("activity-card")).toHaveCount(1); // 第一天早餐還在
});

// 不排活動也能下一步；1-2 按上一步回來，活動還在。
test("不排活動可以直接下一步；1-2 上一步回到日程頁，活動還在", async ({ page }) => {
  await login(page, "e2e-itinerary-back@example.com");
  await page.goto("/compare/new");
  const id = await fillTrip(page, "日本", 7, 11);

  await page.getByRole("button", { name: "下一步" }).click();
  await expect(page).toHaveURL(new RegExp(`/compare/${id}/concerns$`));
  await page.getByRole("button", { name: "上一步" }).click();
  await expect(page).toHaveURL(new RegExp(`/compare/${id}/itinerary$`));

  await slot(page, 14).getByRole("button", { name: /空檔/ }).click();
  await page.getByTestId("activity-dialog").getByLabel("要做什麼").fill("祇園散步");
  await page.getByTestId("activity-dialog").getByRole("button", { name: "儲存" }).click();
  await expect(slot(page, 14).getByTestId("activity-card")).toContainText("祇園散步");

  await page.getByRole("button", { name: "下一步" }).click();
  await expect(page).toHaveURL(/\/concerns$/);
  await page.getByRole("button", { name: "上一步" }).click();
  await expect(slot(page, 14).getByTestId("activity-card")).toContainText("祇園散步");
});

// 舊的 1-1 網址導到日程頁。
test("舊網址 /compare/$id/background 導向日程頁", async ({ page }) => {
  await login(page, "e2e-itinerary-redirect@example.com");
  await page.goto("/compare/new");
  const id = await fillTrip(page, "泰國", 7, 11);
  await page.goto(`/compare/${id}/background`);
  await expect(page).toHaveURL(new RegExp(`/compare/${id}/itinerary$`));
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("規劃旅遊日程");
});

// 整條 wizard：1-2 選的在意事項與補充說明在上一步、下一步、重新整理之間都留著，最後到 1-3。
test("1-1 → 1-2 → 上一步 → 下一步 → 重新整理，資料都在，最後到推薦方案", async ({ page }) => {
  await login(page, "e2e-compare-concerns@example.com");

  await page.getByRole("link", { name: /出發前：幫我選方案/ }).click();
  await fillTrip(page, "日本", 7, 11);
  await page.getByRole("button", { name: "下一步" }).click();
  await expect(page).toHaveURL(/\/compare\/[0-9a-f-]{36}\/concerns$/);

  await expect(page.getByText("這趟旅行你最擔心什麼？依重要程度點選，最多 3 項。")).toBeVisible();
  await page.getByRole("button", { name: /^突然生病就醫/ }).click();
  await page.getByRole("button", { name: /^班機延誤/ }).click();
  await page.getByLabel("其他在意的事（選填）").fill("會去滑雪");

  await page.getByRole("button", { name: "上一步" }).click();
  await expect(page).toHaveURL(/\/itinerary$/);
  await expect(page.getByLabel("目的地")).toHaveValue("日本");
  await expect(page.getByLabel("出發日")).toHaveValue(day(7));
  await expect(page.getByLabel("回程日")).toHaveValue(day(11));

  await page.getByRole("button", { name: "下一步" }).click();
  await expect(page).toHaveURL(/\/concerns$/);
  for (const reload of [false, true]) {
    if (reload) await page.reload();
    await expect(page.getByRole("button", { name: /^突然生病就醫/ })).toHaveAccessibleName(
      "突然生病就醫 第 1 順位",
    );
    await expect(page.getByRole("button", { name: /^班機延誤/ })).toHaveAccessibleName(
      "班機延誤 第 2 順位",
    );
    await expect(page.getByLabel("其他在意的事（選填）")).toHaveValue("會去滑雪");
  }

  await page.getByRole("button", { name: "下一步" }).click();
  await expect(page).toHaveURL(/\/result$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("推薦方案與比較表");
});

// 依點選順序編號；取消後後面往前補；選滿 3 項其他不能點；沒選不能下一步；補充說明最多 200 字。
test("1-2 的點選順序、取消補位、最多 3 項與補充說明字數", async ({ page }) => {
  await login(page, "e2e-compare-concerns-rules@example.com");
  await page.goto("/compare/new");
  await fillTrip(page, "美國", 7, 10);
  await page.getByRole("button", { name: "下一步" }).click();
  await expect(page).toHaveURL(/\/concerns$/);

  const card = (name: string) => page.getByRole("button", { name: new RegExp(`^${name}`) });
  const next = page.getByRole("button", { name: "下一步" });
  await expect(next).toBeDisabled();

  // 9 個在意事項，依序排列
  await expect(page.locator("button[aria-pressed]")).toHaveText([
    "突然生病就醫",
    "需要緊急送醫或家屬趕來",
    "行程取消或更改",
    "班機延誤",
    "行李延誤",
    "行李遺失或損壞",
    "證件遺失",
    "信用卡盜刷",
    "不小心弄傷別人或弄壞東西",
  ]);

  for (const name of ["行李延誤", "證件遺失", "信用卡盜刷"]) await card(name).click();
  await expect(next).toBeEnabled();
  await expect(card("班機延誤")).toBeDisabled();
  await expect(card("信用卡盜刷")).toHaveAccessibleName("信用卡盜刷 第 3 順位");

  await card("行李延誤").click();
  await expect(card("行李延誤")).toHaveAccessibleName("行李延誤");
  await expect(card("行李延誤")).toHaveAttribute("aria-pressed", "false");
  await expect(card("證件遺失")).toHaveAccessibleName("證件遺失 第 1 順位");
  await expect(card("信用卡盜刷")).toHaveAccessibleName("信用卡盜刷 第 2 順位");
  await expect(card("班機延誤")).toBeEnabled();

  await card("證件遺失").click();
  await card("信用卡盜刷").click();
  await expect(next).toBeDisabled();

  const note = page.getByLabel("其他在意的事（選填）");
  await expect(page.getByText("（0/200）")).toBeVisible();
  await note.fill("滑".repeat(201));
  await expect(note).toHaveValue("滑".repeat(200));
  await expect(page.getByText("（200/200）")).toBeVisible();
});

const noOverflow = (page: Page) =>
  page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth);

// 手機版（390px）：日程頁、1-2 都沒有橫向捲動；日程頁單欄、出發日與回程日不重疊。
test("手機寬度（390px）下日程頁、1-2 沒有橫向捲動", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page, "e2e-compare-mobile@example.com");
  await page.goto("/compare/new");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("規劃旅遊日程");
  expect(await noOverflow(page)).toBe(true);

  await fillTrip(page, "日本", 7, 11);
  await expect(page.getByRole("tab")).toHaveCount(5);
  await slot(page, 9).getByRole("button", { name: /空檔/ }).click();
  await page
    .getByTestId("activity-dialog")
    .getByLabel("要做什麼")
    .fill("一個名字很長很長很長很長很長很長很長很長很長很長很長很長的活動");
  await page.getByTestId("activity-dialog").getByRole("button", { name: "儲存" }).click();
  await expect(slot(page, 9).getByTestId("activity-card")).toBeVisible();
  expect(await noOverflow(page)).toBe(true);
  const side = await page.getByLabel("目的地").boundingBox();
  const main = await page.getByTestId("trip-banner").boundingBox();
  expect(side && main && main.y > side.y).toBeTruthy(); // 單欄：日程在表單下面

  await page.getByRole("button", { name: "下一步" }).click();
  await expect(page).toHaveURL(/\/concerns$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("選擇最在意的點");
  for (const name of ["突然生病就醫", "班機延誤", "證件遺失"]) {
    await page.getByRole("button", { name: new RegExp(`^${name}`) }).click();
  }
  expect(await noOverflow(page)).toBe(true);
  const a = await page.getByRole("button", { name: /^突然生病就醫/ }).boundingBox();
  const b = await page.getByRole("button", { name: /^需要緊急送醫或家屬趕來/ }).boundingBox();
  expect(a && b && b.y === a.y && b.x > a.x).toBeTruthy(); // 手機兩欄：第二張和第一張同一排
});

// 按下一步存檔時整頁蓋「正在比較方案…」。
test("1-2 按下一步存檔時顯示載入遮罩", async ({ page }) => {
  await login(page, "e2e-compare-overlay@example.com");
  await page.goto("/compare/new");
  await fillTrip(page, "日本", 7, 11);
  await page.getByRole("button", { name: "下一步" }).click();
  await expect(page).toHaveURL(/\/concerns$/);
  await page.getByRole("button", { name: /^班機延誤/ }).click();
  // 讓 PATCH 慢一點，遮罩才看得到
  await page.route("**/api/v1/comparisons/*", async (route) => {
    if (route.request().method() === "PATCH") await new Promise((r) => setTimeout(r, 800));
    await route.continue();
  });
  await page.getByRole("button", { name: "下一步" }).click();
  await expect(page.getByRole("status").filter({ hasText: "正在比較方案…" })).toBeVisible();
  await expect(page).toHaveURL(/\/result$/);
});

// 城市結尾有空白（打字打到一半）：存檔送 trim 後的值，不能因為畫面上的值與已存的不同而一直重送。
test("城市結尾有空白時只存一次，不會無限重送", async ({ page }) => {
  await login(page, "e2e-itinerary-trailing-space@example.com");
  await page.goto("/compare/new");
  const id = await fillTrip(page, "日本", 7, 11);
  let patches = 0;
  await page.route(`**/api/v1/comparisons/${id}`, async (route) => {
    if (route.request().method() === "PATCH") patches += 1;
    await route.continue();
  });
  await page.getByLabel("城市").fill("New York ");
  await expect(page.getByTestId("trip-banner")).toContainText("New York，日本");
  await page.waitForTimeout(1500); // 自動存檔的等待是 0.5 秒：多等幾輪看有沒有重送
  expect(patches).toBe(1);
});
