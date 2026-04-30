#!/bin/bash
# 초기 환경 세팅 스크립트

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "🚀 ohrmin-claw 초기 설정 시작"

# 1. Python 의존성 설치
echo "📦 Python 패키지 설치 중..."
pip3 install -r "$PROJECT_DIR/requirements.txt"

# 2. .env 파일 확인
if [ ! -f "$PROJECT_DIR/.env" ]; then
    echo "⚠️ .env 파일이 없습니다. .env.example에서 복사합니다."
    cp "$PROJECT_DIR/.env.example" "$PROJECT_DIR/.env"
    echo "📝 .env 파일을 편집하여 인증 정보를 입력하세요."
    exit 1
fi

# 3. 디렉토리 생성
mkdir -p "$PROJECT_DIR/data"
mkdir -p "$PROJECT_DIR/backups"
mkdir -p "$HOME/HealthData/DBs"
mkdir -p "$HOME/.GarminDb"

# 4. GarminDB 설정 확인
if [ ! -f "$HOME/.GarminDb/GarminConnectConfig.json" ]; then
    echo "⚠️ GarminDB 설정 파일이 없습니다."
    echo "  .env의 GARMIN_USERNAME/GARMIN_PASSWORD를 확인하고"
    echo "  garmindb/sync.sh를 실행하세요."
fi

# 5. 스크립트 실행 권한
chmod +x "$PROJECT_DIR/garmindb/sync.sh"
chmod +x "$PROJECT_DIR/scripts/backup.sh"

echo ""
echo "✅ 초기 설정 완료!"
echo ""
echo "다음 단계:"
echo "  1. .env 파일에 인증 정보 입력"
echo "  2. GarminDB 초기 동기화: bash garmindb/sync.sh"
echo "  3. 봇 시작: python3 bot/main.py"
echo ""
echo "cron 등록 (선택):"
echo "  # 매일 06:00 Garmin 동기화"
echo "  0 6 * * * $PROJECT_DIR/garmindb/sync.sh"
echo "  # 매주 일요일 03:00 백업"
echo "  0 3 * * 0 $PROJECT_DIR/scripts/backup.sh"
