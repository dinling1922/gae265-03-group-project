import path from "node:path";
import { expect, type Page, test } from "@playwright/test";
import { login } from "./helpers";

// 票 #444：上傳保單時顯示縮圖，回應回來後依真實結果依序亮出保單解析的步驟（總長 ≤3 秒、可略過），
// 播完（或略過）才把值填入表單並啟用「繼續」。不測精確秒數，只測順序與最終狀態。
const fixture = (name: string) => path.join(import.meta.dirname, "fixtures", name);
const POLICY_PDF = fixture("fubon-policy-c-plan.pdf");
const NO_COUNTRY_PDF = fixture("fubon-policy-advanced-no-country.pdf");
const STEPS = [1, 2, 3, 4, 5, 6];

async function openUpload(page: Page, name: string) {
  await login(page, `e2e-anim-${name}-${Date.now()}@example.com`);
  await page.goto("/incident/new");
  await page.getByTestId("mode-upload").click();
}

const step = (page: Page, n: number) => page.getByTestId(`policy-step-${n}`);

test.beforeEach(async ({ request }) => {
  const config = await (await request.get("/api/v1/auth/config")).json();
  test.skip(!config.dev_login, "後端沒有設 DEV_LOGIN_ENABLED=true");
  test.skip(!(await request.get("/api/v1/plans")).ok(), "條款庫沒有載入，先 make seed");
});

test("上傳後先出現縮圖，步驟依序亮出實際讀到的值，播完才填入表單並啟用繼續", async ({ page }) => {
  await openUpload(page, "reveal");
  await page.getByTestId("policy-file").setInputFiles(POLICY_PDF);

  // 縮圖：第一頁渲染出來
  await expect(page.getByTestId("policy-thumb")).toHaveAttribute("data-kind", "canvas");

  // 第一步亮出時，後面的還沒好、表單還沒填、不能繼續
  await expect(step(page, 1)).toHaveAttribute("data-state", "done");
  await expect(step(page, 6)).not.toHaveAttribute("data-state", "done");
  await expect(page.getByTestId("confirm-continue")).toBeDisabled();
  await expect(page.getByTestId("confirm-sum-insured")).not.toHaveValue("6000000");

  // 全部亮完：每一步都有實際讀到的值
  // 6 步約 2.7 秒，加上上傳時間，逾時放寬
  for (const n of STEPS)
    await expect(step(page, n)).toHaveAttribute("data-state", "done", { timeout: 10_000 });
  await expect(step(page, 3)).toContainText("6,000,000");
  await expect(step(page, 4)).toContainText("日本");
  await expect(step(page, 5)).toContainText("官網C方案");
  await expect(step(page, 6)).toContainText("班機延誤理賠文件簡化附加條款");

  // 這時才填入表單、啟用繼續
  await expect(page.getByTestId("confirm-sum-insured")).toHaveValue("6000000");
  await expect(page.getByTestId("confirm-destination")).toHaveValue("日本");
  await expect(page.getByTestId("confirm-continue")).toBeEnabled();
});

test("按略過直接到結果：步驟全部完成、表單填入、可以繼續", async ({ page }) => {
  await openUpload(page, "skip");
  await page.getByTestId("policy-file").setInputFiles(POLICY_PDF);
  await expect(step(page, 1)).toBeVisible();
  await page.getByTestId("policy-skip").click();

  for (const n of STEPS) await expect(step(page, n)).toHaveAttribute("data-state", "done");
  await expect(page.getByTestId("confirm-sum-insured")).toHaveValue("6000000");
  await expect(page.getByTestId("confirm-continue")).toBeEnabled();
  await expect(page.getByTestId("policy-skip")).toHaveCount(0);
});

test("保單上沒印旅遊國家：那一步標沒讀到，不假裝成功", async ({ page }) => {
  await openUpload(page, "missing");
  await page.getByTestId("policy-file").setInputFiles(NO_COUNTRY_PDF);
  await page.getByTestId("policy-skip").click();

  await expect(step(page, 4)).toHaveAttribute("data-state", "missing");
  await expect(step(page, 4)).toContainText("沒讀到，請手動補");
  await expect(page.getByTestId("mark-destination")).toContainText("保單上讀不到");
});

test("讀不出來的檔案：沒有假步驟，縮圖或圖示保留，可以重新選擇", async ({ page }) => {
  await openUpload(page, "fail");
  await page.getByTestId("policy-file").setInputFiles({
    name: "not-a-policy.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4\n%%EOF\n"),
  });

  await expect(page.getByTestId("policy-thumb")).toBeVisible();
  await expect(page.getByTestId("policy-reselect")).toBeVisible();
  for (const n of STEPS) await expect(step(page, n)).not.toHaveAttribute("data-state", "done");
  await expect(page.getByTestId("confirm-continue")).toBeDisabled();

  // 重新選擇後可以正常上傳
  await page.getByTestId("policy-file").setInputFiles(POLICY_PDF);
  await page.getByTestId("policy-skip").click();
  await expect(page.getByTestId("confirm-continue")).toBeEnabled();
});

test("偏好減少動態：沒有流光與逐步動畫，結果直接出現", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await openUpload(page, "reduced");
  await page.getByTestId("policy-file").setInputFiles(POLICY_PDF);

  for (const n of STEPS) await expect(step(page, n)).toHaveAttribute("data-state", "done");
  await expect(page.getByTestId("policy-scan")).toHaveAttribute("data-motion", "off");
  await expect(page.getByTestId("policy-skip")).toHaveCount(0);
  await expect(page.getByTestId("confirm-sum-insured")).toHaveValue("6000000");
  await expect(page.getByTestId("confirm-continue")).toBeEnabled();
});

const VIEWPORTS = [
  { name: "桌機", width: 1280, height: 800 },
  { name: "手機直式", width: 390, height: 844 },
  { name: "手機橫式矮視窗", width: 844, height: 390 },
];

for (const vp of VIEWPORTS) {
  test(`${vp.name}：沒有橫向捲動，略過鈕點擊目標 ≥44px，繼續鈕可捲到`, async ({ page }) => {
    await page.setViewportSize({ width: vp.width, height: vp.height });
    await openUpload(page, `layout-${vp.width}`);
    await page.getByTestId("policy-file").setInputFiles(POLICY_PDF);

    const skip = page.getByTestId("policy-skip");
    await expect(skip).toBeVisible();
    await expect
      .poll(async () => (await skip.boundingBox())?.height ?? 0)
      .toBeGreaterThanOrEqual(44);
    await expect
      .poll(async () => (await skip.boundingBox())?.width ?? 0)
      .toBeGreaterThanOrEqual(44);
    await skip.click();

    await expect(page.getByTestId("confirm-continue")).toBeEnabled();
    await page.getByTestId("confirm-continue").scrollIntoViewIfNeeded();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    );
    expect(overflow).toBeLessThanOrEqual(0);
  });
}
