#!/usr/bin/env bash
# 本地可复现流水线（与 .github/workflows/ci.yml 对齐，文档 10.3）
#
# 用法：
#   bash deploy/ci.sh              # 全部检查（lint + 单测 + 前端构建 + 镜像构建）
#   SKIP_DOCKER=1 bash deploy/ci.sh  # 跳过镜像构建
#
# 可用环境变量：
#   PYTHON      指定 python 解释器（默认 python，可指向 backend/.venv）
#   SKIP_DOCKER 置 1 跳过 docker compose build
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PYTHON:-python}"

echo "==> [1/4] 后端依赖"
(cd "$ROOT/backend" && "$PYTHON" -m pip install -q -r requirements.txt ruff)

echo "==> [2/4] 后端 lint + 单测"
(cd "$ROOT/backend" && "$PYTHON" -m ruff check --select E9,F63,F7,F82 app scripts tests)
(cd "$ROOT/backend" && "$PYTHON" -m pytest -q)

echo "==> [3/4] 前端构建"
(cd "$ROOT/web" && npm ci && npm run build)

if [ "${SKIP_DOCKER:-0}" = "1" ]; then
    echo "==> [4/4] 跳过镜像构建（SKIP_DOCKER=1）"
else
    echo "==> [4/4] 镜像构建"
    (cd "$ROOT" && docker compose build api web)
fi

echo "CI 全部通过 ✓"
