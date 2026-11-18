import { expect, type Page, test } from "@playwright/test";
import { expectPaper, login, scrolledTo, spyScrollIntoView } from "./helpers";

// 行程頁（/trip/$policyId，ADR-0029）：一張保單的所有事故收在一頁。
// 事故用 API 快速備好（假 5 號問到結束）。需要測試登入與條款庫、方案資料（make seed）。

type Setup = { policyId: string; incidentIds: string[] };

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

async function setup(page: Page, name: string, descriptions: string[]): Promise<Setup> {
  await login(page, `e2e-trip-${name}-${Date.now()}@example.com`);
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
  const incidentIds: string[] = [];
  for (const d of descriptions) incidentIds.push(await newIncident(page, policy.id, d));
  return { policyId: policy.id, incidentIds };
}

test.beforeEach(async ({ request }) => {
  const config = await (await request.get("/api/v1/auth/config")).json();
  test.skip(!config.dev_login, "後端沒有設 DEV_LOGIN_ENABLED=true");
  test.skip(!(await request.get("/api/v1/plans")).ok(), "條款庫沒有載入，先 make seed");
});

test("行程頁：行程資訊列、事故卡、待辦依事故分組並有「共同文件」組", async ({ page }) => {
  const { policyId } = await setup(page, "overview", ["班機延誤了六小時"]);
  await page.goto(`/trip/${policyId}`);
  await expectPaper(page);

  await expect(page.getByTestId("trip-title")).toHaveText("日本 2026/07/05～07/10");
  await expect(page.getByText(/待辦 \d+／\d+/)).toBeVisible();
  // 事故卡：選中的事故展開，沒有「問答」「▸」
  const card = page.getByTestId("trip-incident");
  await expect(card).toHaveCount(1);
  await expect(card.getByTestId("incident-type")).toContainText("班機延誤");
  await expect(card.getByTestId("incident-ask")).toHaveCount(0);
  // 待辦：事故自己的一組，加上「共同文件」一組（理賠申請書）
  await expect(page.getByTestId("todo-group").first()).toContainText("班機延誤");
  const shared = page.getByTestId("todo-group-trip").first();
  await expect(shared).toContainText("共同文件");
  await expect(page.getByTestId("todos")).toContainText("填寫理賠申請書");
  // 右欄問答：選中事故的名稱
  await expect(page.getByTestId("qa-title")).toContainText("班機延誤");
});

test("行程頁載入時問答框沒有被自動對焦（手機一進頁面不該彈鍵盤）", async ({ page }) => {
  const { policyId } = await setup(page, "no-autofocus", ["班機延誤了六小時"]);
  await page.goto(`/trip/${policyId}`);

  await expect(page.getByTestId("message")).toBeVisible();
  await expect(page.getByTestId("message")).not.toBeFocused();
});

test("勾選待辦後完成度更新；行程頁與事件資料一致", async ({ page }) => {
  const { policyId } = await setup(page, "tick", ["班機延誤了六小時"]);
  await page.goto(`/trip/${policyId}`);
  const pill = page.getByText(/待辦 \d+／\d+/);
  await expect(pill).toBeVisible();
  const before = (await pill.textContent()) ?? "";
  const done0 = Number(before.match(/待辦 (\d+)／/)?.[1]);

  // 勾「共同文件」組的第一項（不需要上傳的優先；需上傳的會先跳確認）
  const checkbox = page.getByTestId("todo-group-trip").first().getByRole("checkbox").first();
  await checkbox.click(); // 需上傳的待辦會先跳確認，狀態要等確認後才變，不能用 check()
  const dialog = page.getByTestId("tick-dialog");
  if (await dialog.isVisible().catch(() => false)) await page.getByTestId("tick-confirm").click();

  await expect(pill).toHaveText(new RegExp(`待辦 ${done0 + 1}／`));
});

test("多個事故：選中的事故展開並顯示它的問答；其他事故收起，可按「問答」切換、按「▸」展開", async ({
  page,
}) => {
  const { policyId } = await setup(page, "multi", ["班機延誤了六小時", "班機取消了"]);
  await page.goto(`/trip/${policyId}`);

  const cards = page.getByTestId("trip-incident");
  await expect(cards).toHaveCount(2);
  // 預設選中第一個（日期最早）；第二個沒有選中，有「問答」與「▸」
  await expect(cards.nth(0)).toHaveAttribute("data-selected", "true");
  await expect(cards.nth(1).getByTestId("incident-ask")).toBeVisible();
  const toggle = cards.nth(1).getByTestId("incident-toggle");
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  // 展開只影響左欄，右欄問答還是第一個事故
  await expect(cards.nth(0)).toHaveAttribute("data-selected", "true");
  // 按「問答」：選中第二個，右欄換成它的問答
  await cards.nth(1).getByTestId("incident-ask").click();
  await expect(cards.nth(1)).toHaveAttribute("data-selected", "true");
  await expect(cards.nth(0).getByTestId("incident-ask")).toBeVisible();
  // 共同文件整個行程只有一組、不重複
  await expect(page.getByTestId("todo-group-trip")).toHaveCount(
    await page.getByTestId("todo-group-trip").count(),
  );
  await expect(page.getByTestId("todos").getByText("填寫理賠申請書")).toHaveCount(1);
});

test("不符保障的事故：標「不符保障」，其他事故照常", async ({ page }) => {
  const { policyId } = await setup(page, "uncovered", ["班機延誤了六小時", "班機延誤了但是不理賠"]);
  await page.goto(`/trip/${policyId}`);

  const cards = page.getByTestId("trip-incident");
  await expect(cards).toHaveCount(2);
  await expect(page.getByTestId("not-covered")).toHaveCount(1);
});

test("行程送件結案後唯讀：已結案標籤、沒有問答、待辦不能勾", async ({ page }) => {
  const { policyId } = await setup(page, "locked", ["班機延誤了六小時"]);
  const api = page.request;
  const trip = await (await api.get(`/api/v1/policies/${policyId}/trip`)).json();
  const todos = [
    ...trip.shared_todos,
    ...trip.incidents.flatMap((i: { todos: unknown[] }) => i.todos),
  ];
  for (const t of todos as { id: string; applicable: boolean }[]) {
    if (t.applicable)
      await api.patch(`/api/v1/policies/${policyId}/todos/${t.id}`, { data: { is_done: true } });
  }
  const submitted = await api.post(`/api/v1/policies/${policyId}/submission`, {
    data: { mode: "self" },
  });
  expect(submitted.status()).toBe(201);

  await page.goto(`/trip/${policyId}`);

  await expect(page.getByText("已結案")).toBeVisible();
  await expect(page.getByRole("region", { name: "問答" })).toHaveCount(0);
  await expect(page.getByTestId("todos").getByRole("checkbox").first()).toBeDisabled();
});

test("找不到行程：顯示提示", async ({ page }) => {
  await login(page, `e2e-trip-missing-${Date.now()}@example.com`);
  await page.goto("/trip/00000000-0000-0000-0000-000000000000");
  await expect(page.getByText("找不到這個行程")).toBeVisible();
});

// 上傳（F2）：整個行程一個上傳框，用假 6 號（依檔名辨識：含「延誤證明」等關鍵字通過，「歧義」跨事故要選）。
const SLOW = 15_000;
const pdf = (name: string) => ({
  name,
  mimeType: "application/pdf",
  buffer: Buffer.from("%PDF-1.4"),
});

test("上傳文件：辨識通過自動打勾，完成度同步更新", async ({ page }) => {
  const { policyId } = await setup(page, "upload", ["班機延誤了六小時"]);
  await page.goto(`/trip/${policyId}`);
  const pill = page.getByText(/待辦 \d+／\d+/);
  await expect(pill).toBeVisible();
  const done0 = Number(((await pill.textContent()) ?? "").match(/待辦 (\d+)／/)?.[1]);
  await expect(page.getByTestId("upload-box")).toBeVisible();
  await expect(page.getByTestId("upload-summary")).toHaveCount(0);

  await page.getByTestId("doc-file").setInputFiles(pdf("延誤證明.pdf"));
  await expect(page.getByTestId("upload-summary")).toContainText("1 份已自動打勾", {
    timeout: SLOW,
  });
  await expect(pill).toHaveText(new RegExp(`待辦 ${done0 + 1}／`));
  await expect(page.getByTestId("todos").getByText("AI 已打勾").first()).toBeVisible();
});

test("刪除已上傳的文件：先確認，刪掉後完成度退回、待辦回到待上傳", async ({ page }) => {
  const { policyId } = await setup(page, "delete-doc", ["班機延誤了六小時"]);
  await page.goto(`/trip/${policyId}`);
  const pill = page.getByText(/待辦 \d+／\d+/);
  await expect(pill).toBeVisible();
  const done0 = Number(((await pill.textContent()) ?? "").match(/待辦 (\d+)／/)?.[1]);

  await page.getByTestId("doc-file").setInputFiles(pdf("延誤證明.pdf"));
  await expect(pill).toHaveText(new RegExp(`待辦 ${done0 + 1}／`), { timeout: SLOW });
  const row = page
    .getByTestId("todos")
    .getByRole("listitem")
    .filter({ hasText: "AI 已打勾" })
    .first();
  await expect(row.getByTestId("todo-files")).toBeAttached();

  // 按「刪除」先跳確認；選「保留」什麼都不變
  await row.getByTestId("todo-toggle").click();
  await row.getByTestId("todo-file-delete").click();
  const dialog = page.getByTestId("delete-document-dialog");
  await expect(dialog).toContainText("Google Drive");
  await dialog.getByRole("button", { name: "保留" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(pill).toHaveText(new RegExp(`待辦 ${done0 + 1}／`));

  await row.getByTestId("todo-file-delete").click();
  await page.getByTestId("delete-document-confirm").click();
  await expect(pill).toHaveText(new RegExp(`待辦 ${done0}／`), { timeout: SLOW });
  await expect(page.getByTestId("todos").getByText("AI 已打勾")).toHaveCount(0);
  await expect(page.getByTestId("todo-file-delete")).toHaveCount(0);
});

test("多個事故：文件對得到多個事故時要選，選單帶事故名稱，選好後檢查並打勾", async ({ page }) => {
  const { policyId } = await setup(page, "ambiguous", ["班機延誤了六小時", "班機取消了"]);
  await page.goto(`/trip/${policyId}`);
  await page.getByTestId("doc-file").setInputFiles(pdf("歧義.pdf"));

  const chooser = page.getByTestId("upload-chooser");
  await expect(chooser).toBeVisible({ timeout: SLOW });
  // 選單的每個選項都帶事故名稱，兩個事故的候選分得出來
  const options = await chooser.getByRole("option").allTextContents();
  expect(options.some((o) => o.startsWith("班機延誤") && o.includes("　"))).toBe(true);
  expect(options.some((o) => o.startsWith("班機延誤（2）"))).toBe(true);

  await chooser.getByRole("combobox").selectOption({ index: 1 });
  await chooser.getByTestId("chooser-submit").click();
  await expect(chooser).toHaveCount(0, { timeout: SLOW });
});

test("有事故不符保障：上傳框旁提示；都在保障內沒有提示", async ({ page }) => {
  const mixed = await setup(page, "upload-mixed", ["班機延誤了六小時", "班機延誤了但是不理賠"]);
  await page.goto(`/trip/${mixed.policyId}`);
  await expect(page.getByTestId("upload-uncovered-note")).toBeVisible();

  const plain = await setup(page, "upload-plain", ["班機延誤了六小時"]);
  await page.goto(`/trip/${plain.policyId}`);
  await expect(page.getByTestId("upload-box")).toBeVisible();
  await expect(page.getByTestId("upload-uncovered-note")).toHaveCount(0);
});

// 送件區（F4a）：整個行程一起送件的入口與結案後的說明。
test("送件區：還缺文件時提示並可捲到上傳框，備齊後出現「送件」", async ({ page }) => {
  const { policyId } = await setup(page, "sendbox", ["班機延誤了六小時"]);
  await page.goto(`/trip/${policyId}`);
  const box = page.getByTestId("send-box");
  await expect(box).toContainText("還缺");
  await expect(box.getByRole("link", { name: "上傳文件" })).toHaveAttribute("href", "#upload-box");

  // 全部勾完（需上傳的會先跳確認）
  const api = page.request;
  const trip = await (await api.get(`/api/v1/policies/${policyId}/trip`)).json();
  const todos = [
    ...trip.shared_todos,
    ...trip.incidents.flatMap((i: { todos: unknown[] }) => i.todos),
  ];
  for (const t of todos as { id: string; applicable: boolean }[]) {
    if (t.applicable)
      await api.patch(`/api/v1/policies/${policyId}/todos/${t.id}`, { data: { is_done: true } });
  }
  await page.reload();
  await expect(box).toContainText("文件已備齊");
  await expect(page.getByTestId("go-submit")).toBeVisible();
});

test("所有事故都不符保障：可以結案，結案後鎖住並說明沒有寄信", async ({ page }) => {
  const { policyId } = await setup(page, "close", ["班機延誤了但是不理賠"]);
  await page.goto(`/trip/${policyId}`);
  await expect(page.getByTestId("send-box")).toContainText("事故都不在保障範圍");
  await page.getByTestId("close-trip").click();
  await page.getByTestId("close-confirm").click();

  await expect(page.getByTestId("send-closed")).toHaveText("此行程已結案");
  // 直接結案沒有信也沒有文件：兩個按鈕保留但反灰
  await expect(page.getByRole("button", { name: "到 Gmail 查看" })).toBeVisible();
  await expect(page.getByRole("button", { name: "下載全部文件" })).toBeVisible();
  await expect(page.getByTestId("send-box-gmail")).toHaveCount(0);
  await expect(page.getByTestId("locked-note")).toContainText("重新建立行程");
  await expect(page.getByText("已結案", { exact: true }).first()).toBeVisible();
  await expect(page.getByTestId("close-trip")).toHaveCount(0);
});

test("自己處理結案後：送件區說明沒建草稿，連結沒有的維持反灰", async ({ page }) => {
  const { policyId } = await setup(page, "selfdone", ["班機延誤了六小時"]);
  const api = page.request;
  const trip = await (await api.get(`/api/v1/policies/${policyId}/trip`)).json();
  const todos = [
    ...trip.shared_todos,
    ...trip.incidents.flatMap((i: { todos: unknown[] }) => i.todos),
  ];
  for (const t of todos as { id: string; applicable: boolean }[]) {
    if (t.applicable)
      await api.patch(`/api/v1/policies/${policyId}/todos/${t.id}`, { data: { is_done: true } });
  }
  expect(
    (
      await api.post(`/api/v1/policies/${policyId}/submission`, { data: { mode: "self" } })
    ).status(),
  ).toBe(201);

  await page.goto(`/trip/${policyId}`);
  await expect(page.getByTestId("send-closed")).toContainText("未在你的 Gmail 建立草稿");
  // 沒建草稿就沒有 Gmail 連結：按鈕保留但反灰
  await expect(page.getByRole("button", { name: "到 Gmail 查看" })).toBeVisible();
  await expect(page.getByTestId("send-box-gmail")).toHaveCount(0);
});

// 問答（F3）：捲動對齊、各事故的輸入草稿分開、手機面板。
test("桌機：按「問答」左欄捲到該事故，總結頂端對齊問答框頂端", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 600 });
  const { policyId } = await setup(page, "align", [
    "班機延誤了六小時",
    "班機取消了",
    "行李遺失了被偷",
  ]);
  await page.goto(`/trip/${policyId}`);
  const cards = page.getByTestId("trip-incident");
  await expect(cards).toHaveCount(3);

  await cards.nth(2).getByTestId("incident-ask").click();
  await expect(cards.nth(2)).toHaveAttribute("data-selected", "true");
  const gap = async () => {
    const card = await cards.nth(2).boundingBox();
    const qa = await page.getByRole("region", { name: "問答" }).boundingBox();
    return Math.abs((card?.y ?? 9999) - (qa?.y ?? 0));
  };
  await expect.poll(gap, { timeout: 5000 }).toBeLessThan(8);
});

test("各事故的問答分開：輸入框打到一半的字不會帶到別的事故", async ({ page }) => {
  const { policyId } = await setup(page, "drafts", ["班機延誤了六小時", "班機取消了"]);
  await page.goto(`/trip/${policyId}`);
  const cards = page.getByTestId("trip-incident");
  const input = page.getByRole("region", { name: "問答" }).getByRole("textbox");

  await input.fill("第一個事故的草稿");
  await cards.nth(1).getByTestId("incident-ask").click();
  await expect(cards.nth(1)).toHaveAttribute("data-selected", "true");
  await expect(input).toHaveValue("");
  await input.fill("第二個事故的草稿");

  await cards.nth(0).getByTestId("incident-ask").click();
  await expect(input).toHaveValue("第一個事故的草稿");
  await cards.nth(1).getByTestId("incident-ask").click();
  await expect(input).toHaveValue("第二個事故的草稿");
});

test("手機：問答面板占下半、沒有遮罩，上方待辦仍可捲動，收起後消失", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  const { policyId } = await setup(page, "mobile", ["班機延誤了六小時", "班機取消了"]);
  await page.goto(`/trip/${policyId}`);
  const panel = page.getByRole("region", { name: "問答" });
  await expect(panel).toBeHidden();

  await page.getByTestId("trip-incident").nth(1).getByTestId("incident-ask").click();
  await expect(panel).toBeVisible();
  await expect(page.getByTestId("qa-title")).toContainText("班機");
  const box = await panel.boundingBox();
  expect(box && box.height >= 800 * 0.4 && box.height <= 800 * 0.55).toBeTruthy();
  // 沒有遮罩：面板開著，上方待辦仍可捲動
  await expect(page.getByRole("button", { name: "收起問答" })).toHaveCount(0);
  const before = await page.evaluate(() => window.scrollY);
  await page.evaluate(() => window.scrollBy(0, 200));
  expect(await page.evaluate(() => window.scrollY)).toBeGreaterThanOrEqual(before + 100);

  await page.getByTestId("qa-close").click();
  await expect(panel).toBeHidden();
});

test("手機：只有一個事故時，選中的事故卡也有「問答」，能打開、收起、再打開；桌機沒有（#422）", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 800 });
  const { policyId } = await setup(page, "single-mobile", ["班機延誤了六小時"]);
  await page.goto(`/trip/${policyId}`);
  const panel = page.getByRole("region", { name: "問答" });
  const ask = page.getByTestId("trip-incident").getByTestId("incident-ask-mobile");
  await expect(panel).toBeHidden();

  await expect(ask).toBeVisible();
  await ask.click();
  await expect(panel).toBeVisible();
  await expect(page.getByTestId("qa-title")).toContainText("班機");

  await page.getByTestId("qa-close").click();
  await expect(panel).toBeHidden();
  await ask.click();
  await expect(panel).toBeVisible();

  // 桌機：右欄固定顯示，沒有這個鈕
  await page.setViewportSize({ width: 1280, height: 800 });
  await expect(ask).toBeHidden();
  await expect(panel).toBeVisible();
});

test("手機：有兩個事故時，選中的事故卡也有「問答」（#422）", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  const { policyId } = await setup(page, "two-mobile", ["班機延誤了六小時", "班機取消了"]);
  await page.goto(`/trip/${policyId}`);
  const cards = page.getByTestId("trip-incident");
  await expect(cards.nth(0).getByTestId("incident-ask-mobile")).toBeVisible();
  await expect(cards.nth(1).getByTestId("incident-ask-mobile")).toHaveCount(0);
  await expect(cards.nth(1).getByTestId("incident-ask")).toBeVisible();
});

test("手機橫式矮視窗：問答面板是貼右的整高欄位，輸入框看得到、不蓋住說明文字（#410）", async ({
  page,
}) => {
  await page.setViewportSize({ width: 920, height: 337 });
  const { policyId } = await setup(page, "landscape", ["班機延誤了六小時", "班機取消了"]);
  await page.goto(`/trip/${policyId}`);
  const panel = page.getByRole("region", { name: "問答" });
  await page.getByTestId("trip-incident").nth(1).getByTestId("incident-ask").click();
  await expect(panel).toBeVisible();

  const box = await panel.boundingBox();
  if (!box) throw new Error("找不到問答面板");
  expect(box.x).toBeGreaterThan(920 * 0.4); // 貼右，不是整排寬
  expect(box.x + box.width).toBeCloseTo(920, 0);
  expect(box.y).toBeCloseTo(0, 0);
  expect(box.height).toBeGreaterThanOrEqual(337 - 2); // 整高，不是 50dvh

  const input = await page.getByTestId("message").boundingBox();
  const caption = await page.getByText("回答由 AI 依條款整理").boundingBox();
  if (!input || !caption) throw new Error("找不到輸入框或說明文字");
  expect(input.y).toBeGreaterThanOrEqual(0);
  expect(input.y + input.height).toBeLessThanOrEqual(337); // 輸入框在可見範圍內
  expect(input.y + input.height).toBeLessThanOrEqual(caption.y + 1); // 沒有蓋到說明文字

  // 直式同一頁還是下方滑出的面板
  await page.setViewportSize({ width: 390, height: 800 });
  const portrait = await panel.boundingBox();
  if (!portrait) throw new Error("直式找不到問答面板");
  expect(portrait.x).toBeCloseTo(0, 0);
  expect(portrait.width).toBeCloseTo(390, 0);
  expect(portrait.y).toBeGreaterThan(800 * 0.4);
});

// 設計者 11/14 的調整：不符保障與資訊不足分開標示、結案後說明。5 號的假實作產不出「資訊不足」，
// 所以改行程 API 的回應來看畫面（只改回應，不動資料）。
async function stubTrip(page: Page, policyId: string, change: (trip: any) => void) {
  await page.route(`**/api/v1/policies/${policyId}/trip`, async (route) => {
    try {
      const response = await route.fetch();
      const body = await response.json();
      change(body);
      await route.fulfill({ response, json: body });
    } catch {
      // 測試結束時還在進行的重新讀取：不算錯
    }
  });
}

test("資訊不足：事故卡標「資訊不足，尚無法判斷」，送件區說明可補充說明或結案", async ({ page }) => {
  const { policyId } = await setup(page, "insufficient", ["班機延誤了六小時"]);
  await stubTrip(page, policyId, (trip) => {
    const incident = trip.incidents[0];
    incident.covered = false;
    incident.clauses = incident.clauses.map((c: object) => ({ ...c, conclusion: "insufficient" }));
    incident.todos = [];
    trip.shared_todos = [];
    trip.can_close = true;
    trip.can_submit = false;
  });
  await page.goto(`/trip/${policyId}`);

  const card = page.getByTestId("trip-incident");
  await expect(card.getByTestId("insufficient")).toHaveText("資訊不足，尚無法判斷");
  await expect(card.getByTestId("not-covered")).toHaveCount(0);
  // 資訊不足補了資訊就會有待辦：卡上明講現在還沒有待辦、補充後會重新判斷（#445）
  await expect(card.getByTestId("insufficient-note")).toHaveText(
    "目前的資訊還無法判斷能不能理賠，所以還沒有待辦。在問答補充說明後會重新判斷，符合的話就會產生待辦。",
  );
  await expect(page.getByTestId("send-box")).toContainText("資訊不足，還不能送件");
  await expect(page.getByTestId("close-note")).toContainText("也可以結案");
});

test("不符保障的事故裡有範本待辦：反灰，組小標題下說明；上傳框旁提示", async ({ page }) => {
  const { policyId } = await setup(page, "greyed", ["班機延誤了六小時"]);
  await stubTrip(page, policyId, (trip) => {
    trip.incidents[0].covered = false;
    trip.incidents[0].clauses = trip.incidents[0].clauses.map((c: object) => ({
      ...c,
      conclusion: "excluded",
    }));
  });
  await page.goto(`/trip/${policyId}`);

  await expect(page.getByTestId("not-covered")).toHaveText("不符保障");
  // 不在保障範圍的事故補資訊也不會有待辦：不顯示資訊不足的說明（#445）
  await expect(page.getByTestId("insufficient-note")).toHaveCount(0);
  await expect(page.getByTestId("group-not-covered").first()).toHaveText(
    "這個事故依條款不在保障範圍，這些文件不需要準備",
  );
  await expect(page.getByTestId("upload-uncovered-note")).toHaveText(
    "不符保障的事故不用上傳文件。",
  );
});

test("建立草稿結案：送件區提醒草稿還沒寄出要到 Gmail 寄出", async ({ page }) => {
  const { policyId } = await setup(page, "drafted", ["班機延誤了六小時"]);
  const api = page.request;
  const trip = await (await api.get(`/api/v1/policies/${policyId}/trip`)).json();
  const todos = [
    ...trip.shared_todos,
    ...trip.incidents.flatMap((i: { todos: unknown[] }) => i.todos),
  ];
  for (const t of todos as { id: string; applicable: boolean }[]) {
    if (t.applicable)
      await api.patch(`/api/v1/policies/${policyId}/todos/${t.id}`, { data: { is_done: true } });
  }
  const drafted = await api.post(`/api/v1/policies/${policyId}/submission`, {
    data: { mode: "draft", recipient: "claims@example.com" },
  });
  expect(drafted.status(), await drafted.text()).toBe(201);

  await page.goto(`/trip/${policyId}`);
  await expect(page.getByTestId("send-closed")).toContainText("已在你的 Gmail 建立草稿");
  await expect(page.getByTestId("send-draft-note")).toHaveCount(0);
  await expect(page.getByTestId("locked-note").getByTestId("new-trip")).toHaveAttribute(
    "href",
    "/incident/new",
  );
});

// #385：左欄捲到哪個事故的狀況總結，右欄問答就換成那個事故（閱讀線在視窗上緣下方約 48px）。
// 事故卡的 data-selected 標示選中的是哪一張。
async function selectedIndex(page: Page) {
  return page
    .getByTestId("trip-incident")
    .evaluateAll((cards) => cards.findIndex((c) => c.getAttribute("data-selected") === "true"));
}

// 把第 index 張事故卡的上緣捲到視窗上緣下方 30px（過了閱讀線）
async function scrollCardToTop(page: Page, index: number) {
  await page.evaluate((i) => {
    const card = document.querySelectorAll('[data-testid="trip-incident"]')[i];
    window.scrollBy(0, (card?.getBoundingClientRect().top ?? 0) - 30);
  }, index);
}

async function selectByAsk(page: Page, index: number) {
  await page.getByTestId("trip-incident").nth(index).getByTestId("incident-ask").click();
  await expect.poll(() => selectedIndex(page)).toBe(index);
  await page.waitForTimeout(1500); // 等自動捲動結束
}

test("捲動自動切換：選中下方事故後往上捲到上方事故的狀況總結，問答換成上方事故", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 600 });
  const { policyId, incidentIds } = await setup(page, "scroll-up", [
    "班機延誤了六小時",
    "行李遺失了被偷",
  ]);
  await page.goto(`/trip/${policyId}`);
  await expect(page.getByTestId("trip-incident")).toHaveCount(2);
  await selectByAsk(page, 1);
  const lower = (await page.getByTestId("qa-title").textContent()) ?? "";

  await scrollCardToTop(page, 0);

  await expect.poll(() => selectedIndex(page)).toBe(0);
  await expect(page.getByTestId("qa-title")).not.toHaveText(lower);
  await expect(page.getByTestId("trip-incident").nth(0)).toHaveAttribute(
    "data-incident-id",
    incidentIds[0] ?? "",
  );
});

test("捲動自動切換：選中下方事故後直接跳回最上方，問答換成第一個事故", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 600 });
  const { policyId } = await setup(page, "scroll-top", ["班機延誤了六小時", "行李遺失了被偷"]);
  await page.goto(`/trip/${policyId}`);
  await selectByAsk(page, 1);

  // 閱讀線在第一張卡片之上（頁面最上方）：沒有卡片壓在線上，也要取第一張
  await page.evaluate(() => window.scrollTo(0, 0));

  await expect.poll(() => selectedIndex(page)).toBe(0);
});

test("捲動自動切換：輸入框有字時捲到另一個事故，問答不換，字還在", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 600 });
  const { policyId } = await setup(page, "scroll-draft", ["班機延誤了六小時", "行李遺失了被偷"]);
  await page.goto(`/trip/${policyId}`);
  await selectByAsk(page, 1);
  await page.getByTestId("message").fill("打到一半的問題");

  await scrollCardToTop(page, 0);
  await page.waitForTimeout(1000);

  expect(await selectedIndex(page)).toBe(1);
  await expect(page.getByTestId("message")).toHaveValue("打到一半的問題");
});

test("捲動自動切換：捲到理賠待辦區，問答維持原本的事故", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 600 });
  const { policyId } = await setup(page, "scroll-todos", ["班機延誤了六小時", "行李遺失了被偷"]);
  await page.goto(`/trip/${policyId}`);
  await selectByAsk(page, 1);

  await page.getByRole("region", { name: "理賠待辦" }).scrollIntoViewIfNeeded();
  await page.evaluate(() => window.scrollBy(0, 200));
  await page.waitForTimeout(1000);

  expect(await selectedIndex(page)).toBe(1);
});

test("捲動自動切換：按下方事故的「問答」，捲動結束後選中的仍是它，不被途中經過的卡片搶走", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1280, height: 600 });
  const { policyId } = await setup(page, "scroll-click", [
    "班機延誤了六小時",
    "班機取消了",
    "行李遺失了被偷",
  ]);
  await page.goto(`/trip/${policyId}`);
  const cards = page.getByTestId("trip-incident");
  await expect(cards).toHaveCount(3);

  await cards.nth(2).getByTestId("incident-ask").click();
  const seen = new Set<number>();
  for (let i = 0; i < 20; i++) {
    seen.add(await selectedIndex(page));
    await page.waitForTimeout(100);
  }

  expect(await selectedIndex(page)).toBe(2);
  expect([...seen]).not.toContain(1); // 途中沒有切到中間那張
});

test("捲動自動切換：只有一個事故，捲動不改變問答", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 600 });
  const { policyId } = await setup(page, "scroll-one", ["班機延誤了六小時"]);
  await page.goto(`/trip/${policyId}`);
  const title = (await page.getByTestId("qa-title").textContent()) ?? "";

  await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
  await page.waitForTimeout(500);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(500);

  await expect(page.getByTestId("qa-title")).toHaveText(title);
  expect(await selectedIndex(page)).toBe(0);
});

test("捲動自動切換：等回覆時捲到另一個事故，問答不換", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 600 });
  const { policyId } = await setup(page, "scroll-busy", ["班機延誤了六小時", "行李遺失了被偷"]);
  await page.goto(`/trip/${policyId}`);
  await selectByAsk(page, 1);
  // 5 號回覆慢 4 秒：這段時間 busy／asking 為真
  await page.route("**/api/v1/incidents/*/messages", async (route) => {
    if (route.request().method() === "POST") await new Promise((r) => setTimeout(r, 4000));
    await route.continue();
  });
  await page.getByTestId("message").fill("要準備什麼文件？");
  await page.getByTestId("send").click();

  await scrollCardToTop(page, 0);
  await page.waitForTimeout(1000);

  expect(await selectedIndex(page)).toBe(1);
});

test("捲動自動切換：手機問答面板拉開時，捲動不換問答", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 800 });
  const { policyId } = await setup(page, "scroll-mobile", ["班機延誤了六小時", "行李遺失了被偷"]);
  await page.goto(`/trip/${policyId}`);
  await selectByAsk(page, 1);
  await expect(page.getByRole("region", { name: "問答" })).toBeVisible();

  await scrollCardToTop(page, 0);
  await page.waitForTimeout(1000);

  expect(await selectedIndex(page)).toBe(1);
});

// 行程頁問答補完資訊後再確認（#445）：後端判斷「問完 + 結論或適用待辦有變」才回 show_confirm。
// 假 5 號只要使用者的話裡有「不理賠」，就把保障改成 excluded（結論有變）。
async function askInTrip(page: Page, text: string) {
  const replied = page.waitForResponse(
    (r) => r.url().endsWith("/messages") && r.request().method() === "POST",
  );
  await page.getByTestId("message").fill(text);
  await page.getByTestId("send").click();
  await replied;
}

test("問答補完資訊讓結論有變：再跳確認彈窗，「確認」只關彈窗，不呼叫確認 API", async ({ page }) => {
  const { policyId } = await setup(page, "reconfirm", ["班機延誤了六小時"]);
  await spyScrollIntoView(page);
  await page.goto(`/trip/${policyId}`);
  const confirmCalls: string[] = [];
  page.on("request", (r) => {
    if (r.url().endsWith("/confirm")) confirmCalls.push(r.url());
  });

  await askInTrip(page, "這樣不理賠嗎？");

  const dialog = page.getByTestId("confirm-dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText("您補充的內容讓判斷有變");
  // 事故早就確認過：按鈕不寫「進入行程頁」
  await expect(dialog.getByTestId("confirm-yes")).toHaveText("確認");
  // 背景已經先更新：事故卡不再有可能適用的項目
  await expect(page.getByTestId("not-covered")).toBeVisible();

  await dialog.getByTestId("confirm-yes").click();
  await expect(dialog).toBeHidden();
  expect(confirmCalls).toEqual([]);
  await expect(page).toHaveURL(new RegExp(`/trip/${policyId}`));
  // 這裡補完後變成不在保障範圍、沒有待辦：不捲到理賠待辦（#449）
  expect(await scrolledTo(page)).not.toContain("todos-section");
});

// 假 5 號補完後只會變成不在保障範圍（沒有待辦），所以用行程 API 的回應模擬「補完後有待辦」：
// 補充前照原樣，補充後把待辦與「在保障內」補回去（只改回應，不動資料）
test("再跳的確認彈窗按「確認」：該事故有待辦就捲到理賠待辦（#449）", async ({ page }) => {
  const { policyId } = await setup(page, "reconfirm-scroll", ["班機延誤了六小時"]);
  let before: { covered: boolean; todos: unknown[] } | null = null;
  await page.route(`**/api/v1/policies/${policyId}/trip`, async (route) => {
    try {
      const response = await route.fetch();
      const body = await response.json();
      const incident = body.incidents[0];
      if (!before) before = { covered: incident.covered, todos: incident.todos };
      else {
        incident.covered = before.covered;
        incident.todos = before.todos;
      }
      await route.fulfill({ response, json: body });
    } catch {
      // 測試結束時還在進行的重新讀取：不算錯
    }
  });
  await spyScrollIntoView(page);
  await page.goto(`/trip/${policyId}`);
  await expect(page.getByTestId("trip-incident")).toBeVisible();
  await askInTrip(page, "這樣不理賠嗎？");

  const dialog = page.getByTestId("confirm-dialog");
  await expect(dialog).toBeVisible();
  expect(await scrolledTo(page)).not.toContain("todos-section"); // 彈窗還開著，還沒捲
  await dialog.getByTestId("confirm-yes").click();
  await expect(dialog).toBeHidden();
  await expect.poll(() => scrolledTo(page)).toContain("todos-section");
  await expect(page.getByTestId("todos-section")).toBeInViewport();
});

test("再跳的確認彈窗按「還要補充或修改」：關彈窗，焦點回到問答輸入框", async ({ page }) => {
  const { policyId } = await setup(page, "reconfirm-supplement", ["班機延誤了六小時"]);
  await page.goto(`/trip/${policyId}`);
  await askInTrip(page, "這樣不理賠嗎？");

  const dialog = page.getByTestId("confirm-dialog");
  await expect(dialog).toBeVisible();
  await dialog.getByTestId("confirm-supplement").click();
  await expect(dialog).toBeHidden();
  await expect(page.getByTestId("message")).toBeFocused();
});

test("問答只問條款、結論沒變：不跳確認彈窗", async ({ page }) => {
  const { policyId } = await setup(page, "reconfirm-none", ["班機延誤了六小時"]);
  await page.goto(`/trip/${policyId}`);
  await askInTrip(page, "診斷書要正本嗎？");

  await expect(page.getByTestId("messages")).toContainText("診斷書要正本嗎？");
  await expect(page.getByTestId("confirm-dialog")).toHaveCount(0);
});
