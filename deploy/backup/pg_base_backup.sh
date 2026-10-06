#!/usr/bin/env sh
# PostgreSQL 物理基线备份 pg_basebackup（文档 10.5），保留 RETAIN_DAYS 天。
#
# 为何需要：WAL 增量归档（docker-compose 中 archive_mode=on）只记录"变更"，
#   必须搭配一个物理基线，才能通过「基线 + WAL 重放」恢复到 14 天内任意时间点（PITR）。
#   逻辑备份 pg_dump（pg_backup.sh）是另一条链路，不能与 WAL 组合做 PITR。
#
# 建议每周执行一次，间隔须小于 RETAIN_DAYS：
#   30 4 * * 0 cd /path/to/AlleyBite && sh deploy/backup/pg_base_backup.sh >> deploy/backup/backup.log 2>&1
set -eu

BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETAIN_DAYS="${RETAIN_DAYS:-14}"
POSTGRES_USER="${POSTGRES_USER:-alleybite}"

STAMP="$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"

echo "[base-backup] $(date '+%F %T') 开始物理基线备份 → $BACKUP_DIR/base_${STAMP}.tar.gz"
# -D - 输出到 stdout；-F t 打 tar；-z 压缩；-X fetch 一并取走基线自洽所需的 WAL
docker compose exec -T postgres pg_basebackup -U "$POSTGRES_USER" -D - -F t -z -X fetch \
  > "$BACKUP_DIR/base_${STAMP}.tar.gz"

find "$BACKUP_DIR" -name 'base_*.tar.gz' -mtime "+${RETAIN_DAYS}" -delete
echo "[base-backup] $(date '+%F %T') 完成，已清理 ${RETAIN_DAYS} 天前的基线备份"
