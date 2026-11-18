import { expect, test } from "@playwright/test";
import { login } from "./helpers";

test.beforeEach(async ({ request }) => {
  const config = await (await request.get("/api/v1/auth/config")).json();
  test.skip(!config.dev_login, "後端沒有設 DEV_LOGIN_ENABLED=true");
});

// 範例日程的座標是寫死的，載入時不該呼叫 Nominatim
test("在 /compare/new 載入主 demo：網址換成日程頁、旅行規劃有值、20 個活動", async ({ page }) => {
  await login(page, "e2e-demo-main@example.com");
  let geocoded = false;
  await page.route("https://nominatim.openstreetmap.org/**", (route) => {
    geocoded = true;
    return route.abort();
  });
  await page.goto("/compare/new");

  await page.getByTestId("demo-menu").click();
  await page.getByTestId("demo-main").click();

  await expect(page).toHaveURL(/\/compare\/[0-9a-f-]{36}\/itinerary$/);
  await expect(page.getByLabel("目的地")).toHaveValue("日本");
  await expect(page.getByLabel("城市")).toHaveValue("東京");
  await expect(page.getByText("共 7 天")).toBeVisible();
  await expect(page.getByTestId("clear-activities")).toContainText("（20）");
  expect(geocoded).toBe(false);
});

test("已有活動時載入範例要先確認：取消不變，確認後換成 3 個活動", async ({ page }) => {
  await login(page, "e2e-demo-confirm@example.com");
  await page.goto("/compare/new");
  await page.getByTestId("demo-menu").click();
  await page.getByTestId("demo-main").click();
  await expect(page.getByTestId("clear-activities")).toContainText("（20）");

  await page.getByTestId("demo-menu").click();
  await page.getByTestId("demo-2-1").click();
  const dialog = page.getByTestId("clear-dialog");
  await expect(dialog).toContainText("清除現有的 20 個活動");
  await dialog.getByRole("button", { name: "取消" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByTestId("clear-activities")).toContainText("（20）");

  await page.getByTestId("demo-menu").click();
  await page.getByTestId("demo-2-1").click();
  await page.getByTestId("clear-confirm").click();
  await expect(page.getByTestId("clear-activities")).toContainText("（3）");
  await expect(page.getByLabel("城市")).toHaveValue("京都");
  await expect(page.getByText("共 4 天")).toBeVisible();
});

// 回歸：改了日程再回 1-2，預先勾選與推薦要用新的結果，不能拿上次的快取
test("載入範例後進 1-2、回日程頁換另一份範例再進 1-2：勾選與推薦都是新的結果", async ({ page }) => {
  await login(page, "e2e-demo-stale-suggestions@example.com");
  const first = [
    { concern: "班機延誤", reason: "舊結果 1" },
    { concern: "突然生病就醫", reason: "舊結果 2" },
    { concern: "行程取消或更改", reason: "舊結果 3" },
  ];
  const second = [
    { concern: "信用卡盜刷", reason: "新結果 1" },
    { concern: "證件遺失", reason: "新結果 2" },
    { concern: "行李延誤", reason: "新結果 3" },
  ];
  let calls = 0;
  await page.route("**/api/v1/comparisons/*/concern-suggestions", (route) =>
    route.fulfill({ json: calls++ === 0 ? first : second }),
  );

  await page.goto("/compare/new");
  await page.getByTestId("demo-menu").click();
  await page.getByTestId("demo-main").click();
  await expect(page.getByTestId("clear-activities")).toContainText("（20）");
  await page.getByRole("button", { name: "下一步" }).click();
  await expect(page.getByTestId("concern-suggestions")).toContainText("舊結果 1");

  // 不取消勾選就回日程頁：載入範例會把在意事項一起重設，推薦才能重新預先勾選
  await page.getByRole("button", { name: "上一步" }).click();
  await page.getByTestId("demo-menu").click();
  await page.getByTestId("demo-2-3").click();
  await page.getByTestId("clear-confirm").click();
  await expect(page.getByTestId("clear-activities")).toContainText("（3）");
  await page.getByRole("button", { name: "下一步" }).click();

  const box = page.getByTestId("concern-suggestions");
  await expect(box).toContainText("新結果 1");
  await expect(box).not.toContainText("舊結果");
  await expect(page.getByRole("button", { name: /^信用卡盜刷/ })).toHaveAccessibleName(
    "信用卡盜刷 第 1 順位",
  );
  await expect(page.getByRole("button", { name: /^班機延誤/ })).not.toHaveAccessibleName(/順位/);
});
