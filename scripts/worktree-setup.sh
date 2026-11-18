#!/usr/bin/env bash
# make worktree-setup 用：讓新開的 git worktree 能跑程式、測試與 make dev-worktree。重跑沒有副作用。
#
# worktree 只有 git 追蹤的檔案，缺的這幾樣在這裡補：
# - .env：建 symlink 指回主目錄那份（只建連結，不讀內容；改主目錄的 .env 所有 worktree 都會跟著變）
# - mise 的信任：mise 依路徑記錄，新路徑的 mise.toml 不被信任，mise exec 會失敗
# - Python 與前端依賴：uv sync、pnpm install（都從全域快取連結，很快）
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MAIN="$(dirname "$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir)")"
if [ "$ROOT" = "$MAIN" ]; then
  echo "這裡是主目錄，不是 worktree：用 make install" >&2
  exit 1
fi

if [ ! -e "$ROOT/.env" ]; then
  [ -f "$MAIN/.env" ] || { echo "主目錄沒有 .env：先在主目錄 cp .env.example .env 並填好" >&2; exit 1; }
  ln -s "$MAIN/.env" "$ROOT/.env"
  echo "✓ .env → $MAIN/.env"
fi

# 有 mise 就先信任這個 worktree，之後的工具都透過 mise 取得（PATH 上不一定有 pnpm）
run() { if command -v mise >/dev/null; then mise exec -- "$@"; else "$@"; fi; }
if command -v mise >/dev/null; then
  mise trust --quiet "$ROOT"
  echo "✓ mise trust"
fi

cd "$ROOT"
run uv sync
run pnpm --dir frontend install --frozen-lockfile
echo "worktree 就緒：make dev-worktree 起這個 worktree 的後端與前端（要先在主目錄 make up）"
