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

# 봇 데이터(data/) 백업 — 세션 인덱스(SQL 덤프), cron 잡, 체성분 CSV.
DATA_DIR="$PROJECT_DIR/data"
mkdir -p "$BACKUP_DIR/data"

# session_index.db → SQL 덤프. sqlite3 연결이 WAL/SHM 사이드카를 읽으므로 덤프에 최신 상태가 포함됨.
if [ -f "$DATA_DIR/session_index.db" ]; then
    sqlite3 "$DATA_DIR/session_index.db" .dump > "$BACKUP_DIR/data/session_index.sql"
    echo "✅ session_index.db → data/session_index.sql 백업 완료"
else
    echo "ℹ️ session_index.db 없음. 건너뜁니다."
fi

# JSON/CSV(cron 잡·체성분)는 그대로 복사.
for data_file in cron_jobs.json inbody.csv; do
    src="$DATA_DIR/$data_file"
    if [ -f "$src" ]; then
        cp "$src" "$BACKUP_DIR/data/${data_file}"
        echo "✅ data/${data_file} 백업 완료"
    else
        echo "ℹ️ data/${data_file} 없음. 건너뜁니다."
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
