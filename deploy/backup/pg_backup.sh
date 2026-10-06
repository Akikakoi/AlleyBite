#!/usr/bin/env sh
# PostgreSQL 每日全量备份（文档 10.5），保留 RETAIN_DAYS 天。
# 在项目根目录执行；建议配置到宿主机 crontab：
#   0 4 * * * cd /path/to/AlleyBite && sh deploy/backup/pg_backup.sh >> deploy/backup/backup.log 2>&1
#
# 说明：
#   - 本脚本负责**逻辑全量**（pg_dump），便于单库/单表恢复与跨版本迁移。
#   - WAL 增量归档由 docker-compose 的 archive_mode=on 持续落盘（卷 pgwal），
#     本脚本顺带清理超过 RETAIN_DAYS 的归档段落。
#   - 时间点恢复（PITR）需「物理基线 + WAL 重放」，基线见 deploy/backup/pg_base_backup.sh。
set -eu

BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETAIN_DAYS="${RETAIN_DAYS:-14}"
POSTGRES_USER="${POSTGRES_USER:-alleybite}"
POSTGRES_DB="${POSTGRES_DB:-alleybite}"
WAL_ARCHIVE_DIR="${WAL_ARCHIVE_DIR:-/var/lib/postgresql/wal-archive}"

STAMP="$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"

echo "[backup] $(date '+%F %T') 开始备份 $POSTGRES_DB → $BACKUP_DIR"
docker compose exec -T postgres pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -F c \
  > "$BACKUP_DIR/alleybite_${STAMP}.dump"

find "$BACKUP_DIR" -name 'alleybite_*.dump' -mtime "+${RETAIN_DAYS}" -delete

# WAL 归档卷在 postgres 容器内，清理需进容器执行；目录不存在时跳过（例如尚未启用归档）
docker compose exec -T postgres sh -c \
  "test -d '$WAL_ARCHIVE_DIR' && find '$WAL_ARCHIVE_DIR' -type f -name '0000*' -mtime +${RETAIN_DAYS} -delete || true"

echo "[backup] $(date '+%F %T') 完成，已清理 ${RETAIN_DAYS} 天前的逻辑备份与 WAL 归档"
