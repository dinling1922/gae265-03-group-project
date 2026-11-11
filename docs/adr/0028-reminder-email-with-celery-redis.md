---
status: accepted
---

# 提醒信用 Celery＋Redis 排程，Flower 監控；由系統通知信箱經 Azure 寄出

規格書 v3.7 的 4.9（R9）要求：行程還缺需上傳的理賠文件時，系統定時寄**提醒信**給使用者。規則是缺文件滿 24 小時寄第一封，之後每 3 天寄一封，最多 3 封；新狀況新增需上傳的待辦時重新計時；需上傳的待辦都完成或行程結案就停止。這是第一個「沒有人發出請求、也要按時執行」的工作，FastAPI 的 `BackgroundTasks` 做不到。因此加入 Celery worker、Celery beat 與 Redis，並用 Flower 監控，取代 [ADR-0003](0003-no-background-worker-yet.md)「第一版不放背景工作佇列」。11/4 會議已傾向這個做法。

## 決定

- **服務**：Redis 當 broker。worker 與 beat 用 backend 的同一個 image，套件仍是 `app`。Flower 在開發環境開 5555 埠；正式環境只綁 `127.0.0.1`，要看時用 SSH tunnel（`ssh -L 5555:localhost:5555 <VM>`），平常可以不開。
- **狀態存在 Postgres，不存在 Redis**：寄到第幾封、何時開始計時都記在 `policy.reminder_started_at`、`reminder_count`、`last_reminded_at`。Redis 只是佇列，資料遺失或重啟都不影響規則。不存工作結果（`ignore_result`），排程固定寫在程式裡，不用資料庫型的 beat 排程。
- **流程由程式決定**（[ADR-0004](0004-llm-never-produces-numbers.md)）：
  - beat 每隔固定時間（預設 10 分鐘）排一次掃描工作，找出到期的保單，每張保單各排一個寄信工作。
  - 寄信工作先鎖住保單（`FOR UPDATE`）並重新檢查是否到期，寄出成功後才把 `reminder_count` 加 1。SMTP 失敗時自動重試。Celery 重複派發時，第二個工作看到計數已經加過，就不會再寄。
  - 每封信的 Message-ID 固定由「保單＋這一輪的計時起點＋第幾封」組成，重寄時 ID 相同。收件者都是 Google 帳號，Gmail 通常把相同 Message-ID 的信當成同一封；這是常見行為、不是保證，Azure 也可能改寫這個 header，沒效果時只是回到可能重複一封的情況。
  - 不做寄送紀錄表：它擋不住「寄出後、寫入前當機」的重複，只能讓那一封停在「寄送中」，重寄或放棄仍要二選一。
  - 「這張保單該不該寄、下一封何時寄」寫成不碰資料庫的純函式，規則測試都測它。
- **還缺文件的判斷**：只看 `requires_upload` 而且 `applicable` 的待辦；`status` 不是 `done` 的就算還缺。使用者自行勾選的未附文件待辦（`without_document`）也算完成。標為不適用的待辦不算還缺，也不列進信裡，和送件前的檢查一致。
- **計時**：平台產生待辦、或新狀況新增需上傳的待辦時，設定 `reminder_started_at` 並把 `reminder_count` 歸零；備齊或結案時清空。第一封在計時起點後 24 小時；之後每一封都從**上一封寄出的時間**（`last_reminded_at`）起算 3 天，不從計時起點推算，停擺很久後恢復也只補寄一封。24 小時、3 天與掃描間隔都是以秒為單位的設定值，開發與 demo 時可以設成幾十秒。
- **信件**：
  - 主旨與內文用固定範本，由程式帶入，不經 AI；依事故分組列出缺的文件。
  - 公司名從保單的方案（`plans.json` 的 `company`）取得，空的就寫「保險公司」。資料庫仍不存保險公司（[ADR-0019](0019-no-insurance-company.md)）。
  - 「前往上傳」連到該行程的理賠頁（2-3），未登入時登入後回到這一頁。
- **寄件**：
  - 寄件者是系統通知信箱，顯示名稱「好險有你」，不用使用者的 Gmail；7 號用使用者的 Gmail 送件是另一條路，和 `AGENT_7_IMPL` 無關。
  - 收件者是使用者登入的 Google 帳號信箱，只寄已驗證的 email。
  - 程式只依 SMTP 設定寄信。正式環境用 Azure Communication Services Email：先用 Azure 提供的寄件網域，產品網域驗證好 SPF、DKIM 後再換成 no-reply@產品網域。開發環境用 Mailpit 收信，不會真的寄出。
- **停擺後自動恢復，不提供手動補掃**：該寄哪些信每次都由 Postgres 的現況算出，worker、beat 或 Redis 停擺後重新啟動，下一次掃描就會補寄到期的信。
  - worker、beat、Redis 都設 `restart: unless-stopped`，worker 加 healthcheck（`celery inspect ping`）。
  - 掃描工作設 `expires`，停擺期間堆積的掃描過期就丟掉，恢復後不會連續跑上百次。
  - SMTP 連線逾時、工作時間上限（`time_limit`）、`worker_max_tasks_per_child`，讓卡住的情況變成工作失敗並自動重試，不需要人去重啟。
  - 容器還在但不做事（`unhealthy`、工作堆積）或改了 `.env` 時，才由人 SSH 進 VM 處理；排查步驟寫在 `docs/deploy.md`。
- **既有的背景工作不搬**：條款入庫等工作仍用 `BackgroundTasks`（[ADR-0007](0007-ingest-agent-sse-progress.md)）。要不要搬到 Celery，沿用 ADR-0003 的條件重新評估：單一工作會跑到數分鐘、需要自動重試或進度歷史，或重啟造成工作遺失已經無法接受。

## Considered Options

- **backend 內的定時迴圈（lifespan 背景迴圈或 APScheduler）**：不用加服務。但 backend 開多個 process 時會重複寄信，重啟時工作會遺失，也沒有重試與監控。不採用。
- **`make remind` 加主機的 cron**：最簡單，也是原本的備案。但 cron 設在 compose 之外，部署與交接都要多記一件事，也沒有重試與監控。不採用。
- **用使用者的 Gmail 寄**：規格明定不用；而且使用者不一定授權了 Gmail，等於寄信給自己。不採用。
- **Google Workspace 的 no-reply 帳號**：每月要付一個帳號的費用，寄件信箱也綁在管理員的帳號上，人員離開後不好交接。不採用。
- **Flower 放在 Caddy 子路徑加 basic auth**：最方便，但會多一個公開入口；Flower 能取消與終止工作，密碼外洩就能操作排程。不採用。

## Consequences

- 開發環境多 Redis、worker、beat、Flower、Mailpit 五個服務；正式環境多 Redis、worker、beat，Flower 只在需要時開。B2s 只有 4 GB 記憶體，Redis 要設 `maxmemory`，worker 的 concurrency 要設小。
- `config.py`、`.env.example`、`.env.prod.example` 要加 Redis、SMTP 與提醒間隔的設定；`scripts/doctor.sh`、`docs/deploy.md` 與 CD 都要跟著改。正式環境要先申請 Azure Communication Services 並設定寄件網域。
- 交接文件 `function2-ui-realign.md` 原本建議「提醒信範本不做」，現在不適用；2-3 的「看提醒信範本」與「稍後再傳」的提示可以照設計稿做。
- 有了排程服務，[ADR-0009](0009-policy-document-personal-data.md) 記為已知限制的「自動過期」之後也能做，但不在本 ADR 範圍。
- 寄信和寫入資料庫不在同一個交易裡：寄出後、寫入前剛好當機，可能重複寄一封（固定 Message-ID 只能降低使用者看到兩封的機會）。在「可能重複」與「可能漏寄」之間選擇不漏寄，接受這個風險。
