import { expect, test } from "@playwright/test";
import { login } from "./helpers";

// 帳號頁「刪除我的所有資料」（ADR-0009、#29）：二次確認，刪完保單不見、帳號仍在。
test("刪除我的所有資料：要二次確認，刪除後保單清空、仍登入", async ({ page }) => {
  await login(page, `e2e-delete-data-${Date.now()}@example.com`);
  const api = page.request;
  const options = await (await api.get("/api/v1/plans/fubon_advanced/sum-insured-options")).json();
  const created = await api.post("/api/v1/policies/manual", {
    data: {
      plan_id: "fubon_advanced",
      sum_insured: options.sum_insured[0],
      start_date: "2026-07-05",
      end_date: "2026-07-10",
    },
  });
  expect(created.ok()).toBeTruthy();
  expect((await (await api.get("/api/v1/policies")).json()).length).toBe(1);

  await page.goto("/account");
  await page.getByTestId("delete-my-data-start").click();
  // 先出現確認，這時資料還在
  await expect(page.getByTestId("delete-my-data-confirm")).toBeVisible();
  expect((await (await api.get("/api/v1/policies")).json()).length).toBe(1);

  // 選「保留」會收起確認，資料不動
  await page.getByRole("button", { name: "保留" }).click();
  await expect(page.getByTestId("delete-my-data-confirm")).toBeHidden();
  expect((await (await api.get("/api/v1/policies")).json()).length).toBe(1);

  await page.getByTestId("delete-my-data-start").click();
  await page.getByTestId("delete-my-data-yes").click();
  await expect(page).toHaveURL(/\/$/);
  await expect.poll(async () => (await (await api.get("/api/v1/policies")).json()).length).toBe(0);
  // 帳號保留，仍是登入狀態
  expect((await api.get("/api/v1/auth/me")).status()).toBe(200);
});

// 入口：header 右上的使用者名稱連到我的帳號，不用手打網址
test("header 的使用者名稱連到我的帳號，那裡有刪除我的所有資料", async ({ page }) => {
  await login(page, `e2e-account-link-${Date.now()}@example.com`);
  await page.getByTestId("header-account-link").click();
  await expect(page).toHaveURL(/\/account$/);
  await expect(page.getByTestId("delete-my-data-start")).toBeVisible();
});
