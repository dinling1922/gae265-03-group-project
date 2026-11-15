import { expect, type Page, test } from "@playwright/test";
import { login } from "./helpers";

// 2-2g 現況確認彈窗（#345）：第一次描述問完才跳；「還要補充或修改」回對話、有「回到確認」；
// 沒確認就離開，重新開啟回 2-2 再跳；確認才進行程頁。用假 5 號（問兩輪後結束）。
const SLOW = 15_000;

async function setup(page: Page, name: string) {
  await login(page, `e2e-confirm-${name}-${Date.now()}@example.com`);
  const api = page.request;
  const options = await (await api.get("/api/v1/plans/fubon_advanced/sum-insured-options")).json();
  const policy = await (
    await api.post("/api/v1/policies/manual", {
      data: {
        plan_id: "fubon_advanced",
        sum_insured: options.sum_insured[0],
        start_date: "2026-07-05",
        end_date: "2026-07-10",
      },
    })
  ).json();
  await api.patch(`/api/v1/policies/${policy.id}`, { data: { destination: "日本" } });
  return policy.id as string;
}

async function say(page: Page, text: string) {
  const replied = page.waitForResponse(
    (r) => r.url().endsWith("/messages") && r.request().method() === "POST",
    { timeout: SLOW },
  );
  await page.getByTestId("message").fill(text);
  await page.getByTestId("send").click();
  await replied;
}

/** 從描述頁送出第一句，問到假 5 號結束（兩輪追問）；回事件網址。 */
async function describeUntilFinished(page: Page, policyId: string) {
  await page.goto(`/incident/new/${policyId}`);
  await page.getByTestId("message").fill("班機延誤了六小時");
  await page.getByTestId("send").click();
  await expect(page).toHaveURL(/\/incident\/[0-9a-f-]{36}$/, { timeout: SLOW });
  await expect(page.getByTestId("messages").locator("li")).toHaveCount(3, { timeout: SLOW });
  await say(page, "班機延誤了六小時");
  await say(page, "昨天在東京成田機場");
  return page.url();
}

test.beforeEach(async ({ request }) => {
  const config = await (await request.get("/api/v1/auth/config")).json();
  test.skip(!config.dev_login, "後端沒有設 DEV_LOGIN_ENABLED=true");
  test.skip(!(await request.get("/api/v1/plans")).ok(), "條款庫沒有載入，先 make seed");
});

test("問完跳彈窗，沒有關閉鈕；補充再回到確認；確認才進行程頁", async ({ page }) => {
  const policyId = await setup(page, "flow");
  await describeUntilFinished(page, policyId);

  const dialog = page.getByTestId("confirm-dialog");
  await expect(dialog).toBeVisible({ timeout: SLOW });
  await expect(dialog.getByRole("heading", { name: "確認目前的狀況" })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "確認，進入待辦事項" })).toBeVisible();
  // 沒有關閉鈕：Esc 與點外面都不會關
  await page.keyboard.press("Escape");
  await expect(dialog).toBeVisible();
  // 確認前行程裡還沒有這個事件
  expect((await page.request.get(`/api/v1/policies/${policyId}/trip`)).status()).toBe(404);

  // 還要補充或修改：關閉彈窗，對話顯示固定句，左下有「回到確認」
  await dialog.getByTestId("confirm-supplement").click();
  await expect(dialog).toBeHidden();
  await expect(page.getByTestId("messages")).toContainText("好的，請說明要補充或修改的地方。");
  await page.getByTestId("back-to-confirm").click();
  await expect(dialog).toBeVisible();

  await dialog.getByTestId("confirm-yes").click();
  await expect(page).toHaveURL(new RegExp(`/trip/${policyId}\\?incident=[0-9a-f-]{36}$`), {
    timeout: SLOW,
  });
  await expect(page.getByTestId("trip-incident")).toHaveCount(1);
});

test("沒確認就離開：首頁「我的行程」顯示描述中，點進去回 2-2 再跳彈窗", async ({ page }) => {
  const policyId = await setup(page, "resume");
  const incidentUrl = await describeUntilFinished(page, policyId);
  await expect(page.getByTestId("confirm-dialog")).toBeVisible({ timeout: SLOW });

  await page.goto("/");
  const row = page.getByTestId("trip-row");
  await expect(row).toHaveCount(1);
  await expect(page.getByTestId("trip-row-subtitle")).toHaveText("描述中");
  await row.click();
  await expect(page).toHaveURL(incidentUrl);
  await expect(page.getByTestId("confirm-dialog")).toBeVisible({ timeout: SLOW });
});

// #409：橫式矮視窗、鍵盤開著時底部動作列會被蓋住，「回到確認」搬到輸入列旁邊；直式維持在底部
test("橫式矮視窗：「回到確認」在送出鈕旁；直式在底部動作列", async ({ page }) => {
  const policyId = await setup(page, "landscape");
  await describeUntilFinished(page, policyId);
  const dialog = page.getByTestId("confirm-dialog");
  await expect(dialog).toBeVisible({ timeout: SLOW });
  await dialog.getByTestId("confirm-supplement").click();
  await expect(dialog).toBeHidden();

  const back = page.getByTestId("back-to-confirm");
  const send = page.getByTestId("send");

  // 一般（直式或桌機）高度：只有一個，在送出鈕下方的動作列
  await expect(back).toHaveCount(1);
  const sendBox = await send.boundingBox();
  const normalBox = await back.boundingBox();
  expect(normalBox && sendBox && normalBox.y > sendBox.y + sendBox.height).toBe(true);

  // 橫式矮視窗：仍然只有一個，和送出鈕同一列
  await page.setViewportSize({ width: 920, height: 419 });
  await expect(back).toHaveCount(1);
  const shortSend = await send.boundingBox();
  const shortBack = await back.boundingBox();
  expect(shortSend && shortBack).toBeTruthy();
  if (shortSend && shortBack) {
    const overlap =
      Math.min(shortSend.y + shortSend.height, shortBack.y + shortBack.height) -
      Math.max(shortSend.y, shortBack.y);
    expect(overlap).toBeGreaterThan(0);
  }
  await back.click();
  await expect(dialog).toBeVisible();

  // 轉回一般高度：又回到底部動作列
  await dialog.getByTestId("confirm-supplement").click();
  await page.setViewportSize({ width: 1280, height: 720 });
  await expect(back).toHaveCount(1);
  const againBox = await back.boundingBox();
  const againSend = await send.boundingBox();
  expect(againBox && againSend && againBox.y > againSend.y + againSend.height).toBe(true);
});
