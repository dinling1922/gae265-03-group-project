import { expect, type Page, test } from "@playwright/test";
import { login } from "./helpers";

// 帳號頁「存取權杖」（ADR-0032、#475）：建立只顯示一次明文、列表狀態、撤銷、5 把上限。

async function create(page: Page, name: string) {
  await page.getByTestId("api-token-name").fill(name);
  await page.getByTestId("api-token-create").click();
}

test("建立：明文只顯示一次、可複製，重新整理後不再出現", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await login(page, `e2e-token-create-${Date.now()}@example.com`);
  await page.goto("/account");
  await expect(page.getByTestId("api-tokens-empty")).toBeVisible();

  await create(page, "我的助理");
  const secret = page.getByTestId("api-token-secret");
  await expect(secret).toBeVisible();
  const plaintext = await secret.inputValue();
  expect(plaintext.length).toBeGreaterThan(20);

  await page.getByTestId("api-token-copy").click();
  await expect(page.getByTestId("api-token-copy")).toHaveText("已複製");
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(plaintext);

  // 列表有這把：名稱、前綴、狀態；明文不在列表裡
  const row = page.getByTestId("api-token");
  await expect(row).toHaveCount(1);
  await expect(row.getByTestId("api-token-title")).toHaveText("我的助理");
  await expect(row.getByTestId("api-token-status")).toHaveText("有效");
  await expect(row).toContainText("尚未使用");
  await expect(row).not.toContainText(plaintext);

  // 沒存進瀏覽器儲存空間；重新整理後明文消失，列表仍在
  const stored = await page.evaluate(() =>
    JSON.stringify([{ ...localStorage }, { ...sessionStorage }]),
  );
  expect(stored).not.toContain(plaintext);
  await page.reload();
  await expect(page.getByTestId("api-token")).toHaveCount(1);
  await expect(page.getByTestId("api-token-secret")).toHaveCount(0);
  await expect(page.locator("body")).not.toContainText(plaintext);
});

test("複製失敗時有備案：提示並可選取文字", async ({ page }) => {
  await login(page, `e2e-token-copyfail-${Date.now()}@example.com`);
  await page.goto("/account");
  await page.evaluate(() => {
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: () => Promise.reject(new Error("denied")) },
    });
  });
  await create(page, "複製失敗");
  await page.getByTestId("api-token-copy").click();
  await expect(page.getByTestId("copy-failed")).toBeVisible();
  await page.getByTestId("api-token-secret").focus();
  // 聚焦時自動全選，使用者可直接手動複製
  const range = await page
    .getByTestId("api-token-secret")
    .evaluate((el: HTMLInputElement) => [el.selectionStart, el.selectionEnd, el.value.length]);
  expect(range[0]).toBe(0);
  expect(range[1]).toBe(range[2]);
});

test("撤銷：要確認，保留不動，確定後狀態立即變已撤銷", async ({ page }) => {
  await login(page, `e2e-token-revoke-${Date.now()}@example.com`);
  await page.goto("/account");
  await create(page, "要撤銷的");
  await page.getByTestId("api-token-secret-close").click();

  const row = page.getByTestId("api-token");
  await row.getByTestId("api-token-revoke").click();
  await expect(page.getByTestId("api-token-confirm")).toBeVisible();
  await page.getByTestId("api-token-confirm").getByRole("button", { name: "保留" }).click();
  await expect(page.getByTestId("api-token-confirm")).toBeHidden();
  await expect(row.getByTestId("api-token-status")).toHaveText("有效");

  await row.getByTestId("api-token-revoke").click();
  await page.getByTestId("api-token-revoke-yes").click();
  await expect(row.getByTestId("api-token-status")).toHaveText("已撤銷");
  await expect(row.getByTestId("api-token-revoke")).toHaveCount(0);
});

test("達 5 把上限：建立停用並說明原因，撤銷一把後恢復", async ({ page }) => {
  await login(page, `e2e-token-limit-${Date.now()}@example.com`);
  const api = page.request;
  for (let i = 1; i <= 5; i++) {
    expect((await api.post("/api/v1/me/api-tokens", { data: { name: `t${i}` } })).status()).toBe(
      201,
    );
  }
  await page.goto("/account");
  await expect(page.getByTestId("api-token")).toHaveCount(5);
  await expect(page.getByTestId("api-token-limit")).toContainText("5 把");
  await expect(page.getByTestId("api-token-create")).toBeDisabled();

  await page.getByTestId("api-token-revoke").first().click();
  await page.getByTestId("api-token-revoke-yes").click();
  await expect(page.getByTestId("api-token-limit")).toHaveCount(0);
  await page.getByTestId("api-token-name").fill("第六把");
  await expect(page.getByTestId("api-token-create")).toBeEnabled();
});

test("畫面數量已過期時後端回 409，顯示明確訊息", async ({ page }) => {
  await login(page, `e2e-token-409-${Date.now()}@example.com`);
  await page.goto("/account");
  await expect(page.getByTestId("api-tokens-empty")).toBeVisible();
  // 另一個分頁（這裡用 API）把額度用完，畫面還不知道
  for (let i = 1; i <= 5; i++) {
    await page.request.post("/api/v1/me/api-tokens", { data: { name: `t${i}` } });
  }
  await create(page, "第六把");
  await expect(page.getByTestId("api-token-error")).toContainText("最多 5 把");
  await expect(page.getByTestId("api-token-secret")).toHaveCount(0);
});

test("手機版：無橫向捲動、按鈕可點", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  await login(page, `e2e-token-mobile-${Date.now()}@example.com`);
  await page.goto("/account");
  await create(page, "手機上的一把很長很長很長很長很長很長很長很長很長很長的名稱");
  await expect(page.getByTestId("api-token-secret")).toBeVisible();
  await page.getByTestId("api-token-revoke").scrollIntoViewIfNeeded();
  await page.getByTestId("api-token-revoke").click();
  await expect(page.getByTestId("api-token-confirm")).toBeVisible();
  const scrollsSideways = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth,
  );
  expect(scrollsSideways).toBe(false);
  const box = await page.getByTestId("api-token-revoke-yes").boundingBox();
  expect(box?.height).toBeGreaterThanOrEqual(36);
  await page.getByTestId("api-token-revoke-yes").click();
  await expect(page.getByTestId("api-token-status")).toHaveText("已撤銷");
});
