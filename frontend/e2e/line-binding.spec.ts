import { createHmac } from "node:crypto";
import { type APIRequestContext, expect, type Locator, type Page, test } from "@playwright/test";
import { login } from "./helpers";

// 帳號頁「綁定 LINE」（ADR-0033、#488）。
//
// 後端要啟用 LINE 這些測試才跑得起來（e2e-fake 的後端預設沒設定），啟動時帶：
//   LINE_CHANNEL_SECRET=e2e-secret LINE_CHANNEL_ACCESS_TOKEN=e2e-token \
//   TOKEN_ENCRYPTION_KEY=<Fernet.generate_key()> make e2e-fake args="e2e/line-binding.spec.ts"
// 這支 spec 讀同一個 LINE_CHANNEL_SECRET 來替模擬的 LINE 訊息簽章；沒設就略過需要後端的測試。
// 後端回覆 LINE 的呼叫會因為假權杖失敗，後端吞掉並記 log，不影響綁定。
const CHANNEL_SECRET = process.env.LINE_CHANNEL_SECRET;
const BACKEND = `http://127.0.0.1:${process.env.E2E_BACKEND_PORT ?? "8001"}`;
const SHOT_DIR = process.env.E2E_SHOT_DIR;

let seq = 0;
const uniq = () => `${Date.now()}${seq++}`;

/** 模擬 LINE 平台送一則使用者文字訊息到 webhook（帶正確簽章）。 */
async function sendLine(request: APIRequestContext, lineUserId: string, text: string) {
  const id = `e2e-${uniq()}`;
  const body = JSON.stringify({
    destination: "Uxxxx",
    events: [
      {
        type: "message",
        webhookEventId: id,
        replyToken: `token-${id}`,
        source: { type: "user", userId: lineUserId },
        message: { type: "text", id: "1", text },
      },
    ],
  });
  const signature = createHmac("sha256", CHANNEL_SECRET ?? "")
    .update(body)
    .digest("base64");
  const res = await request.post(`${BACKEND}/line/webhook`, {
    data: body,
    headers: { "X-Line-Signature": signature, "Content-Type": "application/json" },
  });
  expect(res.status(), await res.text()).toBe(200);
}

async function generate(page: Page): Promise<string> {
  await page.getByTestId("line-create-code").click();
  const code = (await page.getByTestId("line-code").textContent()) ?? "";
  expect(code).toMatch(/^\d{6}$/);
  return code;
}

async function setup(page: Page, name: string) {
  await login(page, `e2e-line-${name}-${Date.now()}@example.com`);
  await page.goto("/account");
}

async function noSidewaysScroll(page: Page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)).toBe(
    false,
  );
}

async function height(locator: Locator) {
  const b = await locator.boundingBox();
  if (!b) throw new Error("找不到元素");
  return b.height;
}

test("LINE 沒設定（後端回 404）時，帳號頁不顯示這個區塊", async ({ page }) => {
  // 用 page.route 回 404，不依賴後端有沒有設定 LINE：兩種執行方式都穩定
  await page.route("**/api/v1/me/line-binding", (route) =>
    route.fulfill({ status: 404, json: { detail: "沒有啟用 LINE 功能" } }),
  );
  await login(page, `e2e-line-off-${Date.now()}@example.com`);
  await page.goto("/account");
  await expect(page.getByTestId("api-tokens")).toBeVisible();
  await expect(page.getByTestId("line-binding")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "綁定 LINE" })).toHaveCount(0);
});

test("取得狀態失敗：顯示錯誤，重試後恢復", async ({ page }) => {
  let fail = true;
  await page.route("**/api/v1/me/line-binding", (route) =>
    fail ? route.fulfill({ status: 500, json: { detail: "boom" } }) : route.fallback(),
  );
  await login(page, `e2e-line-err-${Date.now()}@example.com`);
  await page.goto("/account");
  await expect(page.getByTestId("line-status-error")).toBeVisible();
  fail = false;
  await page.getByTestId("line-status-error").getByRole("button", { name: "重試" }).click();
  await expect(page.getByTestId("line-status-error")).toHaveCount(0);
});

test.describe("後端啟用 LINE", () => {
  test.skip(!CHANNEL_SECRET, "啟動 e2e-fake 時要帶 LINE_CHANNEL_SECRET 等環境變數（見檔頭）");

  // 清掉測試使用者的資料（含 LINE 綁定與權杖）；登入狀態還在才呼叫得到
  test.afterEach(async ({ page }) => {
    await page.request.delete("/api/v1/me/data");
  });

  test("產生綁定碼：告知經過 LINE、6 位數大字、倒數、說明帶入實際碼、可複製、不進瀏覽器儲存", async ({
    page,
    context,
  }) => {
    await context.grantPermissions(["clipboard-read", "clipboard-write"]);
    await setup(page, "create");
    await expect(page.getByTestId("line-privacy-notice")).toContainText("經過 LINE 平台");
    await expect(page.getByTestId("line-unbound")).toBeVisible();

    const code = await generate(page);
    await expect(page.getByTestId("line-countdown")).toHaveText(/剩餘 \d+ 分 \d{2} 秒/);
    await expect(page.getByTestId("line-countdown")).toContainText("9 分");
    await expect(page.getByTestId("line-code-box")).toContainText(`綁定 ${code}`);
    await expect(page.getByTestId("line-code-box")).toContainText("重新產生會讓前一組碼立刻失效");

    await page.getByTestId("line-copy").click();
    await expect(page.getByTestId("line-copy")).toHaveText("已複製");
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(code);

    const stored = await page.evaluate(() =>
      JSON.stringify([{ ...localStorage }, { ...sessionStorage }, location.href]),
    );
    expect(stored).not.toContain(code);
    // 重新整理後碼不會回來（只在元件狀態），仍是未綁定
    await page.reload();
    await expect(page.getByTestId("line-code")).toHaveCount(0);
    await expect(page.getByTestId("line-create-code")).toBeVisible();
  });

  test("複製失敗時有備案：提示並可選取", async ({ page }) => {
    await setup(page, "copyfail");
    await page.evaluate(() => {
      Object.defineProperty(navigator, "clipboard", {
        value: { writeText: () => Promise.reject(new Error("denied")) },
      });
    });
    await generate(page);
    await page.getByTestId("line-copy").click();
    await expect(page.getByTestId("line-copy-failed")).toBeVisible();
    await page.getByTestId("line-code").dblclick();
    const selected = await page.evaluate(() => window.getSelection()?.toString().trim());
    expect(selected).toMatch(/^\d{6}$/);
  });

  test("綁定完成後不用重新整理就切到已綁定；解除綁定要確認；解除後可再綁", async ({
    page,
    request,
  }) => {
    await setup(page, "bind");
    const lineUser = `Ue2e${uniq()}`;
    const code = await generate(page);
    await sendLine(request, lineUser, `綁定 ${code}`);

    // 輪詢每 3 秒一次
    await expect(page.getByTestId("line-bound")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("line-bound-done")).toBeVisible();
    await expect(page.getByTestId("line-bound-at")).toContainText(/綁定時間 \d{4}\/\d{2}\/\d{2}/);
    await expect(page.getByTestId("line-code")).toHaveCount(0);
    expect((await (await page.request.get("/api/v1/me/line-binding")).json()).bound).toBe(true);

    // 解除：先出現確認，保留不動
    await page.getByTestId("line-unbind").click();
    await expect(page.getByTestId("line-unbind-confirm")).toBeVisible();
    expect((await (await page.request.get("/api/v1/me/line-binding")).json()).bound).toBe(true);
    await page.getByTestId("line-unbind-confirm").getByRole("button", { name: "保留" }).click();
    await expect(page.getByTestId("line-unbind-confirm")).toBeHidden();
    await expect(page.getByTestId("line-bound")).toBeVisible();

    await page.getByTestId("line-unbind").click();
    await page.getByTestId("line-unbind-yes").click();
    await expect(page.getByTestId("line-unbound")).toBeVisible();
    await expect(page.getByTestId("line-bound")).toHaveCount(0);
    expect((await (await page.request.get("/api/v1/me/line-binding")).json()).bound).toBe(false);

    // 解除後同一個 LINE 可以再綁
    const again = await generate(page);
    await sendLine(request, lineUser, `綁定 ${again}`);
    await expect(page.getByTestId("line-bound")).toBeVisible({ timeout: 15_000 });
  });

  test("換一個 LINE 帳號：說明取代目前綁定，新 LINE 綁定後自動更新", async ({ page, request }) => {
    await setup(page, "rebind");
    const first = await generate(page);
    await sendLine(request, `Ue2e${uniq()}`, `綁定 ${first}`);
    await expect(page.getByTestId("line-bound")).toBeVisible({ timeout: 15_000 });
    const firstBoundAt = (await (await page.request.get("/api/v1/me/line-binding")).json())
      .bound_at;

    await page.getByTestId("line-rebind").click();
    await expect(page.getByTestId("line-replace-note")).toContainText("取代目前綁定");
    await expect(page.getByTestId("line-bound")).toBeVisible(); // 完成前仍顯示已綁定
    const second = await page.getByTestId("line-code").textContent();
    expect(second).toMatch(/^\d{6}$/);

    await sendLine(request, `Ue2e${uniq()}`, `綁定 ${second}`);
    await expect(page.getByTestId("line-code-box")).toHaveCount(0, { timeout: 15_000 });
    await expect(page.getByTestId("line-bound-done")).toBeVisible();
    const after = (await (await page.request.get("/api/v1/me/line-binding")).json()).bound_at;
    expect(after).not.toBe(firstBoundAt);
  });

  test("重新產生：舊碼立刻失效，只有新碼能綁", async ({ page, request }) => {
    await setup(page, "regen");
    const lineUser = `Ue2e${uniq()}`;
    const oldCode = await generate(page);
    await page.getByTestId("line-regenerate").click();
    await expect(page.getByTestId("line-code")).not.toHaveText(oldCode);
    const newCode = (await page.getByTestId("line-code").textContent()) ?? "";

    await sendLine(request, lineUser, `綁定 ${oldCode}`);
    await expect(page.getByTestId("line-bound")).toHaveCount(0);
    expect((await (await page.request.get("/api/v1/me/line-binding")).json()).bound).toBe(false);

    await sendLine(request, lineUser, `綁定 ${newCode}`);
    await expect(page.getByTestId("line-bound")).toBeVisible({ timeout: 15_000 });
  });

  test("綁定碼過期：倒數結束顯示已過期、停止倒數，可重新產生", async ({ page }) => {
    // 真實的有效期是 10 分鐘：把第一次產生的回應改成 3 秒後到期（page.clock 會連帶凍結
    // TanStack Query 的計時，較不穩）。重新產生時用後端原本的回應
    let first = true;
    await page.route("**/api/v1/me/line-binding/code", async (route) => {
      const res = await route.fetch();
      if (!first || !res.ok()) return route.fulfill({ response: res });
      first = false;
      const body = await res.json();
      body.expires_at = new Date(Date.now() + 3000).toISOString();
      return route.fulfill({ response: res, json: body });
    });
    await setup(page, "expire");
    const code = await generate(page);
    await expect(page.getByTestId("line-countdown")).toBeVisible();
    await expect(page.getByTestId("line-expired")).toBeVisible({ timeout: 10_000 });
    await expect(page.getByTestId("line-countdown")).toHaveCount(0);
    await expect(page.getByTestId("line-copy")).toHaveCount(0);
    await expect(page.getByTestId("line-code-box")).not.toContainText(`綁定 ${code}`);

    await page.getByTestId("line-regenerate").click();
    await expect(page.getByTestId("line-countdown")).toContainText("9 分");
    await expect(page.getByTestId("line-expired")).toHaveCount(0);
  });

  test("伺服器缺金鑰（503）：顯示明確訊息，不無聲失敗", async ({ page }) => {
    await page.route("**/api/v1/me/line-binding/code", (route) =>
      route.fulfill({ status: 503, json: { detail: "伺服器尚未設定 TOKEN_ENCRYPTION_KEY" } }),
    );
    await setup(page, "e503");
    await page.getByTestId("line-create-code").click();
    await expect(page.getByTestId("line-code-error")).toContainText(
      "伺服器還沒設好 LINE 綁定所需的金鑰，請聯絡管理者",
    );
    await expect(page.getByTestId("line-code")).toHaveCount(0);
  });

  test("其他錯誤（500）：顯示通用訊息", async ({ page }) => {
    await page.route("**/api/v1/me/line-binding/code", (route) =>
      route.fulfill({ status: 500, json: { detail: "boom" } }),
    );
    await setup(page, "e500");
    await page.getByTestId("line-create-code").click();
    await expect(page.getByTestId("line-code-error")).toContainText("產生綁定碼失敗");
  });

  test("手機 390px：未綁定、產生碼、已綁定三種畫面都沒有橫向捲動，按鈕至少 36px 高", async ({
    page,
    request,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await setup(page, "mobile");
    const section = page.getByTestId("line-binding");
    await section.scrollIntoViewIfNeeded();
    await noSidewaysScroll(page);
    expect(await height(page.getByTestId("line-create-code"))).toBeGreaterThanOrEqual(36);
    if (SHOT_DIR) await section.screenshot({ path: `${SHOT_DIR}/mobile-unbound.png` });

    const code = await generate(page);
    await noSidewaysScroll(page);
    for (const id of ["line-copy", "line-regenerate"]) {
      expect(await height(page.getByTestId(id))).toBeGreaterThanOrEqual(36);
    }
    if (SHOT_DIR) await section.screenshot({ path: `${SHOT_DIR}/mobile-code.png` });

    await sendLine(request, `Ue2e${uniq()}`, `綁定 ${code}`);
    await expect(page.getByTestId("line-bound")).toBeVisible({ timeout: 15_000 });
    await noSidewaysScroll(page);
    for (const id of ["line-unbind", "line-rebind"]) {
      expect(await height(page.getByTestId(id))).toBeGreaterThanOrEqual(36);
    }
    await page.getByTestId("line-unbind").click();
    await noSidewaysScroll(page);
    if (SHOT_DIR) await section.screenshot({ path: `${SHOT_DIR}/mobile-confirm.png` });
  });

  test("桌機截圖：產生碼與已綁定", async ({ page, request }) => {
    test.skip(!SHOT_DIR, "只在要截圖時跑（E2E_SHOT_DIR）");
    await page.setViewportSize({ width: 1280, height: 900 });
    await setup(page, "desktop");
    const section = page.getByTestId("line-binding");
    const code = await generate(page);
    await section.screenshot({ path: `${SHOT_DIR}/desktop-code.png` });
    await sendLine(request, `Ue2e${uniq()}`, `綁定 ${code}`);
    await expect(page.getByTestId("line-bound")).toBeVisible({ timeout: 15_000 });
    await section.screenshot({ path: `${SHOT_DIR}/desktop-bound.png` });
  });
});
