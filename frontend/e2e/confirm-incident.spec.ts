import { expect, type Page, test } from "@playwright/test";
import {
  login,
  scrolledTo,
  spyScrollIntoView,
  tripTransition,
  watchTripTransition,
} from "./helpers";

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
  await spyScrollIntoView(page);
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

  await watchTripTransition(page);
  await dialog.getByTestId("confirm-yes").click();
  await expect(page).toHaveURL(new RegExp(`/trip/${policyId}\\?incident=[0-9a-f-]{36}$`), {
    timeout: SLOW,
  });
  await expect(page.getByTestId("trip-incident")).toHaveCount(1);
  // 按確認後到行程頁之間要有載入遮罩（#445）
  expect((await tripTransition(page)).overlay, "換頁時要有「正在整理您的行程」遮罩").toBe(true);
  // 第一個事故也一樣：有待辦就捲到理賠待辦（#449）
  await expect(page.getByTestId("todos-section")).toBeInViewport();
  expect(await scrolledTo(page)).toContain("todos-section");
});

// #449：正式站已有用舊流程建立（不用確認、狀態 confirmed）還在追問的事故，部署後問完仍要有遮罩並進行程頁
test("舊流程建立（不用確認）的事故問完：有遮罩，不跳彈窗，直接進行程頁並捲到理賠待辦", async ({
  page,
}) => {
  const policyId = await setup(page, "legacy");
  await spyScrollIntoView(page);
  const created = await (
    await page.request.post(`/api/v1/policies/${policyId}/incidents`, {
      data: { description: "班機延誤了六小時", require_confirmation: false },
    })
  ).json();
  await page.goto(`/incident/${created.incident.incident_id}`);
  await expect(page.getByTestId("message")).toBeVisible();

  await say(page, "班機延誤了六小時");
  await watchTripTransition(page);
  await say(page, "昨天在東京成田機場");

  await expect(page).toHaveURL(new RegExp(`/trip/${policyId}\\?incident=[0-9a-f-]{36}$`), {
    timeout: SLOW,
  });
  await expect(page.getByTestId("trip-incident")).toHaveCount(1);
  await expect(page.getByTestId("confirm-dialog")).toHaveCount(0);
  const seen = await tripTransition(page);
  expect(seen.overlay, "換頁時要有「正在整理您的行程」遮罩").toBe(true);
  expect(seen.cardCounts, "不能先閃出別的張數").toEqual([1]);
  await expect(page.getByTestId("todos-section")).toBeInViewport();
  expect(await scrolledTo(page)).toContain("todos-section");
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

  // 改視窗大小後，按鈕會換位置（元件重新渲染），所以用 poll 等位置穩定，不直接讀一次
  async function layout(): Promise<"below" | "same-row" | "other"> {
    const s = await send.boundingBox();
    const b = await back.boundingBox();
    if (!s || !b) return "other";
    if (b.y > s.y + s.height) return "below";
    const overlap = Math.min(s.y + s.height, b.y + b.height) - Math.max(s.y, b.y);
    return overlap > 0 ? "same-row" : "other";
  }

  // 一般（直式或桌機）高度：只有一個，在送出鈕下方的動作列
  await expect(back).toHaveCount(1);
  await expect.poll(layout).toBe("below");

  // 橫式矮視窗：仍然只有一個，和送出鈕同一列
  await page.setViewportSize({ width: 920, height: 419 });
  await expect.poll(layout).toBe("same-row");
  await expect(back).toHaveCount(1);
  await back.click();
  await expect(dialog).toBeVisible();

  // 轉回一般高度：又回到底部動作列
  await dialog.getByTestId("confirm-supplement").click();
  await expect(dialog).toBeHidden();
  await page.setViewportSize({ width: 1280, height: 720 });
  await expect.poll(layout).toBe("below");
  await expect(back).toHaveCount(1);
});
