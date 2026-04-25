#!/bin/bash
# Garmin Connect 토큰 검증/갱신 스크립트
# 봇은 python-garminconnect로 직접 API를 호출하므로 DB 동기화 불필요.
# 이 스크립트는 토큰이 유효한지 확인하고, 만료 시 갱신한다.
# cron: 0 6 * * * /path/to/health-manager/garmindb/sync.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG_FILE="$PROJECT_DIR/data/garmin_sync.log"
TOKEN_DIR="$HOME/.garminconnect"
PYTHON3="/opt/homebrew/opt/python@3.11/bin/python3.11"

timestamp() {
    date '+%Y-%m-%d %H:%M:%S'
}

log() {
    echo "$(timestamp) - $1" | tee -a "$LOG_FILE"
}

mkdir -p "$PROJECT_DIR/data"

log "========== Garmin Connect 토큰 검증 시작 =========="

# .env에서 인증 정보 로드
if [ -f "$PROJECT_DIR/.env" ]; then
    set -a
    source "$PROJECT_DIR/.env"
    set +a
fi

if [ -z "$GARMIN_USERNAME" ] || [ -z "$GARMIN_PASSWORD" ]; then
    log "[실패] GARMIN_USERNAME 또는 GARMIN_PASSWORD가 .env에 설정되지 않음"
    exit 1
fi

# 토큰 검증 및 갱신
log "[1/1] 토큰 검증 중..."
TOKEN_RESULT=$(cd "$PROJECT_DIR" && $PYTHON3 -c "
import os, sys, datetime
os.environ.get('GARMIN_USERNAME')  # already in env
from garminconnect import Garmin

token_dir = os.path.expanduser('~/.garminconnect')
os.makedirs(token_dir, exist_ok=True)

try:
    api = Garmin(
        email=os.environ['GARMIN_USERNAME'],
        password=os.environ['GARMIN_PASSWORD'],
    )
    api.login(tokenstore=token_dir)
    today = datetime.date.today().isoformat()
    stats = api.get_stats(today)
    steps = stats.get('totalSteps', 'N/A')
    print(f'OK - 오늘 걸음 수: {steps}')
except Exception as e:
    err = str(e)
    if '429' in err:
        print('ERR_RATE_LIMIT', file=sys.stderr)
    elif any(k in err.lower() for k in ['auth', 'credential', 'password', 'username']):
        print('ERR_AUTH', file=sys.stderr)
    elif any(k in err.lower() for k in ['timeout', 'unreachable', 'network']):
        print('ERR_NETWORK', file=sys.stderr)
    else:
        print(f'ERR_UNKNOWN: {err}', file=sys.stderr)
    sys.exit(1)
" 2>"$PROJECT_DIR/data/.sync_err")

SYNC_EXIT=$?
SYNC_ERR=$(cat "$PROJECT_DIR/data/.sync_err" 2>/dev/null)
rm -f "$PROJECT_DIR/data/.sync_err"

if [ $SYNC_EXIT -eq 0 ]; then
    log "[성공] $TOKEN_RESULT"
else
    case "$SYNC_ERR" in
        ERR_RATE_LIMIT)
            log "[실패] 429 Rate Limit. 1~2시간 후 재시도 필요" ;;
        ERR_AUTH)
            log "[실패] 인증 오류. .env의 GARMIN_USERNAME/GARMIN_PASSWORD 확인 필요" ;;
        ERR_NETWORK)
            log "[실패] 네트워크 오류. 인터넷 연결 확인 필요" ;;
        *)
            log "[실패] $SYNC_ERR" ;;
    esac
    exit 1
fi

log "========== Garmin Connect 토큰 검증 완료 =========="
