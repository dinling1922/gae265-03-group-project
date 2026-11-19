---
name: pr-media
description: 把前端改動錄成 MP4 或截成前後對照圖，透過 Claude in Chrome 上傳到 GitHub，並嵌進 PR 描述的 Evidence。有流程、動畫、載入狀態或多步驟互動的改動錄影片；單一畫面的外觀改動用改動前後兩張截圖對照。只要 PR 動到 frontend/ 的畫面、使用者要在 PR 放截圖／影片／示範，或要開前端 PR、補 PR 的 Evidence，就使用這個 skill，使用者沒說「影片」或「截圖」也一樣。
---

# PR 媒體：錄影片或前後對照截圖，嵌進 PR

`gh` 沒有上傳附件的指令，所以影片和圖片透過使用者已登入 GitHub 的 Chrome（Claude in Chrome）上傳：在 PR 頁的留言框「貼上」檔案，讓 GitHub 走自己的上傳流程產生 `https://github.com/user-attachments/assets/...` 連結，拿到連結就清空留言框，**不送出留言**，再用 `gh pr edit` 把連結放進 PR 描述。

## 1. 決定要錄影片還是截圖

- **影片**：使用者要經過幾個步驟才看得到效果：動畫、載入中的狀態、換頁、送出後的回饋。截圖會漏掉「怎麼變過去的」。
- **前後對照截圖**：同一個畫面的樣子變了：排版、顏色、文案、元件換掉。兩張圖並排最快看出差別。
- 兩種都有就都做。

前後對照時，兩張圖的視窗大小、捲動位置、資料都要一樣，差別才會只來自程式碼。

## 2. 起服務

**改動後**：在 PR 的分支跑 `make dev-worktree`（背景執行，看到「worktree 的服務就緒」再繼續），前端是 `http://127.0.0.1:5174`。服務沒開時照 `AGENTS.md` 的「Git worktree」處理。

**改動前**（只有前後對照才要）：把 PR 的 base 分支開成另一個 worktree，放在 scratchpad 這類暫存資料夾，用另一組埠起服務：

```bash
git fetch -q origin
git worktree add <scratchpad>/pr-media-base origin/main
make -C <scratchpad>/pr-media-base worktree-setup
DEV_BACKEND_PORT=8002 DEV_FRONT_PORT=5175 make -C <scratchpad>/pr-media-base dev-worktree   # 背景執行
```

base worktree 放在暫存資料夾，用完就移除，不會跟主目錄或其他 worktree 混在一起。兩組服務連同一個開發資料庫，同一個測試帳號可以在兩邊各拍一次。

## 3. 寫情境、截圖或錄影

在 scratchpad 寫一個情境檔，用 [scripts/capture.cjs](scripts/capture.cjs) 執行：

```js
// scenario.cjs：匯出 async ({ page, base, shot, name }) => {}
module.exports = async ({ page, base, shot, name }) => {
  await page.goto(`${base}/compare/new`);
  // ……操作到要展示的畫面
  const box = page.locator("main > div").first(); // 要對照的區塊
  await box.evaluate((el) => el.scrollIntoView({ block: "start" }));
  await shot(name, { clip: await box.boundingBox() });
};
```

```bash
mise exec -- node .claude/skills/pr-media/scripts/capture.cjs <scenario.cjs> \
  --base http://127.0.0.1:5174 --out <scratchpad>/pr-<號碼> --name after --login pr-demo@example.com [--video]
```

- **截圖**：同一個情境跑兩次，`--base` 分別是 5175 + `--name before`、5174 + `--name after`。用 `clip` 只截有改動的區塊，兩張圖會對得齊、也比整頁好讀。
- **影片**：加 `--video`，結束後自動轉成 `<name>.mp4`。`--login` 會在開始錄影前先登入，影片不會從登入頁開始。
- **結果要可以重現**：LLM 等外部回應用 `page.route` 固定成一份假資料；想讓載入動畫被看見，就在 route 裡延遲幾秒再回應。
- **日期不要寫死**：範例日程的日期會跟著今天變，假資料裡的日期從 API 讀實際的資料來算。
- **只用測試資料**：測試帳號、範例日程，畫面裡不能出現真實使用者的資料。

上傳前先看過產出：用 Read 看截圖；影片用 `ffmpeg -v error -ss <秒> -i x.mp4 -frames:v 1 frame.png` 抽幾格來看，確認拍到的是要展示的狀態。

## 4. 用 Claude in Chrome 上傳

載入 Claude in Chrome 的工具（`tabs_context_mcp`、`tabs_create_mcp`、`navigate`、`find`、`file_upload`、`javascript_tool`、`tabs_close_mcp`），開一個新分頁到 PR 頁，然後：

1. **放暫用的檔案欄位**。`navigate` 回來時頁面可能還沒載完，先等留言框出現：

   ```js
   for (let t = 0; t < 20 && !document.querySelector('textarea[placeholder="Leave a comment"]'); t++) {
     await new Promise((r) => setTimeout(r, 500));
   }
   const i = document.createElement("input");
   i.type = "file";
   i.id = "pr-media-carrier";
   i.setAttribute("aria-label", "pr media carrier input");
   document.body.append(i);
   ```

   再用 `find` 找「pr media carrier input」拿到 ref。GitHub 的留言框沒有可以直接用的檔案欄位，而「Paste, drop, or click to add files」會跳出系統的檔案選擇視窗，擴充功能操作不了，所以改用這個暫用欄位。

2. **放進檔案並貼到留言框**。`file_upload` 一次可以放多個檔案（合計 10MB 以內），一起貼上：

   ```js
   const files = [...document.getElementById("pr-media-carrier").files];
   const ta = document.querySelector('textarea[placeholder="Leave a comment"]');
   ta.focus();
   const dt = new DataTransfer();
   files.forEach((f) => dt.items.add(f));
   ta.dispatchEvent(new ClipboardEvent("paste", { clipboardData: dt, bubbles: true, cancelable: true }));
   const done = () => (ta.value.match(/user-attachments\/assets\/[\w-]+/g) || []).length >= files.length;
   for (let t = 0; t < 30 && !done(); t++) await new Promise((r) => setTimeout(r, 1000));
   ta.value;
   ```

   回傳值依檔案順序列出連結：圖片是 `<img width=… alt="檔名" src="https://github.com/user-attachments/assets/…" />`，影片是一行 `https://github.com/user-attachments/assets/…`。

3. **清空留言框、移除暫用欄位**：

   ```js
   document.getElementById("pr-media-carrier").remove();
   const ta = document.querySelector('textarea[placeholder="Leave a comment"]');
   ta.focus();
   ta.select();
   document.execCommand("delete");
   ta.value;
   ```

   回傳空字串才算清乾淨。留言框沒清空，草稿會留在頁面上，被誤送出去就會變成一則留言。

## 5. 放進 PR 描述

```bash
gh pr view <號碼> --json body --jq .body > <scratchpad>/pr-<號碼>/body.md
# 用 Edit 改 Evidence 段落
gh pr edit <號碼> --body-file <scratchpad>/pr-<號碼>/body.md
```

Evidence 的寫法（照 `pr` skill 的範本）：

```markdown
- **Before / After：** <兩張圖拍的是什麼狀態、看哪裡>

| Before | After |
| --- | --- |
| <img width="464" alt="before" src="https://github.com/user-attachments/assets/…" /> | <img width="464" alt="after" src="https://github.com/user-attachments/assets/…" /> |

- **示範：** <影片裡依序發生什麼>

https://github.com/user-attachments/assets/…
```

- 圖片沿用上傳回傳的 `<img>`，把 `width` 改成 464 左右，表格兩欄才放得下。
- 影片的連結自己一行，GitHub 才會顯示成播放器。

改的是使用者指定的那個 PR；沒指定時用目前分支的 PR，並在回覆裡說明改了哪一個。改完在同一個分頁重新整理 PR 頁，確認圖片載得出來（`img.complete && img.naturalWidth > 0`）、影片有 `<video>`，再關掉分頁。

## 6. 收尾

- 停掉這次起的 `dev-worktree`。
- 有開 base worktree 就停掉它的服務，再 `git worktree remove <scratchpad>/pr-media-base`。
- 截圖、影片、情境檔都留在 scratchpad，不 commit 進 repo。
