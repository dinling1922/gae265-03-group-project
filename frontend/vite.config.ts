import { readdirSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import react from "@vitejs/plugin-react";
import { msw } from "msw/vite";
import { defineConfig, type Plugin } from "vite";

// 只從 shell 或 compose 的環境變數讀（Vite 不會把 .env 載進 config）；沒設時連本機的後端
const apiProxyTarget = process.env.API_PROXY_TARGET ?? "http://localhost:8000";

/**
 * 保單縮圖用的 pdf.js 要讀 CMap 與標準字型（保單的中文字型常常沒嵌進檔案）：
 * dev 由 middleware 提供、build 時輸出成 `/pdfjs/cmaps/`、`/pdfjs/standard_fonts/`，使用者畫縮圖時才會抓（#444）。
 */
function pdfjsAssets(): Plugin {
  const root = path.dirname(createRequire(import.meta.url).resolve("pdfjs-dist/package.json"));
  const dirs = ["cmaps", "standard_fonts"];
  return {
    name: "pdfjs-assets",
    configureServer(server) {
      server.middlewares.use("/pdfjs", (req, res, next) => {
        const rel = decodeURIComponent((req.url ?? "").split("?")[0] ?? "").replace(/^\//, "");
        const [dir, file] = rel.split("/");
        if (!dir || !file || !dirs.includes(dir) || file.includes("..")) return next();
        try {
          res.setHeader("Content-Type", "application/octet-stream");
          res.end(readFileSync(path.join(root, dir, file)));
        } catch {
          next();
        }
      });
    },
    generateBundle() {
      for (const dir of dirs) {
        for (const file of readdirSync(path.join(root, dir))) {
          this.emitFile({
            type: "asset",
            fileName: `pdfjs/${dir}/${file}`,
            source: readFileSync(path.join(root, dir, file)),
          });
        }
      }
    },
  };
}

export default defineConfig({
  // 預設放在 node_modules/.vite；主目錄的那個資料夾是 docker 容器用 root 建的，本機另起 vite 寫不進去，
  // 所以 scripts/e2e-fake.sh 會用 VITE_CACHE_DIR 改放到暫存目錄（沒設時行為不變）
  cacheDir: process.env.VITE_CACHE_DIR || undefined,
  // tanstackRouter 要排在 react 之前：先產生路由樹，再交給 React 轉譯
  plugins: [
    tanstackRouter({ target: "react", autoCodeSplitting: true }),
    react(),
    tailwindcss(),
    // 假資料模式用：dev server 提供 MSW 的 service worker；build 不輸出 worker
    msw(),
    pdfjsAssets(),
  ],
  server: {
    host: true,
    port: 5173,
    strictPort: true,
    // 用 ngrok 分享開發環境時，Vite 才不會擋掉 tunnel 的網域
    allowedHosts: [".ngrok-free.app", ".ngrok-free.dev", ".ngrok.app", ".ngrok.dev"],
    proxy: {
      "/api": apiProxyTarget,
    },
  },
});
