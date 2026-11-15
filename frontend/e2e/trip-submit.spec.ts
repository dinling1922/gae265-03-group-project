import { expect, type Page, test } from "@playwright/test";
import { login } from "./helpers";

// 行程版送件（/trip/$policyId/submit，ADR-0029）：整個行程一封信、一次送件，三種結果都結案並鎖住行程。
// 測試登入沒有 Google 授權，不問授權；7 號用離線實作產信。需要 make seed。
const SLOW = 15_000;

async function setup(page: Page, name: string, ready: boolean) {
  await login(page, `e2e-trip-submit-${name}-${Date.now()}@example.com`);
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
  const createdRes = await api.post(`/api/v1/policies/${policy.id}/incidents`, {
    data: { description: "班機延誤了六小時" },
  });
  const created = await createdRes.json();
  if (!created.incident)
    throw new Error(`POST incidents 回 ${createdRes.status()}：${JSON.stringify(created)}`);
  const id: string = created.incident.incident_id;
  for (let i = 0; i < 15; i++) {
    const res = await api.post(`/api/v1/incidents/${id}/messages`, { data: { content: "不知道" } });
    const reply = await res.json();
    if (!reply.reply) throw new Error(`POST messages 回 ${res.status()}：${JSON.stringify(reply)}`);
    if (reply.reply.finished) break;
  }
  if (ready) {
    const trip = await (await api.get(`/api/v1/policies/${policy.id}/trip`)).json();
    const todos = [
      ...trip.shared_todos,
      ...trip.incidents.flatMap((i: { todos: unknown[] }) => i.todos),
    ];
    for (const t of todos as { id: string; applicable: boolean }[]) {
      if (t.applicable)
        await api.patch(`/api/v1/policies/${policy.id}/todos/${t.id}`, { data: { is_done: true } });
    }
  }
  return policy.id as string;
}

test.beforeEach(async ({ request }) => {
  const config = await (await request.get("/api/v1/auth/config")).json();
  test.skip(!config.dev_login, "後端沒有設 DEV_LOGIN_ENABLED=true");
  test.skip(!(await request.get("/api/v1/plans")).ok(), "條款庫沒有載入，先 make seed");
});

test("待辦沒完成：說明還缺什麼，兩個選項都不能按，連結回行程頁", async ({ page }) => {
  const policyId = await setup(page, "notready", false);
  await page.goto(`/trip/${policyId}/submit`);

  await expect(page.getByRole("heading", { level: 1 })).toHaveText("送件");
  await expect(page.getByTestId("ready")).toContainText("還有");
  await expect(page.getByTestId("choose-self")).toBeDisabled();
  await expect(page.getByTestId("choose-send")).toBeDisabled();
  await page.getByTestId("ready").getByRole("link", { name: "上傳文件" }).click();
  await expect(page).toHaveURL(new RegExp(`/trip/${policyId}#upload-box$`));
});

test("行程頁的「送件」連到送件頁；備齊後預覽、寄出、結案，行程鎖住", async ({ page }) => {
  const policyId = await setup(page, "send", true);
  await page.goto(`/trip/${policyId}`);
  await page.getByTestId("go-submit").click();
  await expect(page).toHaveURL(new RegExp(`/trip/${policyId}/submit$`));
  await expect(page.getByTestId("ask")).toContainText("要由我們用你的 Gmail 幫你寄給保險公司嗎？");
  await page.getByTestId("choose-send").click();

  await expect(page.getByRole("heading", { level: 1 })).toHaveText("確認信件");
  const preview = page.getByTestId("preview");
  await expect(preview.getByText("主旨", { exact: true })).toBeVisible({ timeout: SLOW });
  const recipient = page.getByTestId("letter-recipient");
  await recipient.fill("claims@example.com");
  const blanks = page.getByTestId("blank");
  for (const input of await blanks.all()) await input.fill("測試資料");
  await page.getByTestId("submit").click();
  await page.getByTestId("send-confirm").click();

  await expect(page.getByTestId("closed")).toContainText(
    "已替你送件並於你的 Gmail 寄件備份留存紀錄",
    {
      timeout: SLOW,
    },
  );
  await expect(page.getByTestId("closed-banner")).toHaveText(
    "此行程已結案，只能查看，不能再新增狀況或修改。",
  );
  // 寄出後跳出說明視窗（信在 Gmail 的寄件備份），關掉後才看得到頁面
  const gmail = page.getByTestId("gmail-result-dialog");
  await expect(gmail).toContainText("寄件備份");
  await gmail.getByRole("button", { name: "關閉" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("送件結果");

  // 回行程頁：已結案、送件區說明寄出、不能再勾
  await page.getByRole("link", { name: "回行程頁" }).click();
  await expect(page).toHaveURL(new RegExp(`/trip/${policyId}$`));
  await expect(page.getByTestId("send-closed")).toContainText("已替你送件");
  await expect(page.getByTestId("todos").getByRole("checkbox").first()).toBeDisabled();
});

test("自己處理就好：不建草稿，直接結案", async ({ page }) => {
  const policyId = await setup(page, "self", true);
  await page.goto(`/trip/${policyId}/submit`);
  await page.getByTestId("choose-self").click();
  await page.getByRole("button", { name: "取消" }).click(); // 草稿詢問：取消＝不建草稿
  await expect(page.getByTestId("closed")).toContainText("未在你的 Gmail 建立草稿", {
    timeout: SLOW,
  });
  await expect(page.getByTestId("closed-banner")).toBeVisible();
});

test("小助理提醒：勾了卻沒附文件的待辦會提醒，補上文件後重新檢查就少一條；待辦沒完成時不顯示", async ({
  page,
}) => {
  const notReady = await setup(page, "reminder-notready", false);
  await page.goto(`/trip/${notReady}/submit`);
  await expect(page.getByTestId("ready")).toContainText("還有");
  await expect(page.getByTestId("reminders")).toHaveCount(0);

  const policyId = await setup(page, "reminder", true);
  await page.goto(`/trip/${policyId}/submit`);
  const reminders = page.getByTestId("reminder");
  await expect(reminders.first()).toContainText("另行提供", { timeout: SLOW });
  const before = await reminders.count();

  // 重新檢查：再讀一次，內容沒變
  await page.getByTestId("reminders-recheck").click();
  await expect(reminders).toHaveCount(before);

  // 替其中一項待辦補上文件（假 6 號依檔名辨識、指定待辦）後重新檢查，少一條
  const trip = await (await page.request.get(`/api/v1/policies/${policyId}/trip`)).json();
  const todo = trip.incidents[0].todos.find((t: { requires_upload: boolean }) => t.requires_upload);
  const uploaded = await page.request.post(`/api/v1/policies/${policyId}/documents`, {
    multipart: {
      file: { name: "延誤證明.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4") },
      todo_id: todo.id,
    },
  });
  expect(uploaded.status()).toBe(201);
  await page.getByTestId("reminders-recheck").click();
  await expect(reminders).toHaveCount(before - 1);

  // 提醒不擋送件：照樣能選送件方式
  await expect(page.getByTestId("choose-send")).toBeEnabled();
  // 「回去處理」回行程頁
  await page.getByTestId("reminders-back").click();
  await expect(page).toHaveURL(new RegExp(`/trip/${policyId}$`));
});

test("附件合計超過 18 MB：預覽顯示紅字提示、不能寄出，請使用者檢查檔案大小", async ({ page }) => {
  const policyId = await setup(page, "toolarge", true);
  const trip = await (await page.request.get(`/api/v1/policies/${policyId}/trip`)).json();
  // 事故自己的與共同文件裡需要文件的待辦
  const needUpload = [...trip.shared_todos, ...trip.incidents[0].todos].filter(
    (t: { requires_upload: boolean }) => t.requires_upload,
  );
  expect(needUpload.length).toBeGreaterThanOrEqual(2);
  // 每份 9.9 MB（單檔上限 10 MB 以內），兩份合計超過 18 MB
  const big = (name: string) => ({
    name,
    mimeType: "application/pdf",
    buffer: Buffer.concat([Buffer.from("%PDF-1.4"), Buffer.alloc(9_900_000)]),
  });
  const names = ["延誤證明.pdf", "費用收據.pdf"]; // 假 6 號依檔名辨識：含「證明」「收據」才通過、才會附在信裡
  for (const [i, name] of names.entries()) {
    const res = await page.request.post(`/api/v1/policies/${policyId}/documents`, {
      multipart: { file: big(name), todo_id: needUpload[i].id },
    });
    expect(res.status(), await res.text()).toBe(201);
  }

  await page.goto(`/trip/${policyId}/submit`);
  await page.getByTestId("choose-send").click();

  const notice = page.getByTestId("attachments-too-large");
  await expect(notice).toContainText("附件合計超過 18 MB", { timeout: SLOW });
  await expect(notice).toContainText("請檢查檔案大小");
  await expect(page.getByTestId("submit")).toBeDisabled();
  await expect(page.getByText("讀取送件信中…")).toHaveCount(0);
  // 回去處理 → 行程頁；重新檢查仍是超過（檔案還在）
  await page.getByTestId("too-large-recheck").click();
  await expect(notice).toBeVisible();
  await page.getByTestId("too-large-back").click();
  await expect(page).toHaveURL(new RegExp(`/trip/${policyId}$`));
});
