#!/usr/bin/env bash
# 一键更新线上 demo：拉最新代码 → 同步依赖 → 重启服务 → 自检
#
# 用法（在服务器上执行）：
#   bash /www/wwwroot/neonovaclash/deploy/update.sh
#
# 想回滚到某个稳定版本时：
#   cd /www/wwwroot/neonovaclash && git fetch --tags && git checkout v0.1.0 && bash deploy/update.sh

set -euo pipefail

APP_DIR="${APP_DIR:-/www/wwwroot/neonovaclash}"
SERVICE="${SERVICE:-neonovaclash}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8010/api/health}"

cd "$APP_DIR"

echo "==> 当前版本：$(git rev-parse --short HEAD) $(git log -1 --pretty=%s)"
git pull --ff-only
"$APP_DIR/venv/bin/pip" install --quiet --disable-pip-version-check -r requirements.txt

systemctl restart "$SERVICE"
sleep 2

echo "==> 健康检查 $HEALTH_URL"
curl -fsS "$HEALTH_URL"
echo

echo "==> 已更新到 $(git rev-parse --short HEAD) $(git log -1 --pretty=%s)"
