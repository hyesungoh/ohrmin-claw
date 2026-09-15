"""Codex PreToolUse 훅 명령 — 봇 게이트 엔드포인트에 판정을 위임한다 (fail-closed).

Codex가 `[sys.executable, <절대경로>/core/hooks/codex_gate_hook.py]`로 실행한다. 제약:
- stdlib만 사용, `core` import 금지(패키지 `__init__` 없음), Python 3.9 문법(런타임이 다른 인터프리터일 수 있음).
- stdin JSON(훅 payload)을 `$OHRMIN_GATE_URL`(경로에 priv/ro 토큰)로 POST, 타임아웃 5초.
- 응답 `{"allow": true}` → exit 0. deny·예외·타임아웃·비200·잘못된 JSON → deny hookSpecificOutput을
  stdout에 출력 + exit 2.

# provenance: https://learn.chatgpt.com/docs/hooks.md verified=false
"""
import json
import os
import sys
import urllib.request

TIMEOUT_SECONDS = 5
FALLBACK_REASON = "게이트 판정을 확인할 수 없어 안전을 위해 차단합니다."


def _deny(reason):
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    sys.stdout.write(json.dumps(output))
    sys.stdout.flush()
    # exit 2 규약에서 사유 채널로 쓰이는 stderr에도 남긴다(로케일 무관 UTF-8).
    sys.stderr.buffer.write((reason + "\n").encode("utf-8"))
    sys.stderr.flush()
    return 2


def main():
    try:
        payload = json.loads(sys.stdin.buffer.read())
        request = urllib.request.Request(
            os.environ["OHRMIN_GATE_URL"],
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        # 루프백 엔드포인트 — 환경 프록시 설정을 무시한다.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
            if response.status != 200:
                return _deny(FALLBACK_REASON)
            body = json.loads(response.read())
        if not isinstance(body, dict):
            return _deny(FALLBACK_REASON)
        if body.get("allow") is True:
            return 0
        reason = body.get("reason")
        return _deny(reason if isinstance(reason, str) and reason else FALLBACK_REASON)
    except Exception:
        return _deny(FALLBACK_REASON)


if __name__ == "__main__":
    sys.exit(main())
