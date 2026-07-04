"""학습 루프(축4) — 복잡한 인터랙티브 턴을 재사용 스킬 캡처로 유도하는 순수 판정/감지 로직.

이 모듈은 파일을 절대 쓰지 않는다. 담당은 두 가지뿐이다:
1. 스킬 캡처 '제안'을 표면화할지 판정 (should_propose_skill).
2. 이번 턴에 스킬 파일이 저장/수정됐는지 감지 (detect_skill_writes — hot-load 폴백용).

실제 SKILL.md 쓰기는 오너 승인(approve_skill_writes=True) 인터랙티브 경로에서만 일어나며,
core/llm.py의 PreToolUse 게이트(evaluate_tool_gate)가 무인 턴·science-reference 쓰기를
구조적으로 하드 차단한다. 즉 이 모듈은 안전 게이트를 우회하지 않는다 — 게이트 위에 얹힌다.
"""
import os
import re

# 스킬 캡처를 자동 제안하는 도구 호출 임계 수 (auto 모드).
DEFAULT_TOOL_THRESHOLD = 5

# manual 모드에서 스킬 캡처를 명시 요청하는 오너 키워드.
_SKILL_REQUEST_PATTERN = re.compile(
    r"스킬(으?로|을|에)?\s*(저장|만들|추가|캡처|등록)"
    r"|재사용\s*스킬"
    r"|(이|그)\s*(분석|절차|방법).{0,6}스킬"
    r"|스킬.{0,6}(저장|만들|추가)"
)

# 새 스킬 저장 후 hot-load 불가 시 오너에게 보내는 재시작 안내(폴백).
SKILL_RESTART_NOTICE = "🧩 새 스킬({names})을 저장했어요. 다음 봇 재시작 후부터 자동 적용됩니다."


def is_explicit_skill_request(text: str) -> bool:
    """오너 메시지가 '이 절차를 스킬로 저장' 류의 명시 요청인지 판정."""
    return bool(_SKILL_REQUEST_PATTERN.search(text or ""))


def should_propose_skill(
    learning_mode: str,
    interactive: bool,
    tool_count: int,
    success: bool,
    *,
    explicit_request: bool = False,
    threshold: int = DEFAULT_TOOL_THRESHOLD,
) -> bool:
    """스킬 캡처 제안을 표면화할지 판정 (순수 함수, 파일 쓰기 없음).

    불변식(load-bearing): off 모드·무인 초기자·실패 턴은 절대 제안하지 않는다.
    - off:    항상 False.
    - auto:   인터랙티브 + 성공 + tool_count >= threshold 일 때 자동 제안.
    - manual: 인터랙티브 + 성공 + 오너 명시 요청(explicit_request) 일 때만 제안.

    무인 초기자(cron·자동 분석)는 애초에 이 함수를 호출하지 않지만, interactive=False로
    호출돼도 여기서 차단된다(방어적 이중 보장). 실제 스킬 쓰기는 별개(오너 승인 경로).
    """
    if learning_mode == "off" or not interactive or not success:
        return False
    if learning_mode == "manual":
        return bool(explicit_request)
    if learning_mode == "auto":
        return tool_count >= threshold
    return False


def snapshot_skill_mtimes(skills_dir: str) -> dict:
    """.claude/skills/<name>/SKILL.md 의 name→mtime 스냅샷 (hot-load 폴백 감지 기준)."""
    snapshot: dict = {}
    try:
        names = os.listdir(skills_dir)
    except OSError:
        return snapshot
    for name in names:
        skill_md = os.path.join(skills_dir, name, "SKILL.md")
        try:
            snapshot[name] = os.path.getmtime(skill_md)
        except OSError:
            continue
    return snapshot


def detect_skill_writes(skills_dir: str, before: dict) -> list:
    """before 스냅샷 대비 새로 생기거나 갱신된 스킬 이름 목록 (정렬).

    이번 턴에 오너 승인으로 SKILL.md가 생성/수정됐는지 감지한다. 결과가 비지 않으면
    bot이 재시작 안내(SKILL_RESTART_NOTICE)를 표면화한다.
    """
    after = snapshot_skill_mtimes(skills_dir)
    changed = [
        name
        for name, mtime in after.items()
        if before.get(name) is None or mtime > before[name]
    ]
    return sorted(changed)
