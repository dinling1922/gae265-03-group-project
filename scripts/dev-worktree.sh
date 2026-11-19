#!/usr/bin/env bash
# make dev-worktree 用：起「這個 worktree」的後端與前端，連 docker 的開發資料庫，用來在 worktree 驗證改動。
#
# 為什麼：make up 的服務掛載的是主目錄的程式碼，worktree 的改動在 localhost:5173 看不到。
# 這裡在本機另起 uvicorn（--reload）與 vite，埠預設 8001／5174；db、mailpit 沿用 docker 的。
# 限制：redis 沒有對本機開埠，要排程或寄提醒信的功能（worker、beat）仍然只能在主目錄驗證。
# 前景執行，Ctrl+C 結束時只關掉自己起的兩個程序。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_PORT="${DEV_BACKEND_PORT:-8001}"
FRONT_PORT="${DEV_FRONT_PORT:-5174}"

[ -e "$ROOT/.env" ] || { echo "沒有 .env：先 make worktree-setup" >&2; exit 1; }
[ -d "$ROOT/frontend/node_modules" ] || { echo "沒有 frontend/node_modules：先 make worktree-setup" >&2; exit 1; }

port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
port_busy 5432 || { echo "連不到資料庫（127.0.0.1:5432）：先在主目錄 make up" >&2; exit 1; }
for p in "$BACKEND_PORT" "$FRONT_PORT"; do
  if port_busy "$p"; then
    echo "埠 $p 已被占用（可能是另一個 worktree 或 make e2e-fake）：用 DEV_BACKEND_PORT／DEV_FRONT_PORT 換埠。" >&2
    exit 1
  fi
done

run() { if command -v mise >/dev/null; then mise exec -- "$@"; else "$@"; fi; }

# set -m：背景程序各自成一個行程群組，結束時整組關掉（vite、uvicorn --reload 都會再生子程序）。
# macOS 沒有 setsid，用 bash 的工作控制達到一樣的效果
set -m
PIDS=()
cleanup() {
  for pid in "${PIDS[@]:-}"; do
    [ -n "$pid" ] && kill -TERM -- "-$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 130' INT TERM

# compose 在容器裡把主機名改成服務名；本機要連 docker 對外開的埠
(cd "$ROOT/backend" && POSTGRES_SERVER=localhost SMTP_HOST=localhost DEV_LOGIN_ENABLED=true \
  exec "$(run which uv)" run uvicorn app.main:app --reload --host 127.0.0.1 --port "$BACKEND_PORT") &
PIDS+=("$!")
(cd "$ROOT/frontend" && API_PROXY_TARGET="http://127.0.0.1:$BACKEND_PORT" \
  exec "$(run which pnpm)" exec vite --host 127.0.0.1 --port "$FRONT_PORT" --strictPort) &
PIDS+=("$!")

for _ in $(seq 1 60); do
  if curl -fsS -o /dev/null "http://127.0.0.1:$BACKEND_PORT/api/v1/auth/config" 2>/dev/null &&
    curl -fsS -o /dev/null "http://127.0.0.1:$FRONT_PORT/" 2>/dev/null; then
    echo
    echo "worktree 的服務就緒：前端 http://127.0.0.1:$FRONT_PORT（後端 :$BACKEND_PORT）"
    echo "對它跑 e2e：E2E_BASE_URL=http://127.0.0.1:$FRONT_PORT make e2e"
    wait
    exit 0
  fi
  for pid in "${PIDS[@]}"; do
    kill -0 "$pid" 2>/dev/null || { echo "服務啟動失敗，看上面的記錄" >&2; exit 1; }
  done
  sleep 1
done
echo "等了 60 秒還沒就緒，看上面的記錄" >&2
exit 1
