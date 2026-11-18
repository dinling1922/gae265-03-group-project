import { expect, type Page, test } from "@playwright/test";
import {
  login,
  scrolledTo,
  spyScrollIntoView,
  tripTransition,
  watchTripTransition,
} from "./helpers";

// 行程頁的入口與導向（#360）：問完事故進行程頁並選中這個事故；`?incident=` 預設選中並展開；
// Demo 流程「建立行程 → 事故 A → 新狀況 → 事故 B → 行程頁看兩個事故與共用待辦」。需要 make seed。
const SLOW = 15_000;

/** 用 API 建一個事故並聊到 5 號結束（產生類型、結論與待辦）。 */
async function newIncident(page: Page, policyId: string, description: string) {
  const api = page.request;
  const createdRes = await api.post(`/api/v1/policies/${policyId}/incidents`, {
    data: { description },
  });
  const created = await createdRes.json();
  if (!created.incident)
    throw new Error(`POST incidents 回 ${createdRes.status()}：${JSON.stringify(created)}`);
  const id: string = created.incident.incident_id;
  for (let i = 0; i < 15; i++) {
    const res = await api.post(`/api/v1/incidents/${id}/messages`, {
      data: { content: description },
    });
    const reply = await res.json();
    if (!reply.reply) throw new Error(`POST messages 回 ${res.status()}：${JSON.stringify(reply)}`);
    if (reply.reply.finished) break;
  }
  return id;
}

async function newPolicy(page: Page, name: string) {
  await login(page, `e2e-trip-entry-${name}-${Date.now()}@example.com`);
  const api = page.request;
  const sums = await (await api.get("/api/v1/plans/fubon_advanced/sum-insured-options")).json();
  const policy = await (
    await api.post("/api/v1/policies/manual", {
      data: {
        plan_id: "fubon_advanced",
        sum_insured: sums.sum_insured[0],
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

test("?incident= 預設選中並展開那個事故；不在這個行程裡就忽略", async ({ page }) => {
  const policyId = await newPolicy(page, "param");
  await newIncident(page, policyId, "班機延誤了六小時");
  const second = await newIncident(page, policyId, "班機取消了");

  await page.goto(`/trip/${policyId}?incident=${second}`);
  const cards = page.getByTestId("trip-incident");
  await expect(cards).toHaveCount(2);
  await expect(cards.nth(1)).toHaveAttribute("data-selected", "true");
  await expect(cards.nth(1).getByTestId("incident-ask")).toHaveCount(0); // 選中的沒有「問答」按鈕
  await expect(cards.nth(0).getByTestId("incident-ask")).toBeVisible();

  await page.goto(`/trip/${policyId}?incident=00000000-0000-0000-0000-000000000000`);
  await expect(page.getByTestId("trip-incident").nth(0)).toHaveAttribute("data-selected", "true");
});

/**
 * Demo 流程的前半（#449）：事故 A 已完成，到行程頁按「＋有新狀況」描述事故 B，問到 5 號結束；
 * 這時要跳 2-2g 確認彈窗，還不會進行程頁。回事故 B 的 id。
 */
async function describeSecondIncident(page: Page, policyId: string) {
  await page.goto(`/trip/${policyId}`);
  // 「＋有新狀況」開新的對話，描述事故 B
  await page.getByTestId("new-incident").click();
  await expect(page).toHaveURL(new RegExp(`/incident/new/${policyId}$`));
  // 等描述事故的表單出現：切換頁面時行程頁的問答輸入框還在，會填到舊的輸入框
  await expect(page.getByTestId("start-form")).toBeVisible();
  const first = page.getByTestId("start-form").getByTestId("message");
  await first.fill("班機取消了");
  await expect(first).toHaveValue("班機取消了");
  await expect(page.getByTestId("send")).toBeEnabled();
  await page.getByTestId("send").click();
  await expect(page).toHaveURL(/\/incident\/[0-9a-f-]{36}$/, { timeout: SLOW });
  const incidentB = page.url().split("/").pop() ?? "";
  const reply = () =>
    page.waitForResponse((r) => r.url().endsWith("/messages") && r.request().method() === "POST", {
      timeout: SLOW,
    });
  let replied = reply();
  await page.getByTestId("message").fill("班機取消了");
  await page.getByTestId("send").click();
  await replied;
  replied = reply();
  await page.getByTestId("message").fill("昨天在東京成田機場");
  await page.getByTestId("send").click();
  await replied;
  // 新狀況也要確認（#449）：問完先跳 2-2g，還在追問頁，行程裡還沒有 B
  await expect(page.getByTestId("confirm-dialog")).toBeVisible({ timeout: SLOW });
  await expect(page).toHaveURL(new RegExp(`/incident/${incidentB}$`));
  return incidentB;
}

test("Demo 流程：事故 A → 有新狀況 → 事故 B，問完跳確認，按確認進行程頁、選中 B 並捲到理賠待辦，共同待辦只有一組", async ({
  page,
}) => {
  const policyId = await newPolicy(page, "demo");
  await newIncident(page, policyId, "班機延誤了六小時");
  await spyScrollIntoView(page);
  const incidentB = await describeSecondIncident(page, policyId);

  // 從這裡開始記換頁過場（#445）：按確認後要有載入遮罩，行程頁不能先出現只有舊行程的一張卡
  await watchTripTransition(page);
  await page.getByTestId("confirm-yes").click();

  // 停在行程頁：兩個事故同一頁，預設選中的是剛確認的 B
  await expect(page).toHaveURL(new RegExp(`/trip/${policyId}\\?incident=${incidentB}$`), {
    timeout: SLOW,
  });
  const cards = page.getByTestId("trip-incident");
  await expect(cards).toHaveCount(2);
  await expect(page.locator(`[data-incident-id="${incidentB}"]`)).toHaveAttribute(
    "data-selected",
    "true",
  );
  await expect(page.getByTestId("qa-title")).toContainText("班機");
  const seen = await tripTransition(page);
  expect(seen.overlay, "換頁時要有「正在整理您的行程」遮罩").toBe(true);
  expect(seen.cardCounts, "行程頁出現過的事故卡張數：不能先閃只有 A 的一張").toEqual([2]);
  // B 有待辦：捲到理賠待辦，不是捲到事故卡（#449）
  await expect(page.getByTestId("todos-section")).toBeInViewport();
  const scrolled = await scrolledTo(page);
  expect(scrolled).toContain("todos-section");
  expect(scrolled).not.toContain("trip-incident");
  // 共用待辦各只出現一次
  await expect(page.getByTestId("todos").getByText("填寫理賠申請書")).toHaveCount(1);
});

// 5 號的假實作產不出「資訊不足」，所以改行程 API 的回應：B 沒有待辦（只改回應，不動資料）
test("新狀況的事故資訊不足（沒有待辦）：按確認後停在事故卡，不捲到理賠待辦", async ({ page }) => {
  const policyId = await newPolicy(page, "insufficient");
  await newIncident(page, policyId, "班機延誤了六小時");
  await spyScrollIntoView(page);
  const incidentB = await describeSecondIncident(page, policyId);
  await page.route(`**/api/v1/policies/${policyId}/trip`, async (route) => {
    try {
      const response = await route.fetch();
      const body = await response.json();
      for (const incident of body.incidents) {
        if (incident.id !== incidentB) continue;
        incident.covered = false;
        incident.todos = [];
        incident.clauses = incident.clauses.map((c: object) => ({
          ...c,
          conclusion: "insufficient",
        }));
      }
      await route.fulfill({ response, json: body });
    } catch {
      // 測試結束時還在進行的重新讀取：不算錯
    }
  });

  await page.getByTestId("confirm-yes").click();

  await expect(page).toHaveURL(new RegExp(`/trip/${policyId}\\?incident=${incidentB}$`), {
    timeout: SLOW,
  });
  await expect(page.locator(`[data-incident-id="${incidentB}"]`)).toHaveAttribute(
    "data-selected",
    "true",
  );
  await expect(
    page.locator(`[data-incident-id="${incidentB}"]`).getByTestId("insufficient"),
  ).toBeVisible();
  // 沒有待辦：不捲到理賠待辦（維持原本，B 不是第一個時只捲到它的事故卡）
  expect(await scrolledTo(page)).not.toContain("todos-section");
});

test("舊的理賠頁有「看整個行程」連結，連到行程頁並選中這個事故", async ({ page }) => {
  const policyId = await newPolicy(page, "legacy");
  const incidentId = await newIncident(page, policyId, "班機延誤了六小時");

  await page.goto(`/incident/${incidentId}`);
  await page.getByTestId("view-trip").click();

  await expect(page).toHaveURL(new RegExp(`/trip/${policyId}\\?incident=${incidentId}$`));
  await expect(page.getByTestId("trip-incident").first()).toHaveAttribute("data-selected", "true");
});
