import { expect, type Page, test } from "@playwright/test";
import { expectPaper, login } from "./helpers";

// 2-2 描述事故：建立前（/incident/new/$policyId）旅客送出第一句才建立事件；追問中（/incident/$id）選項 Chip 與提問列。
// 用假 5 號：第一個追問附三個選項。需要測試登入與條款庫、方案資料（make seed）。
const SLOW = 15_000;

async function loginWithPolicy(page: Page, name: string) {
  await login(page, `e2e-describe-${name}-${Date.now()}@example.com`);
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

test.beforeEach(async ({ request }) => {
  const config = await (await request.get("/api/v1/auth/config")).json();
  test.skip(!config.dev_login, "後端沒有設 DEV_LOGIN_ENABLED=true");
  test.skip(!(await request.get("/api/v1/plans")).ok(), "條款庫沒有載入，先 make seed");
});

test("建立前：頁首帶保單的目的地與期間，只有固定第一句；沒送出就離開不會留下事件", async ({
  page,
}) => {
  const policyId = await loginWithPolicy(page, "before");

  await page.goto(`/incident/new/${policyId}`);
  await expectPaper(page);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("日本 2026/07/05～07/10");
  await expect(page.getByText("富邦國外旅平險-進階型 ·")).toBeVisible();
  const chat = page.getByTestId("messages");
  await expect(chat.locator("li")).toHaveCount(1);
  await expect(chat).toContainText("發生什麼事了？用一兩句話描述就好");
  // 範例是 5 號支援的狀況（班機延誤）；5 號不支援班機取消，照範例打會被回「無法協助判斷」
  await expect(chat).toContainText("例如：颱風來了，回台灣的班機延誤了。");
  await expect(chat).not.toContainText("班機被取消");
  await expect(page.getByTestId("quick-replies")).toHaveCount(0);
  // 輸入框為空時送出 disabled；有字才啟用，計數即時更新（上限 500）
  const send = page.getByTestId("send");
  await expect(send).toBeDisabled();
  await page.getByTestId("message").fill("班機延誤了");
  await expect(page.getByText("5/500")).toBeVisible();
  await expect(send).toBeEnabled();

  // 沒送出就離開：不會有事件
  await page.goto("/");
  const incidents = await (await page.request.get("/api/v1/incidents")).json();
  expect(incidents).toHaveLength(0);
});

test("建立前：送出第一句才建立事件，換到追問畫面；回上一步仍是同一份保單", async ({ page }) => {
  const policyId = await loginWithPolicy(page, "send");

  await page.goto(`/incident/new/${policyId}`);
  await page.getByRole("link", { name: "上一步" }).click();
  await expect(page).toHaveURL(new RegExp(`/incident/new\\?policy=${policyId}$`));
  await page.goBack();

  await page.getByTestId("message").fill("班機延誤了六小時");
  await page.getByTestId("send").click();
  await expect(page).toHaveURL(/\/incident\/[0-9a-f-]{36}$/, { timeout: SLOW });
  // 開場白、旅客的第一句、5 號的第一個追問（附選項）
  await expect(page.getByTestId("messages").locator("li")).toHaveCount(3, { timeout: SLOW });
  await expect(
    page.getByTestId("quick-replies").getByRole("button", { name: "生病受傷" }),
  ).toBeVisible();
  const incidents = await (await page.request.get("/api/v1/incidents")).json();
  expect(incidents).toHaveLength(1);
  // 換路由是取代：按上一頁不會回到空的對話
  await page.goBack();
  await expect(page).not.toHaveURL(new RegExp(`/incident/new/${policyId}$`));
});

test("追問中：頁首沿用保單資訊、沒有舊的標題與步驟列，點選項就是回答", async ({ page }) => {
  const policyId = await loginWithPolicy(page, "clarify");
  const createdRes = await page.request.post(`/api/v1/policies/${policyId}/incidents`, {
    data: { description: "班機延誤了六小時" },
  });
  const created = await createdRes.json();
  // 準備資料時伺服器回了錯誤：把狀態碼與內容丟出來，才看得到真正原因
  if (!created.incident)
    throw new Error(`POST incidents 回 ${createdRes.status()}：${JSON.stringify(created)}`);

  await page.goto(`/incident/${created.incident.incident_id}`);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("日本 2026/07/05～07/10");
  await expect(page.getByRole("navigation", { name: "理賠步驟" })).toHaveCount(0);
  await expect(page.getByTestId("message")).toHaveAttribute(
    "placeholder",
    "回答上面的問題，或點選項",
  );

  // 點選項：送出並收到下一句；舊問題的選項不再出現
  await page.getByTestId("quick-replies").getByRole("button", { name: "班機延誤" }).click();
  await expect(page.getByTestId("messages").locator("li")).toHaveCount(5, { timeout: SLOW });
  await expect(page.getByTestId("quick-replies")).toHaveCount(0);
  await expect(page.getByTestId("message")).toHaveAttribute("placeholder", "回答上面的問題");
});

test("手機寬度（390px）下 2-2 沒有橫向捲動，提問列上下排", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  const policyId = await loginWithPolicy(page, "mobile");
  await page.goto(`/incident/new/${policyId}`);
  await expect(page.getByTestId("message")).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
  );
  expect(overflow).toBe(false);
  const input = await page.getByTestId("message").boundingBox();
  const submit = await page.getByTestId("send").boundingBox();
  expect(input && submit && submit.y > input.y + input.height - 1).toBeTruthy();
});

test("2-2 送出第一句：旅客的話先出現，接著是輸入中的泡泡，沒有全頁遮罩，桌機仍是單欄", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const policyId = await loginWithPolicy(page, "overlay");
  // 讓建立事件的請求慢一點，才看得到送出中的畫面
  await page.route("**/api/v1/policies/*/incidents", async (route) => {
    await new Promise((r) => setTimeout(r, 1500));
    await route.continue();
  });
  await page.goto(`/incident/new/${policyId}`);
  const chat = page.getByTestId("messages");
  const box = await chat.boundingBox();
  expect(box && box.width > 700).toBeTruthy();
  await page.getByTestId("message").fill("班機延誤了六小時");
  await page.getByTestId("send").click();

  // 開場白後面立刻接旅客的話，再接 5 號輸入中的泡泡；沒有蓋住畫面的遮罩
  await expect(chat.locator("li").nth(1)).toHaveText("班機延誤了六小時");
  const busy = chat.getByTestId("busy");
  await expect(busy).toBeVisible();
  await expect(busy).toHaveAttribute("role", "status");
  await expect(busy).toContainText("正在確認狀況"); // 泡泡寫出正在做的事，不只是三個點
  await expect(page.locator(".fixed.inset-0")).toHaveCount(0);
  await expect(page.getByTestId("message")).not.toBeEditable();
  await page.screenshot({ path: "test-results/shots/desktop_2-2_loading.png" });
  await expect(page).toHaveURL(/\/incident\/[0-9a-f-]{36}$/, { timeout: SLOW });
});

test("2-2 建立事件失敗：旅客的話與輸入中泡泡被移除，顯示錯誤，輸入框保留原本的字", async ({
  page,
}) => {
  const policyId = await loginWithPolicy(page, "create-fail");
  await page.route("**/api/v1/policies/*/incidents", async (route) => {
    await new Promise((r) => setTimeout(r, 800));
    await route.fulfill({
      status: 500,
      contentType: "application/json",
      body: JSON.stringify({ detail: "伺服器暫時有問題" }),
    });
  });
  await page.goto(`/incident/new/${policyId}`);
  const chat = page.getByTestId("messages");
  await page.getByTestId("message").fill("班機延誤了六小時");
  await page.getByTestId("send").click();
  await expect(chat.locator("li").nth(1)).toHaveText("班機延誤了六小時");

  await expect(page.getByTestId("error")).toBeVisible();
  await expect(chat.locator("li")).toHaveCount(1); // 只剩開場白
  await expect(chat.getByTestId("busy")).toHaveCount(0);
  await expect(page.getByTestId("message")).toHaveValue("班機延誤了六小時");
  await expect(page.getByTestId("message")).toBeEnabled();
  await expect(page).toHaveURL(new RegExp(`/incident/new/${policyId}$`));
});

test("追問中送出：旅客的話先出現在對話裡，接著是 5 號輸入中的泡泡，畫面沒有被遮罩蓋住", async ({
  page,
}) => {
  const policyId = await loginWithPolicy(page, "typing");
  const createdRes = await page.request.post(`/api/v1/policies/${policyId}/incidents`, {
    data: { description: "班機延誤了" },
  });
  const created = await createdRes.json();
  if (!created.incident)
    throw new Error(`POST incidents 回 ${createdRes.status()}：${JSON.stringify(created)}`);

  // 讓回覆慢一點，才看得到送出中的畫面
  await page.route("**/api/v1/incidents/*/messages", async (route) => {
    if (route.request().method() === "POST") await new Promise((r) => setTimeout(r, 2000));
    await route.continue();
  });
  await page.goto(`/incident/${created.incident.incident_id}`);
  const chat = page.getByTestId("messages");
  await expect(page.getByTestId("message")).toBeVisible();
  const before = await chat.locator("li").count();

  await page.getByTestId("message").fill("我不確定，可能是昨天");
  await page.getByTestId("send").click();

  // 旅客剛送出的話立刻出現在最下面，後面接輸入中的泡泡（在對話區裡，不是全頁遮罩）
  await expect(chat.locator("li").nth(before)).toHaveText("我不確定，可能是昨天");
  const busy = chat.getByTestId("busy");
  await expect(busy).toBeVisible();
  await expect(busy).toHaveAttribute("role", "status");
  await expect(busy).toContainText("正在整理您的回答"); // 依頁面寫出正在做的事
  await expect(page.locator(".fixed.inset-0")).toHaveCount(0);
  await expect(page.getByTestId("message")).not.toBeEditable();

  // 回覆回來：泡泡消失，旅客的話只出現一次（換成真正的訊息）
  await expect(busy).toHaveCount(0, { timeout: SLOW });
  await expect(chat.getByText("我不確定，可能是昨天")).toHaveCount(1);
});

/** 建一個還在追問的事件（假 5 號：問兩輪才結束），回事件網址。 */
async function startClarifying(page: Page, name: string) {
  const policyId = await loginWithPolicy(page, name);
  const res = await page.request.post(`/api/v1/policies/${policyId}/incidents`, {
    data: { description: "班機延誤了" },
  });
  const created = await res.json();
  if (!created.incident)
    throw new Error(`POST incidents 回 ${res.status()}：${JSON.stringify(created)}`);
  return `/incident/${created.incident.incident_id}`;
}

test("追問中連續答兩題：只點一次輸入框，之後每題直接打字（英數字）也進得去，送出後輸入框仍有焦點", async ({
  page,
}) => {
  await page.goto(await startClarifying(page, "keep-focus"));
  const input = page.getByTestId("message");
  const chat = page.getByTestId("messages");
  await expect(chat.locator("li")).toHaveCount(3, { timeout: SLOW });

  // 旅客點一次輸入框，答第一題：打字、按 Enter
  await input.click();
  await page.keyboard.type("10:30");
  await page.keyboard.press("Enter");
  // 5 號回覆回來（新的一則訊息、輸入框可以打字）後，焦點還在輸入框
  await expect(chat.locator("li")).toHaveCount(5, { timeout: SLOW });
  await expect(input).toBeEditable();
  await expect(input).toBeFocused();

  // 第二題不再點輸入框：直接打英數字，字要進得去
  await page.keyboard.type("7/10");
  await expect(input).toHaveValue("7/10");
  await page.keyboard.press("Enter");
  await expect(chat.locator("li").nth(5)).toHaveText("7/10");
});

test("追問中等回覆期間：輸入框不能打字，再按 Enter 不會送出第二則訊息", async ({ page }) => {
  await page.goto(await startClarifying(page, "no-double-send"));
  const input = page.getByTestId("message");
  const posts: string[] = [];
  page.on("request", (r) => {
    if (r.method() === "POST" && r.url().endsWith("/messages")) posts.push(r.url());
  });
  // 讓回覆慢一點，才有「等回覆」的空檔
  await page.route("**/api/v1/incidents/*/messages", async (route) => {
    if (route.request().method() === "POST") await new Promise((r) => setTimeout(r, 2000));
    await route.continue();
  });
  await expect(page.getByTestId("messages").locator("li")).toHaveCount(3, { timeout: SLOW });

  await input.click();
  await page.keyboard.type("昨天");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("busy")).toBeVisible();
  await expect(input).not.toBeEditable();
  // 等回覆期間再按 Enter、再打字：不送出第二則，也不改輸入框的內容
  await page.keyboard.press("Enter");
  await page.keyboard.type("x");
  await page.keyboard.press("Enter");
  await expect(page.getByTestId("busy")).toHaveCount(0, { timeout: SLOW });
  expect(posts).toHaveLength(1);
});

// 從建立事故頁送出描述後進到追問頁：輸入框自動對焦，旅客可以直接答第一題（#441）。
// 只有「那一次換頁」（路由 state 帶 focusAsk）、而且是桌機（pointer: fine）才對焦。
async function describeFromNewPage(page: Page, policyId: string, text: string, tap = false) {
  await page.goto(`/incident/new/${policyId}`);
  if (tap) await page.getByTestId("message").tap();
  else await page.getByTestId("message").click();
  await page.keyboard.type(text);
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/incident\/[0-9a-f-]{36}$/, { timeout: SLOW });
}

test("送出描述後進到追問頁：不點輸入框，直接打英數字就是第一題的答案", async ({ page }) => {
  const policyId = await loginWithPolicy(page, "autofocus");
  await describeFromNewPage(page, policyId, "班機延誤了六小時");
  const input = page.getByTestId("message");
  const chat = page.getByTestId("messages");
  // 第一題（附快速選項）出現 = 已經換到追問頁（建立事件頁的輸入中泡泡也是一個 li，不能只數 li）
  await expect(page.getByTestId("quick-replies")).toBeVisible({ timeout: SLOW });
  await expect(input).toBeFocused();

  await page.keyboard.type("7/10");
  await expect(input).toHaveValue("7/10");
  await page.keyboard.press("Enter");
  await expect(chat.locator("li").nth(3)).toHaveText("7/10");
});

test("沒有 focusAsk（直接打開追問頁）：不會自動對焦", async ({ page }) => {
  await page.goto(await startClarifying(page, "no-autofocus"));
  await expect(page.getByTestId("messages").locator("li")).toHaveCount(3, { timeout: SLOW });
  await expect(page.getByTestId("message")).toBeVisible();
  await expect(page.getByTestId("message")).not.toBeFocused();
});

test.describe("手機（觸控）", () => {
  test.use({ hasTouch: true, isMobile: true, viewport: { width: 390, height: 844 } });

  test("送出描述後進到追問頁：不自動對焦，不會彈出鍵盤", async ({ page }) => {
    const policyId = await loginWithPolicy(page, "autofocus-touch");
    await describeFromNewPage(page, policyId, "班機延誤了六小時", true);
    await expect(page.getByTestId("quick-replies")).toBeVisible({ timeout: SLOW });
    await expect(page.getByTestId("message")).toBeVisible();
    await expect(page.getByTestId("message")).not.toBeFocused();
  });
});

test("送出描述後 5 號一輪就問完（直接跳確認彈窗）：不出錯，輸入框不搶彈窗的焦點", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const policyId = await loginWithPolicy(page, "autofocus-dialog");
  // 先用 API 備一個已經問完、等確認的事件，讓「建立事件」的回應直接指到它
  const create = async () =>
    page.request.post(`/api/v1/policies/${policyId}/incidents`, {
      data: { description: "班機延誤了六小時", require_confirmation: true },
    });
  const first = await create();
  const created = await first.json();
  const id: string = created.incident.incident_id;
  for (let i = 0; i < 15; i++) {
    const res = await page.request.post(`/api/v1/incidents/${id}/messages`, {
      data: { content: "班機延誤了六小時" },
    });
    if ((await res.json()).reply.finished) break;
  }
  await page.route("**/api/v1/policies/*/incidents", async (route) => {
    if (route.request().method() === "POST") await route.fulfill({ json: created });
    else await route.continue();
  });

  await describeFromNewPage(page, policyId, "班機延誤了六小時");
  const dialog = page.getByTestId("confirm-dialog");
  await expect(dialog).toBeVisible({ timeout: SLOW });
  await expect(page.getByTestId("message")).not.toBeFocused();
  expect(errors).toEqual([]);
});
