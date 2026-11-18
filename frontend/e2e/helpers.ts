import { expect, type Page, test } from "@playwright/test";

/**
 * 用測試登入（任何 email 都能登入，需要後端開 DEV_LOGIN_ENABLED）。
 * 登入後輪詢 `/auth/me` 直到回 200 才回來：只等網址變成首頁，並行時登入狀態偶爾還沒生效，
 * 接著用 `page.request` 打 API 就會 401「未登入」。
 */
export async function login(page: Page, email: string) {
  await page.goto("/login?next=%2F");
  await page.getByLabel("Email").fill(email);
  await page.getByRole("button", { name: "測試登入" }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect
    .poll(async () => (await page.request.get("/api/v1/auth/me")).status(), { timeout: 10_000 })
    .toBe(200);
}

/** 後台的測試用管理者：後端的 `ADMIN_EMAILS` 要包含它（.env.example 已有）。 */
export const ADMIN_EMAIL = "e2e-admin@example.com";

/** 已用測試登入之後呼叫：目前登入的人不是管理者就略過（通常是本機 .env 的 ADMIN_EMAILS 沒有 ADMIN_EMAIL）。 */
export async function skipUnlessAdmin(page: Page) {
  const me = await (await page.request.get("/api/v1/auth/me")).json();
  test.skip(!me.is_admin, `後端的 ADMIN_EMAILS 要包含 ${ADMIN_EMAIL}（見 .env.example）`);
}

/** 設計稿功能二的頁面底色是米白（--color-paper）：標了 `data-page-paper` 的頁面，body 換成這個底色。 */
export async function expectPaper(page: Page) {
  await expect(page.locator("body")).toHaveCSS("background-color", "rgb(251, 250, 246)");
}

/** 登入、建一張保單與一個延誤事故，並和 5 號聊到待辦產生；回事故 id（假 6 號依檔名辨識，需要 make seed）。 */
export async function setupClaimIncident(page: Page, name: string) {
  await login(page, `e2e-docs-${name}-${Date.now()}@example.com`);
  const api = page.request;
  const options = await (await api.get("/api/v1/plans/fubon_advanced/sum-insured-options")).json();
  const policyRes = await api.post("/api/v1/policies/manual", {
    data: {
      plan_id: "fubon_advanced",
      sum_insured: options.sum_insured[0],
      start_date: "2026-07-05",
      end_date: "2026-07-10",
    },
  });
  // 偶發失敗時要看得到狀態碼與內容，不是後面才冒出 policy_id 是 undefined
  expect(
    policyRes.ok(),
    `POST policies/manual 回 ${policyRes.status()}：${await policyRes.text()}`,
  ).toBeTruthy();
  const policy = await policyRes.json();
  await api.patch(`/api/v1/policies/${policy.id}`, { data: { destination: "日本" } });
  const createdRes = await api.post(`/api/v1/policies/${policy.id}/incidents`, {
    data: { description: "班機延誤了六小時" },
  });
  const created = await createdRes.json();
  // 準備資料時伺服器回了錯誤：把狀態碼與內容丟出來，才看得到真正原因
  if (!created.incident)
    throw new Error(`POST incidents 回 ${createdRes.status()}：${JSON.stringify(created)}`);
  const id: string = created.incident.incident_id;
  for (let i = 0; i < 15; i++) {
    const res = await api.post(`/api/v1/incidents/${id}/messages`, {
      data: { content: "不知道" },
    });
    const reply = await res.json();
    // 準備資料時伺服器回了錯誤：直接把狀態碼與內容丟出來，才看得到真正原因
    if (!reply.reply) throw new Error(`POST messages 回 ${res.status()}：${JSON.stringify(reply)}`);
    if (reply.reply.finished) break;
  }
  return id;
}

/**
 * 上傳保單並略過保單解析的逐步動畫（#444）：回應回來後步驟要播約 2.7 秒才填表，
 * 不是在測動畫的測試用這個，免得撞到 expect 的 5 秒逾時。
 */
export async function uploadPolicyPdf(page: Page, file: string) {
  await page.getByTestId("policy-file").setInputFiles(file);
  await page.getByTestId("policy-skip").click();
}

type TripTransition = { overlay: boolean; cardCounts: number[] };

/**
 * 開始記錄換到行程頁的過場（#445）：有沒有出現「正在整理您的行程」載入遮罩，以及事故卡出現過哪些張數
 * （只有舊行程的一張卡閃一下，就會多記到 1）。SPA 換頁不重新載入，記錄會一直留著；要在換頁前呼叫。
 */
export async function watchTripTransition(page: Page) {
  await page.evaluate(() => {
    const seen: TripTransition = { overlay: false, cardCounts: [] };
    (window as Window & { __trip?: TripTransition }).__trip = seen;
    const check = () => {
      const status = document.querySelector('[role="status"]');
      if (status?.textContent?.includes("正在整理您的行程")) seen.overlay = true;
      const n = document.querySelectorAll('[data-testid="trip-incident"]').length;
      if (n > 0 && !seen.cardCounts.includes(n)) seen.cardCounts.push(n);
    };
    new MutationObserver(check).observe(document.body, { childList: true, subtree: true });
  });
}

/** `watchTripTransition` 到目前為止記到的過場。 */
export async function tripTransition(page: Page): Promise<TripTransition> {
  return page.evaluate(
    () =>
      (window as Window & { __trip?: TripTransition }).__trip ?? { overlay: false, cardCounts: [] },
  );
}

/**
 * 記錄頁面上呼叫過 `scrollIntoView` 的元素（#449）：用元素的 `data-testid` 當名字（事故卡是 `trip-incident`、
 * 理賠待辦是 `todos-section`）。頁面高度不夠時實際捲不動，只看呼叫才準；要在換頁（`goto`）前呼叫。
 */
export async function spyScrollIntoView(page: Page) {
  await page.addInitScript(() => {
    const w = window as Window & { __scrolled?: string[] };
    w.__scrolled = [];
    const original = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function (this: Element, ...args: []) {
      w.__scrolled?.push(this.getAttribute("data-testid") ?? this.tagName);
      return original.apply(this, args);
    };
  });
}

/** `spyScrollIntoView` 到目前為止記到的元素名稱。 */
export async function scrolledTo(page: Page): Promise<string[]> {
  return page.evaluate(() => (window as Window & { __scrolled?: string[] }).__scrolled ?? []);
}

/** 1-1 還沒有任何活動就按「下一步」：會先跳出提醒，按「確定」跳過安排行程。 */
export async function nextSkippingItinerary(page: Page) {
  await page.getByRole("button", { name: "下一步" }).click();
  await page.getByTestId("skip-confirm").click();
}
