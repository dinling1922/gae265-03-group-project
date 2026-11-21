#!/usr/bin/env bash
# make e2e-fake 用：另起「假 5 號」的後端與前端，對它們跑 Playwright（票 #458）。
#
# 為什麼：docker 開發環境（localhost:5173）的後端是 AGENT_5_IMPL=v1（真實 5 號、Azure OpenAI），
# 完整 e2e 在上面會大量逾時；e2e 預期的是假 5 號那種即時、固定的回應。
# 這個腳本不動 docker 的服務與 .env，結束時（含失敗、Ctrl+C）只關掉自己啟動的兩個程序。
# macOS 沒有 setsid（util-linux 的指令）：開服務時改用 python3 的 os.setsid()，見下面的 own_session（#577）。
#
# 用法：scripts/e2e-fake.sh [playwright test 的參數...]，例如 scripts/e2e-fake.sh incident-policy-confirm
# 環境變數：E2E_BACKEND_PORT（預設 8001）、E2E_FRONT_PORT（預設 5174）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_PORT="${E2E_BACKEND_PORT:-8001}"
FRONT_PORT="${E2E_FRONT_PORT:-5174}"

# worktree 通常沒有自己的 .venv：找不到就用主目錄那份
UVICORN="$ROOT/.venv/bin/uvicorn"
if [ ! -x "$UVICORN" ]; then
  MAIN="$(cd "$(git -C "$ROOT" rev-parse --git-common-dir)/.." && pwd)"
  UVICORN="$MAIN/.venv/bin/uvicorn"
fi
[ -x "$UVICORN" ] || { echo "找不到 uvicorn（先在主目錄 make install）" >&2; exit 1; }

port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
for p in "$BACKEND_PORT" "$FRONT_PORT"; do
  if port_busy "$p"; then
    echo "埠 $p 已被占用：不動既有的服務。先關掉它，或用 E2E_BACKEND_PORT／E2E_FRONT_PORT 換埠。" >&2
    exit 1
  fi
done

LOGS="$(mktemp -d)"
# 主目錄的 frontend/node_modules/.vite* 是 docker 用 root 建的，本機寫不進去：快取放暫存目錄、設定檔在記憶體載入
VITE_CACHE="$LOGS/vite-cache"
PIDS=()
cleanup() {
  for pid in "${PIDS[@]:-}"; do
    [ -n "$pid" ] && kill -TERM -- "-$pid" 2>/dev/null || true
  done
  # 等它們真的結束，免得馬上再跑時埠還被占著
  for _ in $(seq 1 50); do
    alive=0
    for pid in "${PIDS[@]:-}"; do
      [ -n "$pid" ] && kill -0 -- "-$pid" 2>/dev/null && alive=1
    done
    [ "$alive" = 0 ] && break
    sleep 0.1
  done
}
trap cleanup EXIT
trap 'exit 130' INT TERM

# 兩個服務各自開新的 session，成為自己行程群組的組長（pgid == pid），結束時整組關掉（vite 會再生子行程）。
# exec 之後 PID 不變，cleanup 才能用 kill -- -PID。Linux 有 setsid 就照舊用；
# macOS 沒有 setsid（那是 util-linux 的指令），改用 python3 的 os.setsid() 再 exec 原本的指令（#577）
own_session() {
  if command -v setsid >/dev/null 2>&1; then
    exec setsid "$@"
  fi
  exec python3 -c 'import os,sys; os.setsid(); os.execvp(sys.argv[1], sys.argv[1:])' "$@"
}

(cd "$ROOT/backend" && AGENT_5_IMPL=fake DEV_LOGIN_ENABLED=true POSTGRES_SERVER=localhost \
  own_session "$UVICORN" app.main:app --host 127.0.0.1 --port "$BACKEND_PORT" >"$LOGS/backend.log" 2>&1) &
PIDS+=("$!")

[ -d "$ROOT/frontend/node_modules" ] || pnpm --dir "$ROOT/frontend" install --frozen-lockfile
(cd "$ROOT/frontend" && API_PROXY_TARGET="http://127.0.0.1:$BACKEND_PORT" VITE_CACHE_DIR="$VITE_CACHE" \
  own_session pnpm exec vite --configLoader runner --host 127.0.0.1 --port "$FRONT_PORT" --strictPort >"$LOGS/frontend.log" 2>&1) &
PIDS+=("$!")

wait_ready() { # 名稱 網址 PID 記錄檔
  for _ in $(seq 1 60); do
    if curl -fsS -o /dev/null "$2" 2>/dev/null; then return 0; fi
    if ! kill -0 "$3" 2>/dev/null; then
      echo "$1 啟動失敗（常見原因：資料庫沒開，先 make up）。最後的記錄：" >&2
      tail -n 15 "$4" >&2
      exit 1
    fi
    sleep 1
  done
  echo "$1 等了 60 秒還沒就緒。記錄在 $4" >&2
  exit 1
}
wait_ready 後端 "http://127.0.0.1:$BACKEND_PORT/api/v1/auth/config" "${PIDS[0]}" "$LOGS/backend.log"
wait_ready 前端 "http://127.0.0.1:$FRONT_PORT/" "${PIDS[1]}" "$LOGS/frontend.log"
echo "假 5 號後端 :${BACKEND_PORT}、前端 :${FRONT_PORT} 就緒（記錄在 ${LOGS}）"

cd "$ROOT/frontend"
pnpm exec playwright install chromium
set +e
E2E_BASE_URL="http://127.0.0.1:$FRONT_PORT" pnpm exec playwright test "$@"
status=$?
exit "$status"
