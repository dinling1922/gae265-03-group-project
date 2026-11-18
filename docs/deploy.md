# 部署（GitHub CD → GHCR → Azure VM）

決策見 [ADR-0001](adr/0001-single-vm-docker-compose-deploy.md)。

## 流程

推 `v*` tag（或到 Actions 頁面手動執行 Deploy，選 main 或某個 tag）：

1. `check`：commit 必須在 main 歷史上，否則失敗（tag 打在未合併分支會被擋）
2. `build`：在 GitHub 打包 `backend`、`caddy`（含前端）推到 `ghcr.io/kurotyan514/gae265-03-group-project-{backend,caddy}:<commit SHA>`；該 SHA 的 image 已存在就跳過
3. `deploy`（Environment `production`）：SSH 進 VM 執行 `make deploy`，再從外部檢查 `https://<網域>/api/v1/health`

連續觸發：打包中的舊版會被取消；正在部署的跑完，排隊的只留最新一次。

```bash
git tag v0.1.0 && git push origin v0.1.0
```

## 首次架設

執行 `scripts/setup-vm.sh`（從 `az login` 開始，每一步先顯示指令、確認後才執行）。精靈會：建 VM（B2s、Ubuntu、`eastasia`，不行改 `japaneast`）、開 22／80／443、設網域與每天台灣時間 23:00 自動關機、產生部署金鑰與 host key，並引導你：

- 裝 Docker、clone repo、從 `.env.prod.example` 建 `.env`
- 到 Google Cloud Console 登記 `${PUBLIC_BASE_URL}/api/v1/auth/google/callback`
- 在 GitHub 建 Environment `production`（deployment 只允許 `main` 與 `v*`，不設 reviewer），放三個 secret：
  - `DEPLOY_HOST`：`user@<名稱>.<區域>.cloudapp.azure.com`
  - `DEPLOY_SSH_KEY`：部署專用私鑰
  - `DEPLOY_KNOWN_HOSTS`：VM 的 host key
- 第一次部署後，把兩個 GHCR package 改成 public（預設 private，VM 才能不登入 pull），再重跑部署
- 匯入 seed（VM 上不能用 `make seed`）：

```bash
IMAGE_TAG=$(git rev-parse HEAD) docker compose -f compose.prod.yaml exec backend python -m app.seed load
# embedding deployment 名稱與匯出時不同：加 --as-model <名稱>
```

`.env` 必須補齊 [integration-plan](integration-plan.md) 列的正式環境必填設定（Google、`TOKEN_ENCRYPTION_KEY`、`ADMIN_EMAILS` 等），缺任何一項 backend 會啟動失敗，部署的 `--wait` 會失敗。

提醒信要另外補 `SMTP_*` 與 `MAIL_FROM`（專案專用的 Gmail，見下方「提醒信」）。沒補不會讓部署失敗，但信會寄不出去，worker 的 log 會一直出現寄信失敗與重試。

## 回滾

手動部署舊的 tag。GHCR 已有該版 image 就不重新打包。每次部署完會清掉 VM 上沒在用的 image，所以回滾時會從 GHCR 重新 pull。

注意：不會退回 migration。舊程式碼與新資料表結構不相容時，要寫新的 migration 修正，不能直接回滾。

## 本機手動部署（GitHub Actions 不能用時）

```bash
make deploy DEPLOY_HOST=user@<網域> REF=v0.1.0
```

該版 image 必須已由 CD 打包過（`--no-build`，不存在會直接失敗）。

## VM 停機

部署前不會自動開機。VM 關機時部署會 SSH 逾時而失敗；開機後重跑 workflow。

## 在 VM 上手動執行 compose

要自己帶 `IMAGE_TAG`：

```bash
IMAGE_TAG=$(git rev-parse HEAD) docker compose -f compose.prod.yaml ps
```

本機驗證正式環境（不經 GHCR）：`docker compose -f compose.prod.yaml up --build`。

## 提醒信（Celery）

決策見 [ADR-0031](adr/0031-reminder-email-with-celery-redis.md)。正式環境多三個服務，都不對外開 port：`redis`（佇列，不保存資料）、`worker`（寄信）、`beat`（每 10 分鐘排一次掃描）。三個都用 backend 的 image，部署時一起更新。

- **VM 停機**：每天 23:00 自動關機期間不會寄信；開機後下一次掃描會補寄到期的信（每張保單只補一封）。
- **寄件設定**：用專案專用的 Gmail 寄出，申請方式見下方「申請寄信用的 Gmail」。改完 `.env` 要重新建立容器，`restart` 不會重讀：

```bash
IMAGE_TAG=$(git rev-parse HEAD) docker compose -f compose.prod.yaml up -d worker beat
```

- **確認寄得出去**：在 VM 上用正式設定寄一封測試信給自己（會真的寄出）：

```bash
IMAGE_TAG=$(git rev-parse HEAD) docker compose -f compose.prod.yaml exec worker python -c "
from app.domain.reminder.mailer import SmtpMailer, OutgoingMail
from app.domain.reminder.letter import Letter
SmtpMailer().send(OutgoingMail(to='<你的信箱>', letter=Letter('好險有你 測試信', '測試', '<p>測試</p>'), message_id='<deploy-check@example.com>'))"
```

### 申請寄信用的 Gmail

只要做一次，免費。另外申請一個帳號，不要用成員的個人信箱：個人信箱會公開給所有收件者，應用程式密碼外洩也等於個人信箱外洩。

1. 申請新的 Gmail，名稱取得像系統信箱（例如 `haoxianyouni.noreply@gmail.com`）。手機驗證與兩步驟驗證用負責管理的成員的手機，交接時一起轉移。
2. Google 帳戶 →「安全性」→ 開啟**兩步驟驗證**。
3. Google 帳戶搜尋「**應用程式密碼**」，新增一組（例如命名為 `好險有你提醒信`），複製 16 碼密碼。只會顯示一次。
4. VM 與要測試的本機 `.env` 填入（密碼用私訊交接，不要貼在群組或 commit 裡）：

```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_STARTTLS=true
SMTP_USERNAME=<專用 Gmail 地址>
SMTP_PASSWORD=<16 碼應用程式密碼>
MAIL_FROM=<專用 Gmail 地址>
```

5. 本機先寄一封測試信確認（會真的寄出）：

```bash
make test-mail to=<你的信箱>
```

注意：

- `MAIL_FROM` 要和 `SMTP_USERNAME` 相同，Gmail 會把寄件地址改成登入的帳號；顯示名稱仍是「好險有你 Tripcare」。
- 本機填了這些值，提醒信就會真的寄到 Google 登入帳號的信箱，不再進 Mailpit。測完把 `SMTP_*` 改回 `.env.example` 的值，再 `docker compose up -d worker beat`。
- 一般 Gmail 每天寄送有上限（約數百封），超過會暫停寄信約一天，不會收費。
- 使用者回信會進這個信箱；課程結束後到 Google 帳戶刪掉應用程式密碼。
- 寄件地址是 `@gmail.com`。要換成 no-reply@產品網域，得另外買網域並改用交易信服務（ADR-0031）。

### 用 Flower 看工作

Flower 跟著其他服務一直開著，用瀏覽器開 `https://<網域>/flower`，輸入 `.env` 的 `FLOWER_BASIC_AUTH`（格式「帳號:密碼」）登入。

- 第一次部署前要在 VM 的 `.env` 填好 `FLOWER_BASIC_AUTH`；沒填 Flower 會拒絕啟動，`/flower` 回 502，網站其他功能不受影響。
- 產生密碼：

```bash
openssl rand -base64 18
```

- 改了帳號密碼要重建 Flower 才會讀到新值：

```bash
IMAGE_TAG=$(git rev-parse HEAD) docker compose -f compose.prod.yaml up -d flower
```

- Flower 能取消與終止工作：帳號密碼只給需要的人，懷疑外洩就換掉。
- Flower 只記得它這次啟動之後的工作，重啟後之前的紀錄會清空。

### 排查

先看狀態與 log：

```bash
IMAGE_TAG=$(git rev-parse HEAD) docker compose -f compose.prod.yaml ps
```

```bash
IMAGE_TAG=$(git rev-parse HEAD) docker compose -f compose.prod.yaml logs --tail=200 worker
```

| 狀況 | 怎麼看出來 | 處理 |
| --- | --- | --- |
| worker 卡住（還在跑但不做事） | `ps` 顯示 `unhealthy`；Flower 看到工作堆積；log 停在某個時間點 | `restart worker` |
| 改了 `.env` 沒生效 | 新的 SMTP 設定沒用到 | 用上面的 `up -d worker beat` 重新建立，不要用 `restart` |
| 設定錯誤（例如 `REDIS_URL`） | 容器一直重啟又馬上結束 | 修正 `.env`，再 `up -d` |
| 寄信一直失敗 | log 反覆出現 `Retry in` 或 SMTP 錯誤（例如 `535 Username and Password not accepted`） | 檢查 `SMTP_USERNAME`、應用程式密碼與 `MAIL_FROM`；應用程式密碼被刪除或改了 Google 密碼都會失效，要重建一組；重啟沒有用 |
| Postgres 或 Redis 停了 | log 出現連線錯誤 | 先處理 db 或 redis，worker 會自己重新連上 |

寄信失敗重試 5 次（10、20、40、80、160 秒）都失敗也不會漏寄：計數沒加，下一次掃描會再派發。不需要手動補寄。
