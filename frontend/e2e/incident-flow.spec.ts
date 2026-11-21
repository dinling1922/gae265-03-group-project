import { expect, type Page, test } from "@playwright/test";

const SLOW = 30_000;

// 出事了的正式流程（/incident/*）：手選方案與保額 → 說明狀況 → 待辦 → 上傳文件 → 送件。
// 用後端目前設定的 agent 實作（預設是假實作）。需要測試登入與已載入的條款庫、方案資料（make seed）。
test("出事了：手選保單後走完 說明、待辦、上傳、送件", async ({ page, request }) => {
  const config = await (await request.get("/api/v1/auth/config")).json();
  test.skip(!config.dev_login, "後端沒有設 DEV_LOGIN_ENABLED=true");
  test.skip(!(await request.get("/api/v1/plans")).ok(), "條款庫沒有載入，先 make seed");

  await page.goto("/login?next=%2F");
  await page.getByLabel("Email").fill("e2e-incident-flow@example.com");
  await page.getByRole("button", { name: "測試登入" }).click();
  await expect(page).toHaveURL(/\/$/);

  // 入口：首頁的卡片進到 /incident/new
  await page.getByRole("link", { name: /出事了：幫我處理理賠/ }).click();
  await expect(page).toHaveURL(/\/incident\/new$/);

  // 1. 沒有保單檔：手動選擇公司、方案、保額、目的地與保險期間
  // （這個帳號重跑會累積保單，但 2-1 一進來一律是「上傳保單／手動選擇」兩個分頁，不用另外切）
  await page.getByTestId("mode-manual").click();
  await page.getByTestId("confirm-company").selectOption("富邦產物");
  await page.getByTestId("confirm-plan").selectOption("fubon_advanced");
  const sums = page.getByTestId("confirm-sum-insured");
  await expect(sums.locator("option")).not.toHaveCount(1);
  await sums.selectOption({ index: 1 });
  await page.getByTestId("confirm-destination").fill("日本");
  await page.getByLabel("開始日").fill("2026-12-01");
  await page.getByLabel("結束日").fill("2026-12-05");
  await page.getByTestId("confirm-continue").click();

  // 2. 說明發生什麼事，助理先問第一個問題
  await expect(page).toHaveURL(/\/incident\/new\/[0-9a-f-]{36}$/);
  await page.getByTestId("message").fill("班機延誤了六小時");
  await page.getByTestId("send").click();
  await expect(page).toHaveURL(/\/incident\/[0-9a-f-]+$/, { timeout: SLOW });
  // 開場白、旅客的第一句、5 號的第一個追問
  await expect(page.getByTestId("messages").locator("li")).toHaveCount(3, { timeout: SLOW });

  // 對話：問完才判斷出類型，補上待辦
  const reply = () =>
    page.waitForResponse((r) => r.url().endsWith("/messages") && r.request().method() === "POST", {
      timeout: SLOW,
    });
  let replied = reply();
  await page.getByTestId("message").fill("班機延誤了六小時");
  await page.getByTestId("send").click();
  await replied;
  await expect(page.getByTestId("messages").locator("li")).toHaveCount(5, { timeout: SLOW });
  replied = reply();
  await page.getByTestId("message").fill("昨天在東京成田機場");
  await page.getByTestId("send").click();
  await replied;
  // 問完先跳「確認目前的狀況」（2-2g），按確認才進行程頁（#345）
  await page.getByTestId("confirm-yes").click();
  await expect(page.getByTestId("todos").getByRole("checkbox").first()).toBeVisible({
    timeout: SLOW,
  });

  // 3. 上傳文件：假 6 號依檔名辨識通過並勾選待辦
  // 上傳入口就在理賠頁的待辦上方
  await page.getByTestId("doc-file").setInputFiles({
    name: "延誤證明.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from("%PDF-1.4"),
  });
  // 選好檔案就直接上傳辨識（沒有另外的「上傳」按鈕）
  await expect(page.getByTestId("upload-summary")).toContainText("這次上傳 1 份", {
    timeout: SLOW,
  });
  await expect(page.getByTestId("upload-summary")).toContainText("1 份已自動打勾");
  await page.getByTestId("upload-detail-toggle").click();
  await expect(page.getByTestId("upload-progress")).toContainText("符合規則，已自動打勾");

  // 4. 送件：待辦沒完成前不能送
  // 問完事故會導到行程頁（網址是 /trip/<保單 id>?incident=<事件 id>），送件頁直接前往
  await expect(page).toHaveURL(/\/trip\/[0-9a-f-]{36}\?incident=[0-9a-f-]{36}$/);
  const policyId = new URL(page.url()).pathname.split("/")[2];
  await page.goto(`/trip/${policyId}/submit`);
  await expect(page.getByTestId("ready")).toContainText("還有");
  await expect(page.getByTestId("choose-send")).toBeDisabled();
  await expect(page.getByTestId("choose-self")).toBeDisabled();

  // 回到待辦把剩下的自己勾完，就能送件
  await page.goto(`/trip/${policyId}`);
  const boxes = page.getByTestId("todos").getByRole("checkbox");
  await expect(boxes.first()).toBeVisible();
  for (const box of await boxes.all()) {
    if (!(await box.isChecked()) && (await box.isEnabled())) {
      const patched = page.waitForResponse((r) => r.request().method() === "PATCH");
      // 勾選要等後端回應才會變，所以用 click 再等回應，不用 check()
      await box.click();
      // 需上傳但還沒有文件的待辦，勾之前會先確認（會標「未附文件」）
      const confirm = page.getByTestId("tick-confirm");
      if (await confirm.isVisible()) await confirm.click();
      await patched;
    }
  }
  // 理賠頁沒有舊的步驟列：待辦都完成後，送件區出現「文件已備齊」與「送件」
  await expect(page.getByTestId("send-box")).toContainText("文件已備齊");
  await page.getByTestId("go-submit").click();
  await expect(page.getByTestId("ready")).toContainText("可以送件");

  // 先選「好險有你小助理幫我送出」：預覽信、填收件者，寄出後結案（測試登入沒有 Google 授權，不問授權）
  await page.getByTestId("choose-send").click();
  await expect(page.getByTestId("letter-body")).toBeVisible({ timeout: SLOW });
  await page.getByTestId("letter-recipient").fill("agent@example.com");
  await page.getByTestId("submit").click();
  // 寄出前再確認一次：寄出後這筆理賠即結案
  await expect(page.getByTestId("send-confirm-dialog")).toContainText("即結案");
  await page.getByTestId("send-confirm").click();
  await expect(page.getByTestId("closed")).toContainText(
    "已替你送件並於你的 Gmail 寄件備份留存紀錄",
    {
      timeout: SLOW,
    },
  );
  // 跳出視窗說明信在 Gmail 的寄件備份，關掉再繼續
  await expect(page.getByTestId("gmail-result-dialog")).toContainText("寄件備份");
  await page.getByRole("button", { name: "關閉" }).click();

  // 結案後鎖住：不能再傳訊息或改待辦
  await expect(page.getByTestId("closed-banner")).toBeVisible();
  await page.getByRole("link", { name: "回行程頁" }).click();
  // 行程頁：標籤顯示已結案，沒有問答（沒有提問列），待辦與送件區只能看
  await expect(page.getByText("已結案", { exact: true })).toBeVisible();
  await expect(page.getByTestId("message")).toHaveCount(0);
  await expect(page.getByTestId("send-closed")).toHaveText(
    "已替你送件並於你的 Gmail 寄件備份留存紀錄，文件也已存在你的 Google Drive。",
  );
  await expect(page.getByTestId("todos").getByRole("checkbox").first()).toBeDisabled();
});

/** 用 API 快速備好一個待辦全完成、可以送件的事件，回傳事件編號。 */
async function readyIncident(page: Page): Promise<string> {
  const api = page.request;
  const options = await (await api.get("/api/v1/plans/fubon_advanced/sum-insured-options")).json();
  const policy = await (
    await api.post("/api/v1/policies/manual", {
      data: { plan_id: "fubon_advanced", sum_insured: options.sum_insured[0] },
    })
  ).json();
  const created = await (
    await api.post(`/api/v1/policies/${policy.id}/incidents`, {
      data: { description: "班機延誤了六小時" },
    })
  ).json();
  const id: string = created.incident.incident_id;
  // 一直回答到 5 號問完（假 5 號最多問幾題由它決定）
  for (let i = 0; i < 15; i++) {
    const res = await api.post(`/api/v1/incidents/${id}/messages`, {
      data: { content: "不知道" },
    });
    const reply = await res.json();
    // 準備資料時伺服器回了錯誤：直接把狀態碼與內容丟出來，才看得到真正原因
    if (!reply.reply) throw new Error(`POST messages 回 ${res.status()}：${JSON.stringify(reply)}`);
    if (reply.reply.finished) break;
  }
  // 待辦全部完成：需上傳的靠假 6 號辨識，其他自己勾
  for (let i = 0; i < 20; i++) {
    const ctx = await (await api.get(`/api/v1/incidents/${id}`)).json();
    const undone = ctx.todos.filter(
      (t: { is_done: boolean; status: string }) => !t.is_done && t.status !== "not_applicable",
    );
    if (undone.length === 0) break;
    if (undone[0].requires_upload) {
      await api.post(`/api/v1/incidents/${id}/documents`, {
        multipart: {
          file: { name: "證明.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4") },
        },
      });
    } else {
      await api.patch(`/api/v1/incidents/${id}/todos/${undone[0].id}`, {
        data: { is_done: true },
      });
    }
  }

  return id;
}

// 送件的另一條路：選「我自己處理就好」、不建草稿，直接結案。事件用 API 快速備好（待辦全完成）。
test("出事了：自己處理、不建草稿，直接結案", async ({ page, request }) => {
  const config = await (await request.get("/api/v1/auth/config")).json();
  test.skip(!config.dev_login, "後端沒有設 DEV_LOGIN_ENABLED=true");
  test.skip(!(await request.get("/api/v1/plans")).ok(), "條款庫沒有載入，先 make seed");

  await page.goto("/login?next=%2F");
  await page.getByLabel("Email").fill("e2e-incident-self@example.com");
  await page.getByRole("button", { name: "測試登入" }).click();
  await expect(page).toHaveURL(/\/$/);

  const id = await readyIncident(page);

  await page.goto(`/incident/${id}/submit`);
  await expect(page.getByTestId("ready")).toContainText("可以送件", { timeout: SLOW });
  await page.getByTestId("choose-self").click();
  // 先問要不要建草稿，並說明不論選什麼都會結案；選取消就不建立
  await expect(page.getByTestId("draft-question")).toContainText("就會結案");
  await page.getByRole("button", { name: "取消" }).click();
  await expect(page.getByTestId("closed")).toContainText("未在你的 Gmail 建立草稿", {
    timeout: SLOW,
  });
  await expect(page.getByTestId("closed-banner")).toBeVisible();
});

// 寄出失敗時（7-2：附件在 Drive 的垃圾桶），說明放在預覽信那一區、寄出按鈕旁，列出是哪些附件並附上處理的入口。
// 測試登入沒有 Google 授權，用 page.route 換掉送件 API 的回應，模擬 7 號回 failed。
test("出事了：寄出失敗時在預覽信頁說明是哪些附件、怎麼處理", async ({ page, request }) => {
  const config = await (await request.get("/api/v1/auth/config")).json();
  test.skip(!config.dev_login, "後端沒有設 DEV_LOGIN_ENABLED=true");
  test.skip(!(await request.get("/api/v1/plans")).ok(), "條款庫沒有載入，先 make seed");

  await page.goto("/login?next=%2F");
  await page.getByLabel("Email").fill("e2e-incident-failed@example.com");
  await page.getByRole("button", { name: "測試登入" }).click();
  await expect(page).toHaveURL(/\/$/);
  const id = await readyIncident(page);

  // 7 號的寫法：一句說明加上一個檔案一行；永久刪除與在垃圾桶兩種都有
  const reason = [
    "Google Drive 上找不到下面 1 個附件，請重新上傳：",
    "・理賠申請書_1.pdf",
    "下面 1 個附件在 Google Drive 的垃圾桶裡，請到 Google Drive 的垃圾桶還原後再試一次：",
    "・班機延誤_機票及登機證_1.pdf",
  ].join("\n");
  await page.route("**/api/v1/incidents/*/submission", (route) =>
    route.fulfill({
      status: 201,
      json: {
        id: "00000000-0000-0000-0000-000000000000",
        status: "failed",
        external_ref: null,
        reason,
      },
    }),
  );

  await page.goto(`/incident/${id}/submit`);
  await expect(page.getByTestId("ready")).toContainText("可以送件", { timeout: SLOW });
  await page.getByTestId("choose-send").click();
  await expect(page.getByTestId("letter-body")).toBeVisible({ timeout: SLOW });
  await page.getByTestId("letter-recipient").fill("agent@example.com");
  await page.getByTestId("submit").click();
  await page.getByTestId("send-confirm").click();

  const notice = page.getByTestId("preview").getByTestId("submission");
  await expect(notice).toContainText("班機延誤_機票及登機證_1.pdf");
  // 一個檔案一行，看得出有幾個
  await expect(notice.getByTestId("submission-reason")).toHaveCSS("white-space", "pre-line");
  await expect(notice).toContainText("事件還沒結案");
  await expect(notice.getByTestId("open-drive-trash")).toHaveAttribute(
    "href",
    "https://drive.google.com/drive/trash",
  );
  // 留在預覽頁、沒有結案，可以處理後再寄一次
  await expect(page.getByTestId("submit")).toBeVisible();
  await expect(page.getByTestId("closed")).toHaveCount(0);

  // 從說明的連結到「上傳文件」處理附件，再回到送件頁：仍在預覽、填過的收件者還在
  await notice.getByTestId("go-upload").click();
  await expect(page).toHaveURL(/\/incident\/[0-9a-f-]{36}#upload-box$/);
  await page.goto(`/incident/${id}/submit`);
  await expect(page.getByTestId("letter-body")).toBeVisible({ timeout: SLOW });
  await expect(page.getByTestId("letter-recipient")).toHaveValue("agent@example.com");
});

// 契約 v2.8：預覽時 7 號在 Drive 上取不到的附件，預覽頁上方提示是哪些、怎麼處理；信裡只寫「另行提供」。
// 不還原也能寄出，按寄出時確認視窗再列一次（2026-11-21）。
// 測試登入沒有 Google 授權，用 page.route 在預覽的回應加上取不到的附件。
test("出事了：預覽時提示 Drive 上取不到的附件，可以重新檢查，也能不還原就寄出", async ({
  page,
  request,
}) => {
  const config = await (await request.get("/api/v1/auth/config")).json();
  test.skip(!config.dev_login, "後端沒有設 DEV_LOGIN_ENABLED=true");
  test.skip(!(await request.get("/api/v1/plans")).ok(), "條款庫沒有載入，先 make seed");

  await page.goto("/login?next=%2F");
  await page.getByLabel("Email").fill("e2e-incident-unavailable@example.com");
  await page.getByRole("button", { name: "測試登入" }).click();
  await expect(page).toHaveURL(/\/$/);
  const id = await readyIncident(page);

  let previews = 0;
  await page.route("**/api/v1/incidents/*/package", async (route) => {
    previews += 1;
    const response = await route.fetch();
    const pkg = await response.json();
    await route.fulfill({
      response,
      json: {
        ...pkg,
        // 需上傳的文件取不到、又沒有其他文件：7 號信上寫另行提供，不列為還缺
        unavailable_attachments: [
          {
            document_id: "00000000-0000-0000-0000-000000000001",
            file_name: "班機延誤_機票及登機證_1.pdf",
            reason: "trashed",
          },
          {
            document_id: "00000000-0000-0000-0000-000000000002",
            file_name: "理賠申請書_1.pdf",
            reason: "not_found",
          },
        ],
      },
    });
  });

  await page.goto(`/incident/${id}/submit`);
  await expect(page.getByTestId("ready")).toContainText("可以送件", { timeout: SLOW });
  await page.getByTestId("choose-send").click();

  const notice = page.getByTestId("unavailable-attachments");
  await expect(notice).toContainText("下面 2 個附件在 Google Drive 上找不到");
  await expect(notice.getByTestId("unavailable-attachment")).toHaveText([
    "・班機延誤_機票及登機證_1.pdf（在垃圾桶裡）",
    "・理賠申請書_1.pdf（已永久刪除）",
  ]);
  await expect(notice.getByTestId("unavailable-reason").first()).toHaveClass(/text-danger/);
  await expect(notice).toContainText("這些文件於 Google Drive 已找不到，請確認是否");
  await expect(notice.getByTestId("unavailable-open-trash")).toHaveAttribute(
    "href",
    "https://drive.google.com/drive/trash",
  );
  await expect(notice.getByTestId("unavailable-go-upload")).toHaveText(
    "到「上傳理賠文件」重新上傳",
  );
  await expect(notice).toContainText("不處理也能寄出");
  await expect(notice.getByTestId("unavailable-recheck")).toHaveCSS("cursor", "pointer");

  // 到垃圾桶還原或重新上傳之後按「重新檢查」：重新讀一次預覽
  const before = previews;
  await notice.getByTestId("unavailable-recheck").click();
  await expect.poll(() => previews, { timeout: SLOW }).toBe(before + 1);
  await expect(page.getByTestId("letter-body")).toBeVisible({ timeout: SLOW });

  // 不還原也能按寄出：先再讀一次送件包，確認視窗分別列出在垃圾桶裡與已永久刪除的文件
  await page.getByTestId("letter-recipient").fill("agent@example.com");
  await expect(page.getByTestId("submit")).toBeEnabled();
  const beforeSend = previews;
  await page.getByTestId("submit").click();
  await expect.poll(() => previews, { timeout: SLOW }).toBe(beforeSend + 1);
  const dialog = page.getByTestId("send-confirm-dialog");
  await expect(dialog.getByTestId("confirm-trashed")).toContainText("班機延誤_機票及登機證_1.pdf");
  await expect(dialog.getByTestId("confirm-not_found")).toContainText("理賠申請書_1.pdf");
  await expect(dialog.getByTestId("send-confirm")).toBeEnabled();
  // 攔截的回應可能還在向後端讀資料：測試結束前停掉，避免「Test ended」的錯誤
  await page.unrouteAll({ behavior: "ignoreErrors" });
});

// 從「我自己處理就好」建立草稿時，有文件在 Drive 上找不到：先重新檢查一次，跳視窗列出在垃圾桶與
// 已永久刪除的文件，按「確定建立草稿」才建（2026-11-21）。
// 測試登入不是 Google 帳號，用 page.route 模擬成 Google 登入、Drive 與 Gmail 都已授權。
test("出事了：建立草稿前有文件在 Drive 上找不到，先列出來再確定", async ({ page, request }) => {
  const config = await (await request.get("/api/v1/auth/config")).json();
  test.skip(!config.dev_login, "後端沒有設 DEV_LOGIN_ENABLED=true");
  test.skip(!(await request.get("/api/v1/plans")).ok(), "條款庫沒有載入，先 make seed");

  await page.goto("/login?next=%2F");
  await page.getByLabel("Email").fill("e2e-incident-draft-lost@example.com");
  await page.getByRole("button", { name: "測試登入" }).click();
  await expect(page).toHaveURL(/\/$/);
  const id = await readyIncident(page);

  await page.route("**/api/v1/auth/me", async (route) => {
    const response = await route.fetch();
    await route.fulfill({
      response,
      json: { ...(await response.json()), login_provider: "google" },
    });
  });
  await page.route("**/api/v1/auth/google/grant", (route) =>
    route.fulfill({ json: { status: "active", drive: true, gmail: true } }),
  );
  await page.route("**/api/v1/incidents/*/package", async (route) => {
    const response = await route.fetch();
    await route.fulfill({
      response,
      json: {
        ...(await response.json()),
        unavailable_attachments: [
          {
            document_id: "00000000-0000-0000-0000-000000000001",
            file_name: "班機延誤_機票及登機證_1.pdf",
            reason: "trashed",
          },
          {
            document_id: "00000000-0000-0000-0000-000000000002",
            file_name: "理賠申請書_1.pdf",
            reason: "not_found",
          },
        ],
      },
    });
  });
  let submissions = 0;
  await page.route("**/api/v1/incidents/*/submission", (route) => {
    submissions += 1;
    return route.fulfill({ status: 500, json: { detail: "測試不建草稿" } });
  });

  await page.goto(`/incident/${id}/submit`);
  await expect(page.getByTestId("ready")).toContainText("可以送件", { timeout: SLOW });
  await page.getByTestId("choose-self").click();
  await page.getByTestId("draft-question").getByRole("button", { name: "確定" }).click();

  const dialog = page.getByTestId("draft-confirm-dialog");
  await expect(dialog.getByTestId("confirm-trashed")).toContainText("班機延誤_機票及登機證_1.pdf", {
    timeout: SLOW,
  });
  await expect(dialog.getByTestId("confirm-not_found")).toContainText("理賠申請書_1.pdf");
  // 和預覽頁一樣附上處理的入口：垃圾桶、重新上傳、重新檢查
  await expect(dialog.getByTestId("draft-open-trash")).toHaveAttribute(
    "href",
    "https://drive.google.com/drive/trash",
  );
  await expect(dialog.getByTestId("draft-go-upload")).toHaveText("到「上傳理賠文件」重新上傳");
  await expect(dialog.getByTestId("draft-recheck")).toHaveCSS("cursor", "pointer");
  // 還沒確定前不建草稿；取消就留在詢問頁
  expect(submissions).toBe(0);
  await dialog.getByRole("button", { name: "取消" }).click();
  await expect(dialog).toHaveCount(0);
  expect(submissions).toBe(0);
  await expect(page.getByTestId("closed")).toHaveCount(0);
  // 攔截的回應可能還在向後端讀資料：測試結束前停掉，避免「Test ended」的錯誤
  await page.unrouteAll({ behavior: "ignoreErrors" });
});

// 按「好險有你小助理幫我送出」先授權 Drive、回到送件頁：停在詢問頁、顯示「已完成 Google Drive 授權」，
// 接著跳 Gmail 的說明視窗；Gmail 也授權回來後才進預覽（2026-11-21）。
// 測試登入不是 Google 帳號，用 page.route 模擬成 Google 登入，並把暫存設成剛從 Drive 授權回來。
test("出事了：看預覽前先授權 Drive 回來，停在詢問頁並接著問 Gmail", async ({ page, request }) => {
  const config = await (await request.get("/api/v1/auth/config")).json();
  test.skip(!config.dev_login, "後端沒有設 DEV_LOGIN_ENABLED=true");
  test.skip(!(await request.get("/api/v1/plans")).ok(), "條款庫沒有載入，先 make seed");

  await page.goto("/login?next=%2F");
  await page.getByLabel("Email").fill("e2e-incident-grant-steps@example.com");
  await page.getByRole("button", { name: "測試登入" }).click();
  await expect(page).toHaveURL(/\/$/);
  const id = await readyIncident(page);

  await page.route("**/api/v1/auth/me", async (route) => {
    const response = await route.fetch();
    await route.fulfill({
      response,
      json: { ...(await response.json()), login_provider: "google" },
    });
  });
  let gmail = false;
  await page.route("**/api/v1/auth/google/grant", (route) =>
    route.fulfill({ json: { status: "active", drive: true, gmail } }),
  );
  await page.evaluate((key) => {
    sessionStorage.setItem(
      key,
      JSON.stringify({ step: "ask", intent: "preview", purpose: "drive" }),
    );
  }, `incident-submit:${id}`);

  await page.goto(`/incident/${id}/submit?google_auth=granted`);
  await expect(page.getByTestId("google-auth-result")).toHaveText("已完成 Google Drive 授權。");
  await expect(page.getByTestId("ask")).toBeVisible();
  const consent = page.getByTestId("google-consent");
  await expect(consent).toContainText("用你的 Gmail 寄出送件信", { timeout: SLOW });
  await expect(page.getByTestId("preview")).toHaveCount(0);

  // Gmail 也授權回來：進預覽
  gmail = true;
  await page.goto(`/incident/${id}/submit?google_auth=granted`);
  await expect(page.getByTestId("google-auth-result")).toHaveText("已完成 Google Gmail 授權。");
  await expect(page.getByTestId("preview")).toBeVisible({ timeout: SLOW });
});

// 「我自己處理就好 → 建立草稿」先授權 Drive 回來：不再跳「需要幫你在 Gmail 建立草稿嗎？」，
// 直接接著問 Gmail；Gmail 也授權回來才再問一次草稿的問題，確認後才建（2026-11-21）。
test("出事了：建立草稿前先授權 Drive 回來，不再重問草稿、直接問 Gmail", async ({
  page,
  request,
}) => {
  const config = await (await request.get("/api/v1/auth/config")).json();
  test.skip(!config.dev_login, "後端沒有設 DEV_LOGIN_ENABLED=true");
  test.skip(!(await request.get("/api/v1/plans")).ok(), "條款庫沒有載入，先 make seed");

  await page.goto("/login?next=%2F");
  await page.getByLabel("Email").fill("e2e-incident-draft-grant-steps@example.com");
  await page.getByRole("button", { name: "測試登入" }).click();
  await expect(page).toHaveURL(/\/$/);
  const id = await readyIncident(page);

  await page.route("**/api/v1/auth/me", async (route) => {
    const response = await route.fetch();
    await route.fulfill({
      response,
      json: { ...(await response.json()), login_provider: "google" },
    });
  });
  let gmail = false;
  await page.route("**/api/v1/auth/google/grant", (route) =>
    route.fulfill({ json: { status: "active", drive: true, gmail } }),
  );
  await page.evaluate((key) => {
    sessionStorage.setItem(key, JSON.stringify({ step: "ask", intent: "draft", purpose: "drive" }));
  }, `incident-submit:${id}`);

  await page.goto(`/incident/${id}/submit?google_auth=granted`);
  await expect(page.getByTestId("google-auth-result")).toHaveText("已完成 Google Drive 授權。");
  await expect(page.getByTestId("google-consent")).toContainText("用你的 Gmail 建立草稿", {
    timeout: SLOW,
  });
  await expect(page.getByTestId("draft-question")).toHaveCount(0);

  gmail = true;
  await page.goto(`/incident/${id}/submit?google_auth=granted`);
  await expect(page.getByTestId("google-auth-result")).toHaveText("已完成 Google Gmail 授權。");
  await expect(page.getByTestId("draft-question")).toBeVisible({ timeout: SLOW });
  await expect(page.getByTestId("google-consent")).toHaveCount(0);
});
