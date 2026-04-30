#!/bin/bash
# DB → SQL 덤프 백업 스크립트
# cron: 0 3 * * 0 /path/to/ohrmin-claw/scripts/backup.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
DB_DIR="$HOME/HealthData/DBs"
BACKUP_DIR="$PROJECT_DIR/backups"

mkdir -p "$BACKUP_DIR"

# GarminDB SQLite 파일들을 SQL 텍스트로 덤프
for db_name in garmin garmin_activities garmin_summary; do
    db_file="$DB_DIR/${db_name}.db"
    if [ -f "$db_file" ]; then
        sqlite3 "$db_file" .dump > "$BACKUP_DIR/${db_name}.sql"
        echo "✅ ${db_name}.db → ${db_name}.sql 백업 완료"
    else
        echo "⚠️ ${db_file} 파일이 없습니다. 건너뜁니다."
    fi
done

# git commit (프로젝트 디렉토리에서)
cd "$PROJECT_DIR"
if git diff --quiet backups/ 2>/dev/null; then
    echo "ℹ️ 변경사항 없음. 커밋 건너뜁니다."
else
    git add backups/
    git commit -m "weekly backup $(date +%Y%m%d)"
    echo "✅ git 커밋 완료"
fi
