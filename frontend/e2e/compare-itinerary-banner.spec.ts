import { expect, test } from "@playwright/test";
import { login } from "./helpers";

// 1x1 的 PNG：攔截 Unsplash 的圖片請求，測試不連外
const PNG = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==",
  "base64",
);

/** 今天往後 offset 天的 YYYY-MM-DD（瀏覽器的本地日期）。 */
function day(offset: number): string {
  const d = new Date();
  d.setDate(d.getDate() + offset);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

test.beforeEach(async ({ request }) => {
  const config = await (await request.get("/api/v1/auth/config")).json();
  test.skip(!config.dev_login, "後端沒有設 DEV_LOGIN_ENABLED=true");
});

test("日程頁橫幅依目的地顯示照片，換目的地就換圖", async ({ page }) => {
  await page.route("https://images.unsplash.com/**", (route) =>
    route.fulfill({ contentType: "image/png", body: PNG }),
  );
  await login(page, "e2e-itinerary-banner@example.com");
  await page.goto("/compare/new");

  await page.getByLabel("目的地").selectOption("日本");
  await page.getByLabel("出發日").fill(day(7));
  await page.getByLabel("回程日").fill(day(11));
  await expect(page).toHaveURL(/\/compare\/[0-9a-f-]{36}\/itinerary$/);

  const photo = page.getByTestId("trip-banner-photo");
  await expect(photo).toBeVisible();
  // 保留 ixid（Unsplash 靠它統計瀏覽），並帶橫幅尺寸
  await expect(photo).toHaveAttribute("src", /photo-1545569341-9eb8b30979d9.*ixid=.*w=1200&h=400/);

  await page.getByLabel("目的地").selectOption("韓國");
  await expect(photo).toHaveAttribute("src", /photo-1583833008338-31a6657917ab/);
  await expect(page.getByTestId("trip-banner")).toContainText("韓國");
});
