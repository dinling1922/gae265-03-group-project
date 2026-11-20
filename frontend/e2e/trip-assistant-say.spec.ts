import { expect, type Page, test } from "@playwright/test";
import { login } from "./helpers";

// 小助理幫你說（#568、#569）：行程頁「事故當下」卡片右上角的按鈕，打開後把要向當地單位拿的文件
// 翻成日文＋英文＋中文小字。事故用 API 快速備好（假 5 號問到結束）；翻譯員要打真的 LLM，
// 所以翻譯的 API 在這裡換成固定的回應（真實翻譯員另外在本機實測）。需要 make seed。

const SAY = {
  language: "ja",
  request: {
    local: "旅行保険の請求のため、以下の書類を発行していただけますでしょうか。",
    english:
      "I am applying for a travel insurance claim. Could you please issue the following documents?",
    chinese: "申請旅遊保險理賠，需要以下文件，麻煩您開立。",
  },
  sections: [
    {
      unit: { local: "航空会社", english: "Airline", chinese: "航空公司" },
      documents: [
        {
          code: "D01",
          name: {
            local: "遅延を証明する書類",
            english: "Certificate of flight delay",
            chinese: "航空業者出具、載有班機延誤期間或取消的證明",
          },
          items: [
            {
              local: "予定と実際の出発時刻",
              english: "Scheduled and actual departure times",
              chinese: "原定與實際起飛時間（或延誤時數）；班機取消時載明取消",
            },
          ],
        },
      ],
    },
  ],
  thanks: {
    local: "ご協力ありがとうございます。",
    english: "Thank you for your help.",
    chinese: "謝謝您的協助。",
  },
};

async function setup(page: Page, name: string, description: string) {
  await login(page, `e2e-say-${name}-${Date.now()}@example.com`);
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
  const created = await (
    await api.post(`/api/v1/policies/${policy.id}/incidents`, { data: { description } })
  ).json();
  const id: string = created.incident.incident_id;
  for (let i = 0; i < 15; i++) {
    const reply = await (
      await api.post(`/api/v1/incidents/${id}/messages`, { data: { content: description } })
    ).json();
    if (reply.reply.finished) break;
  }
  return policy.id as string;
}

/** 翻譯的 API 換成固定回應，並先等一下，才看得到「正在翻譯…」。 */
async function fakeTranslation(page: Page) {
  await page.route("**/api/v1/policies/*/assistant-say", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 600));
    await route.fulfill({ json: SAY });
  });
}

test.beforeEach(async ({ request }) => {
  const config = await (await request.get("/api/v1/auth/config")).json();
  test.skip(!config.dev_login, "後端沒有設 DEV_LOGIN_ENABLED=true");
  test.skip(!(await request.get("/api/v1/plans")).ok(), "條款庫沒有載入，先 make seed");
});

test("有事故當下要拿的文件才看得到「小助理幫你說」按鈕", async ({ page }) => {
  const withDocs = await setup(page, "button", "班機延誤了六小時");
  await page.goto(`/trip/${withDocs}`);
  await expect(page.getByTestId("assistant-say-open")).toBeVisible();

  // 不符保障的事故：待辦反灰、不需要準備，沒有要向當地單位拿的文件
  const without = await setup(page, "nobutton", "班機延誤了但是不理賠");
  await page.goto(`/trip/${without}`);
  await expect(page.getByTestId("not-covered")).toBeVisible();
  await expect(page.getByTestId("assistant-say-open")).toHaveCount(0);
});

test("按下按鈕先看到「正在翻譯…」，再看到日文、英文、中文，依單位分段", async ({ page }) => {
  const policyId = await setup(page, "dialog", "班機延誤了六小時");
  await fakeTranslation(page);
  await page.goto(`/trip/${policyId}`);

  await page.getByTestId("assistant-say-open").click();

  const dialog = page.getByRole("dialog", { name: "小助理幫你說" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText("把手機拿給櫃檯人員看，對方就知道要開哪些文件。")).toBeVisible();
  await expect(dialog.getByRole("status")).toHaveText("正在翻譯…");
  await expect(dialog.getByRole("status")).toHaveCount(0);

  // 請求：日文、英文、中文小字；單位的標題是「當地語言 / 英文（中文）」
  await expect(dialog.getByText(SAY.request.local)).toBeVisible();
  await expect(dialog.getByText(SAY.request.english)).toBeVisible();
  await expect(dialog.getByText(SAY.request.chinese)).toBeVisible();
  await expect(
    dialog.getByRole("heading", { name: "航空会社 / Airline（航空公司）" }),
  ).toBeVisible();
  const section = dialog.locator("section").first();
  await expect(section.getByText("遅延を証明する書類")).toBeVisible();
  await expect(section.getByText("Certificate of flight delay")).toBeVisible();
  await expect(
    section.getByText("原定與實際起飛時間（或延誤時數）；班機取消時載明取消"),
  ).toBeVisible();
  await expect(dialog.getByText(SAY.thanks.local)).toBeVisible();
});

test("按「關閉」回到行程頁", async ({ page }) => {
  const policyId = await setup(page, "close", "班機延誤了六小時");
  await fakeTranslation(page);
  await page.goto(`/trip/${policyId}`);
  await page.getByTestId("assistant-say-open").click();
  const dialog = page.getByRole("dialog", { name: "小助理幫你說" });
  await expect(dialog.getByText(SAY.thanks.local)).toBeVisible();

  await dialog.getByRole("button", { name: "關閉" }).click();

  await expect(dialog).toHaveCount(0);
  await expect(page.getByTestId("trip-title")).toBeVisible();
  await expect(page.getByTestId("assistant-say-open")).toBeVisible();
});
