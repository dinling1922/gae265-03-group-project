import { readFileSync } from "node:fs";
import { expect, type Page, test } from "@playwright/test";
import { login } from "./helpers";

// 日程頁的 7 個 WebMCP 工具：用 document.modelContext.executeTool 直接呼叫，不經過 LLM。
// Chromium 要加 --enable-features=WebMCP（playwright.config.ts 已加）。

function day(offset: number): string {
  const d = new Date();
  d.setDate(d.getDate() + offset);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
/** 2026-09-18 → 9/18（agent 常用的寫法，年份由網站決定）。 */
const md = (iso: string) => `${Number(iso.slice(5, 7))}/${Number(iso.slice(8, 10))}`;

test.beforeEach(async ({ request }) => {
  const config = await (await request.get("/api/v1/auth/config")).json();
  test.skip(!config.dev_login, "後端沒有設 DEV_LOGIN_ENABLED=true");
});

type ToolResult = { isError: boolean; text: string };

/** 呼叫已註冊的工具；回傳是否出錯與回應文字。 */
async function call(page: Page, name: string, args: object = {}): Promise<ToolResult> {
  return page.evaluate(
    async ([toolName, toolArgs]) => {
      // biome-ignore lint/suspicious/noExplicitAny: 瀏覽器的 WebMCP API 沒有型別宣告
      const modelContext = (document as any).modelContext;
      // biome-ignore lint/suspicious/noExplicitAny: 同上
      const tool = (await modelContext.getTools()).find((t: any) => t.name === toolName);
      if (!tool) return { isError: true, text: `沒有註冊 ${toolName}` };
      const raw = await modelContext.executeTool(tool, toolArgs);
      // Chromium 回的是 JSON 字串（將來可能直接回物件）：兩種都接
      const result = typeof raw === "string" ? JSON.parse(raw) : raw;
      return { isError: Boolean(result.isError), text: String(result.content?.[0]?.text ?? "") };
    },
    [name, args] as const,
  );
}

/** 呼叫並要求成功，回傳解析後的 JSON。 */
async function ok(page: Page, name: string, args: object = {}) {
  const result = await call(page, name, args);
  expect(result.isError, result.text).toBe(false);
  return JSON.parse(result.text);
}

/** 呼叫並要求失敗，回傳錯誤訊息。 */
async function fail(page: Page, name: string, args: object = {}) {
  const result = await call(page, name, args);
  expect(result.isError, `預期出錯，卻成功：${result.text}`).toBe(true);
  return result.text;
}

const toolNames = (page: Page) =>
  page.evaluate(async () => {
    // biome-ignore lint/suspicious/noExplicitAny: 同上
    const tools = await (document as any).modelContext.getTools();
    return tools.map((t: { name: string }) => t.name).sort();
  });

/** 在 `/compare/new` 用工具建立一趟 6 天的日本旅行，回傳比較紀錄 id。 */
async function createTrip(page: Page, depart = 7, ret = 12): Promise<string> {
  await page.goto("/compare/new");
  await expect(page.getByTestId("open-tools")).toBeVisible();
  await ok(page, "set_trip_details", {
    destination: "日本",
    city: "京都",
    start_date: day(depart),
    end_date: day(ret),
  });
  await expect(page).toHaveURL(/\/compare\/[0-9a-f-]{36}\/itinerary$/);
  return page.url().split("/").at(-2) as string;
}

const card = (page: Page, title: string) =>
  page.getByTestId("activity-card").filter({ hasText: title });

test("7 個工具都有註冊，內容與 schema.json 一致，離開日程頁後移除", async ({ page }) => {
  await login(page, "e2e-webmcp-register@example.com");
  await page.goto("/compare/new");
  await expect(page.getByTestId("open-tools")).toBeVisible();

  const schema = JSON.parse(
    readFileSync(new URL("../../docs/design/itinerary/schema.json", import.meta.url), "utf8"),
  ) as { tools: { name: string; description: string; inputSchema: object }[] };
  expect(await toolNames(page)).toEqual(schema.tools.map((t) => t.name).sort());
  expect(schema.tools).toHaveLength(7);

  const registered = await page.evaluate(async () => {
    // biome-ignore lint/suspicious/noExplicitAny: 同上
    const tools = await (document as any).modelContext.getTools();
    return tools.map((t: { name: string; description: string; inputSchema: object }) => ({
      name: t.name,
      description: t.description,
      inputSchema: typeof t.inputSchema === "string" ? JSON.parse(t.inputSchema) : t.inputSchema,
    }));
  });
  for (const tool of schema.tools) {
    const found = registered.find((t: { name: string }) => t.name === tool.name);
    expect(found?.description, tool.name).toBe(tool.description);
    expect(found?.inputSchema, tool.name).toEqual(tool.inputSchema);
  }

  // 客戶端換頁到首頁（頁面沒有重新載入）：工具跟著移除
  await page.getByRole("link", { name: "好險有你" }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect.poll(() => toolNames(page)).toEqual([]);
});

test("在 /compare/new 呼叫 set_trip_details：表單填好、網址換成日程頁", async ({ page }) => {
  await login(page, "e2e-webmcp-trip@example.com");
  await page.goto("/compare/new");
  await expect(page.getByTestId("open-tools")).toBeVisible();

  // 出發日用 M/D、回程日用完整日期：年份由網站決定
  const result = await ok(page, "set_trip_details", {
    destination: "日本",
    city: "京都",
    start_date: md(day(7)),
    end_date: day(11),
  });
  expect(result).toMatchObject({
    created: true,
    destination: "日本",
    city: "京都",
    start_date: day(7),
    end_date: day(11),
    day_count: 5,
    removed_activity_count: 0,
  });
  await expect(page).toHaveURL(/\/compare\/[0-9a-f-]{36}\/itinerary$/);
  await expect(page.getByLabel("目的地")).toHaveValue("日本");
  await expect(page.getByLabel("城市")).toHaveValue("京都");
  await expect(page.getByLabel("出發日")).toHaveValue(day(7));
  await expect(page.getByLabel("回程日")).toHaveValue(day(11));
  await expect(page.getByRole("tab")).toHaveCount(5);
  await expect(page.getByTestId("toast")).toHaveText("agent 設定了旅行規劃");
  // 換頁之後工具也重新註冊
  expect(await toolNames(page)).toHaveLength(7);
});

test("set_trip_details 的錯誤：日期格式、天數、目的地", async ({ page }) => {
  await login(page, "e2e-webmcp-trip-errors@example.com");
  await page.goto("/compare/new");
  await expect(page.getByTestId("open-tools")).toBeVisible();

  const invalid = await fail(page, "set_trip_details", {
    destination: "日本",
    start_date: "下週五",
    end_date: "9/22",
  });
  expect(invalid).toContain("INVALID_DATE");
  expect(
    await fail(page, "set_trip_details", {
      destination: "日本",
      start_date: day(7),
      end_date: day(18),
    }),
  ).toContain("旅遊天數最長 10 天");
  expect(
    await fail(page, "set_trip_details", {
      destination: "冰島",
      start_date: day(7),
      end_date: day(11),
    }),
  ).toContain("日本");
  await expect(page).toHaveURL(/\/compare\/new$/); // 都沒有建立
});

test("add_itinerary_activities：用 day 與 hour 加活動、跳 toast；時段重複的那筆回報 SLOT_TAKEN", async ({
  page,
}) => {
  await login(page, "e2e-webmcp-add@example.com");
  await createTrip(page);

  const first = await ok(page, "add_itinerary_activities", {
    activities: [{ day: 1, hour: 9, title: "進進堂早餐", category: "coffee" }],
  });
  expect(first.added_count).toBe(1);
  expect(first.failed).toEqual([]);
  expect(first.days).toEqual([{ day: 1, activity_count: 1 }]);
  await expect(page.getByTestId("slot-9").getByTestId("activity-card")).toContainText("進進堂早餐");
  await expect(page.getByTestId("toast")).toHaveText(
    `agent 新增了 ${md(day(7))} 09:00「進進堂早餐」`,
  );
  const plan = await ok(page, "get_day_plan", { day: 1 });
  const occupant = plan.activities[0].id as string;

  // 批次：第 2 筆撞到已存的 9:00，第 3 筆撞到同一批的 11:00
  const batch = await ok(page, "add_itinerary_activities", {
    activities: [
      { date: md(day(7)), hour: 10, title: "清水寺" },
      { day: 1, hour: 9, title: "另一間早餐" },
      { day: 1, hour: 11, title: "錦市場" },
      { day: 1, hour: 11, title: "同批重複" },
    ],
  });
  expect(batch.added_count).toBe(2);
  expect(batch.failed).toHaveLength(2);
  expect(batch.failed[0]).toMatchObject({ index: 1, code: "SLOT_TAKEN", occupied_by: occupant });
  expect(batch.failed[0].message).toContain("進進堂早餐");
  expect(batch.failed[0].message).toContain("update_itinerary_activity");
  expect(batch.failed[1]).toMatchObject({ index: 3, code: "SLOT_TAKEN" });
  await expect(page.getByTestId("toast")).toHaveText("agent 新增了 2 個活動");
  await expect(page.getByTestId("activity-card")).toHaveCount(3);

  // 全部失敗：回錯誤，說明原因
  const allFailed = await fail(page, "add_itinerary_activities", {
    activities: [{ day: 1, hour: 9, title: "再一間" }],
  });
  expect(allFailed).toContain("SLOT_TAKEN");

  // 結構上就不能用的資料整批不送出，訊息指出第幾筆
  const bad = await fail(page, "add_itinerary_activities", {
    activities: [
      { day: 1, hour: 12, title: "好的" },
      { day: 9, hour: 12, title: "第九天" },
      { day: 1, hour: 3, title: "凌晨" },
    ],
  });
  expect(bad).toContain("第 2 筆");
  expect(bad).toContain("這趟只有 6 天");
  expect(bad).toContain("第 3 筆");
  await expect(page.getByTestId("activity-card")).toHaveCount(3);
});

test("還沒設定日期就呼叫會改資料的工具：回「請先用 set_trip_details」", async ({ page }) => {
  await login(page, "e2e-webmcp-notrip@example.com");
  await page.goto("/compare/new");
  await expect(page.getByTestId("open-tools")).toBeVisible();
  for (const [name, args] of [
    ["add_itinerary_activities", { activities: [{ day: 1, hour: 9, title: "x" }] }],
    ["get_day_plan", { day: 1 }],
    ["update_itinerary_activity", { activity_id: "x", title: "y" }],
    ["remove_itinerary_activity", { activity_id: "x" }],
    ["set_map_view", { day: 1 }],
  ] as const) {
    expect(await fail(page, name, args), name).toContain("請先用 set_trip_details");
  }
  const state = await ok(page, "get_trip_state");
  expect(state.has_trip).toBe(false);
});

test("update_itinerary_activity 與 remove_itinerary_activity：畫面更新、不存在的 id 回可照著做的錯誤", async ({
  page,
}) => {
  await login(page, "e2e-webmcp-update@example.com");
  await createTrip(page);
  await ok(page, "add_itinerary_activities", {
    activities: [
      { day: 2, hour: 15, title: "竹林散步", category: "explore" },
      { day: 2, hour: 12, title: "蕎麥麵", category: "eat" },
    ],
  });
  const plan = await ok(page, "get_day_plan", { day: 2 });
  const bamboo = plan.activities.find((a: { title: string }) => a.title === "竹林散步").id;
  const soba = plan.activities.find((a: { title: string }) => a.title === "蕎麥麵").id;

  const updated = await ok(page, "update_itinerary_activity", {
    activity_id: bamboo,
    title: "京都國際漫畫博物館",
    category: "culture",
    latitude: 35.0117,
    longitude: 135.7593,
  });
  expect(updated).toMatchObject({ id: bamboo, day: 2, hour: 15, title: "京都國際漫畫博物館" });
  expect(updated.updated_fields).toEqual(["title", "category", "latitude", "longitude"]);
  await expect(page.getByRole("tab", { name: /第 2 天/ })).toHaveAttribute("aria-selected", "true");
  await expect(card(page, "京都國際漫畫博物館")).toHaveAttribute("data-selected", "true");
  await expect(card(page, "京都國際漫畫博物館")).not.toContainText("沒有座標");
  await expect(page.getByTestId("toast")).toHaveText(`agent 修改了 ${md(day(8))} 15:00 的活動`);

  // 移到其他時段與日子；移到已佔用的時段回錯誤並附佔用者 id
  const moved = await ok(page, "update_itinerary_activity", {
    activity_id: bamboo,
    day: 3,
    hour: 10,
  });
  expect(moved).toMatchObject({ day: 3, hour: 10 });
  expect(moved.updated_fields).toEqual(["date", "hour"]);
  const clash = await fail(page, "update_itinerary_activity", {
    activity_id: bamboo,
    day: 2,
    hour: 12,
  });
  expect(clash).toContain("SLOT_TAKEN");
  expect(clash).toContain(soba);

  expect(await fail(page, "update_itinerary_activity", { activity_id: bamboo })).toContain(
    "沒有要修改的欄位",
  );
  const missing = "00000000-0000-0000-0000-000000000000";
  expect(
    await fail(page, "update_itinerary_activity", { activity_id: missing, title: "x" }),
  ).toContain("get_day_plan");
  expect(await fail(page, "remove_itinerary_activity", { activity_id: missing })).toContain(
    "找不到這個活動",
  );

  const removed = await ok(page, "remove_itinerary_activity", { activity_id: soba });
  expect(removed).toMatchObject({ removed_id: soba, title: "蕎麥麵", day: 2, hour: 12 });
  await expect(page.getByTestId("toast")).toHaveText(`agent 移除了 ${md(day(8))} 12:00「蕎麥麵」`);
  await expect(card(page, "蕎麥麵")).toHaveCount(0);
  await expect(page.getByRole("tab", { name: /第 2 天/ })).toContainText("0 個活動");
});

test("縮短日期：未帶 confirm_remove 回 WOULD_REMOVE_ACTIVITIES 且什麼都沒刪，帶了才刪", async ({
  page,
}) => {
  await login(page, "e2e-webmcp-shorten@example.com");
  await createTrip(page);
  await ok(page, "add_itinerary_activities", {
    activities: [
      { day: 1, hour: 9, title: "第一天早餐" },
      { day: 5, hour: 10, title: "陶藝小店" },
      { day: 6, hour: 9, title: "最後一杯咖啡" },
    ],
  });
  const shorter = { destination: "日本", city: "京都", start_date: day(7), end_date: day(10) };

  const blocked = await fail(page, "set_trip_details", shorter);
  expect(blocked).toContain("WOULD_REMOVE_ACTIVITIES");
  expect(blocked).toContain("陶藝小店");
  expect(blocked).toContain("最後一杯咖啡");
  expect(blocked).toContain("confirm_remove");
  await expect(page.getByLabel("回程日")).toHaveValue(day(12));
  await expect(page.getByRole("tab")).toHaveCount(6);
  expect((await ok(page, "get_trip_state")).activity_count).toBe(3);

  const done = await ok(page, "set_trip_details", { ...shorter, confirm_remove: true });
  expect(done).toMatchObject({ created: false, day_count: 4, removed_activity_count: 2 });
  await expect(page.getByRole("tab")).toHaveCount(4);
  await expect(page.getByLabel("回程日")).toHaveValue(day(10));
  await expect(page.getByTestId("toast")).toHaveText("agent 修改了日期，移除了 2 個活動");
  expect((await ok(page, "get_trip_state")).activity_count).toBe(1);
});

test("set_map_view({ day, activity_id })：日期分頁、pin、清單同步標示；沒有座標的活動回錯誤", async ({
  page,
}) => {
  await login(page, "e2e-webmcp-map@example.com");
  await createTrip(page);
  await ok(page, "add_itinerary_activities", {
    activities: [
      { day: 3, hour: 9, title: "嵐山小徑", latitude: 35.0094, longitude: 135.6668 },
      { day: 3, hour: 15, title: "抹茶", latitude: 35.0167, longitude: 135.6742 },
      { day: 3, hour: 18, title: "沒座標的晚餐" },
      { day: 1, hour: 9, title: "第一天咖啡", latitude: 35.0055, longitude: 135.7656 },
    ],
  });
  const plan = await ok(page, "get_day_plan", { day: 3 });
  const id = plan.activities.find((a: { title: string }) => a.title === "抹茶").id;
  const noCoords = plan.activities.find((a: { title: string }) => a.title === "沒座標的晚餐").id;
  expect(plan.unmapped_count).toBe(1);

  await expect(page.getByTestId("toast")).toHaveCount(0); // 前面新增的 toast 已消失
  const view = await ok(page, "set_map_view", { day: 3, activity_id: id });
  expect(view).toEqual({
    scope: "day",
    day: 3,
    selected_activity_id: id,
    stop_count: 2,
    unmapped_count: 1,
  });
  await expect(page.getByRole("tab", { name: /第 3 天/ })).toHaveAttribute("aria-selected", "true");
  await expect(page.locator('[data-testid="map-pin"][data-n="2"]')).toHaveAttribute(
    "data-selected",
    "true",
  );
  await expect(page.getByTestId("stop-item").nth(1)).toHaveAttribute("data-selected", "true");
  await expect(card(page, "抹茶")).toHaveAttribute("data-selected", "true");
  await expect(page.getByTestId("toast")).toHaveCount(0); // 只是切換畫面，不跳 toast
  expect((await ok(page, "get_trip_state")).selected_activity_id).toBe(id);

  const trip = await ok(page, "set_map_view", { scope: "trip" });
  expect(trip).toMatchObject({ scope: "trip", stop_count: 3, unmapped_count: 1 });
  await expect(page.getByTestId("map-pin")).toHaveCount(3);

  expect(await fail(page, "set_map_view", { day: 3, activity_id: noCoords })).toContain("沒有座標");
  expect(await fail(page, "set_map_view", { day: 1, activity_id: id })).toContain("第 3 天");
  expect(await fail(page, "set_map_view", { day: 99 })).toContain("這趟只有 6 天");
});

test("get_trip_state 與 get_day_plan 的回應不超過 1,500 字元（10 天、每天排滿）", async ({
  page,
}) => {
  await login(page, "e2e-webmcp-size@example.com");
  await createTrip(page, 7, 16); // 10 天
  // 每天 7～22 點排滿 16 個、標題與地點都寫得長：直接打 API 比較快（一批最多 30 筆）
  const id = page.url().split("/").at(-2);
  const all = Array.from({ length: 10 }, (_, d) =>
    Array.from({ length: 16 }, (_, h) => ({
      date: day(7 + d),
      hour: 7 + h,
      title: `第${d + 1}天${7 + h}點的一個名字很長很長的活動標題`,
      location: "京都市中京區一個名字也很長很長的地點",
      description: "很長的說明".repeat(20),
      category: "explore",
      latitude: 35 + h / 100,
      longitude: 135 + d / 100,
    })),
  ).flat();
  for (let i = 0; i < all.length; i += 30) {
    const response = await page.request.post(`/api/v1/comparisons/${id}/activities`, {
      data: { activities: all.slice(i, i + 30) },
    });
    expect(response.ok()).toBeTruthy();
  }
  await page.reload();
  await expect(page.getByTestId("open-tools")).toBeVisible();
  await expect(page.getByRole("tab", { name: /第 1 天/ })).toContainText("16 個活動");

  const state = await call(page, "get_trip_state");
  expect(state.isError).toBe(false);
  expect(state.text.length).toBeLessThanOrEqual(1500);
  expect(JSON.parse(state.text).activity_count).toBe(160);
  for (const n of [1, 5, 10]) {
    const plan = await call(page, "get_day_plan", { day: n });
    expect(plan.isError).toBe(false);
    expect(plan.text.length, `第 ${n} 天`).toBeLessThanOrEqual(1500);
    const parsed = JSON.parse(plan.text);
    expect(parsed.day).toBe(n);
    expect(parsed.free_hours).toEqual([]);
  }
});

test("工具面板：7 個工具、4 句範例說法、支援狀態；不支援 WebMCP 時說明開啟方式", async ({
  page,
}) => {
  await login(page, "e2e-webmcp-panel@example.com");
  await page.goto("/compare/new");
  await page.getByTestId("open-tools").click();
  const dialog = page.getByTestId("tools-dialog");
  await expect(dialog.getByRole("heading", { name: "這一頁開放給 agent 的工具" })).toBeVisible();
  await expect(dialog.getByTestId("tools-list").locator("li")).toHaveCount(7);
  await expect(dialog.getByTestId("tools-list").getByText("唯讀")).toHaveCount(2);
  await expect(dialog.getByRole("button", { name: /^複製：/ })).toHaveCount(4);
  await expect(dialog.getByTestId("webmcp-status")).toHaveAttribute("data-supported", "true");
  await expect(dialog.getByTestId("webmcp-status")).toContainText("已啟用 WebMCP");

  await dialog.getByRole("button", { name: /^複製：幫我排京都/ }).click();
  await expect(page.getByTestId("toast")).toHaveText(/已複製|複製失敗/);
});

test("瀏覽器不支援 WebMCP 時，工具面板說明怎麼開啟", async ({ page }) => {
  // 模擬沒有 WebMCP 的瀏覽器：在頁面載入前把 document.modelContext 蓋掉
  await page.addInitScript(() => {
    Object.defineProperty(document, "modelContext", { value: undefined, configurable: true });
  });
  await login(page, "e2e-webmcp-unsupported@example.com");
  await page.goto("/compare/new");
  await page.getByTestId("open-tools").click();
  const status = page.getByTestId("webmcp-status");
  await expect(status).toHaveAttribute("data-supported", "false");
  await expect(status).toContainText("chrome://flags/#enable-webmcp-testing");
});
