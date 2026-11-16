import { expect, type Locator, test } from "@playwright/test";
import { login } from "./helpers";

// 手機版（390px）導覽列與首頁連結的點擊目標至少 44px 高（#408），而且版面沒有橫向捲動。
// 用負 margin 抵銷 padding，所以版面高度不變；桌機版不受影響。

async function box(locator: Locator) {
  const b = await locator.boundingBox();
  if (!b) throw new Error("找不到元素");
  return b;
}

test.beforeEach(async ({ request }) => {
  const config = await (await request.get("/api/v1/auth/config")).json();
  test.skip(!config.dev_login, "後端沒有設 DEV_LOGIN_ENABLED=true");
});

test("手機版：Logo、使用者名稱、登出至少 44px 高，沒有橫向捲動", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  await login(page, `e2e-tap-${Date.now()}@example.com`);

  for (const locator of [
    page.getByRole("link", { name: "好險有你" }),
    page.getByTestId("header-account-link"),
    page.getByRole("button", { name: "登出" }),
  ]) {
    expect((await box(locator)).height).toBeGreaterThanOrEqual(44);
  }
  const scrollsSideways = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth,
  );
  expect(scrollsSideways).toBe(false);
});

test("手機版：首頁「看全部」至少 44px 高", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  await login(page, `e2e-tap-all-${Date.now()}@example.com`);
  for (let i = 0; i < 4; i++) {
    const r = await page.request.post("/api/v1/comparisons", {
      data: { destination: "日本", depart_date: "2027-03-01", return_date: "2027-03-05", age: 30 },
    });
    expect(r.ok()).toBeTruthy();
  }
  await page.goto("/");
  const all = page.getByRole("link", { name: "看全部" }).first();
  await expect(all).toBeVisible();
  expect((await box(all)).height).toBeGreaterThanOrEqual(44);
});

test("桌機版：導覽列高度與登出按鈕尺寸維持原樣", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await login(page, `e2e-tap-desktop-${Date.now()}@example.com`);
  expect((await box(page.getByRole("button", { name: "登出" }))).height).toBe(40);
  expect((await box(page.locator("header"))).height).toBe(96);
});
