#!/usr/bin/env sh
# PostgreSQL 每日全量备份（文档 10.5），保留 RETAIN_DAYS 天。
# 在项目根目录执行；建议配置到宿主机 crontab：
#   0 4 * * * cd /path/to/AlleyBite && sh deploy/backup/pg_backup.sh >> deploy/backup/backup.log 2>&1
#
# 说明：WAL 增量归档需在 postgres 侧开启 archive_mode（生产按需配置），
#       本脚本负责每日全量。OSS 快照与榜单快照的保留策略见文档 10.5。
set -eu

BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETAIN_DAYS="${RETAIN_DAYS:-14}"
POSTGRES_USER="${POSTGRES_USER:-alleybite}"
POSTGRES_DB="${POSTGRES_DB:-alleybite}"

STAMP="$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"

echo "[backup] $(date '+%F %T') 开始备份 $POSTGRES_DB → $BACKUP_DIR"
docker compose exec -T postgres pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -F c \
  > "$BACKUP_DIR/alleybite_${STAMP}.dump"

find "$BACKUP_DIR" -name 'alleybite_*.dump' -mtime "+${RETAIN_DAYS}" -delete
echo "[backup] $(date '+%F %T') 完成，已清理 ${RETAIN_DAYS} 天前的备份"