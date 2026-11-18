#!/usr/bin/env node
// 用 Playwright 跑一段情境並截圖或錄影，給 PR 的 Evidence 用。
//
// 用法：node capture.cjs <scenario.cjs> --base <前端網址> --out <資料夾> [--name <檔名>] [--login <email>] [--video]
//   scenario.cjs 匯出 async ({ page, base, shot, name }) => {}
//   - shot(name, options)：把目前畫面存成 <out>/<name>.png（options 交給 page.screenshot，例如 { clip }、{ fullPage: true }）
//   - name：--name 的值，前後對照時分別給 before、after
//   --login：先用開發環境的「測試登入」登入，再開始情境（錄影不會從登入頁開始）
//   --video：整段錄影，結束後用 ffmpeg 轉成 <out>/<name>.mp4（H.264，GitHub 收得下）
// 視窗固定 1280x900，前後對照的兩張圖才對得齊。
const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "../../../..");
const { chromium } = require(path.join(ROOT, "frontend/node_modules/@playwright/test"));

const args = process.argv.slice(2);
const opt = (key, fallback) => {
  const i = args.indexOf(`--${key}`);
  return i === -1 ? fallback : args[i + 1];
};
const scenarioPath = args[0];
const base = opt("base", "http://127.0.0.1:5174");
const out = path.resolve(opt("out", "."));
const name = opt("name", "capture");
const email = opt("login", null);
const video = args.includes("--video");
if (!scenarioPath) {
  console.error(
    "用法：node capture.cjs <scenario.cjs> --base <網址> --out <資料夾> [--name <檔名>] [--login <email>] [--video]",
  );
  process.exit(1);
}
fs.mkdirSync(out, { recursive: true });

const SIZE = { width: 1280, height: 900 };

// 在不錄影的 context 登入，把 cookie 交給正式的 context
async function loginState(browser) {
  if (!email) return undefined;
  const ctx = await browser.newContext({ viewport: SIZE });
  const page = await ctx.newPage();
  await page.goto(`${base}/login`);
  await page.getByLabel("Email").fill(email);
  await page.getByRole("button", { name: "測試登入" }).click();
  await page.waitForURL((u) => !u.pathname.startsWith("/login"));
  const state = await ctx.storageState();
  await ctx.close();
  return state;
}

(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: SIZE,
    storageState: await loginState(browser),
    ...(video ? { recordVideo: { dir: out, size: SIZE } } : {}),
  });
  const page = await context.newPage();
  const outputs = [];
  const shot = async (n, options = {}) => {
    const file = path.join(out, `${n}.png`);
    await page.screenshot({ path: file, ...options });
    outputs.push(file);
  };

  const scenario = require(path.resolve(scenarioPath));
  try {
    await scenario({ page, base, shot, name });
  } finally {
    const rec = video ? page.video() : null;
    await context.close();
    await browser.close();
    if (rec) {
      const webm = await rec.path();
      const mp4 = path.join(out, `${name}.mp4`);
      const r = spawnSync(
        "ffmpeg",
        [
          ...["-v", "error", "-y", "-i", webm],
          ...["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "23", "-movflags", "+faststart", "-an"],
          mp4,
        ],
        { stdio: "inherit" },
      );
      if (r.status !== 0) throw new Error("ffmpeg 轉檔失敗（brew install ffmpeg）");
      fs.rmSync(webm);
      outputs.push(mp4);
    }
    for (const f of outputs) console.log(f);
  }
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
