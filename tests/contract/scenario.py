"""런타임 스크립트 DSL — 백엔드 중립. 각 fake가 자기 런타임의 네이티브 이벤트로 번역한다.

도구 이름·입력은 정규 어휘(Claude 명명: Bash·Write·…·mcp__<server>__<tool>, 입력 키 file_path·command 등)로 쓴다.
`fake.script(*steps)`는 `end()`로 구분된 턴들을 큐에 넣고, 런타임이 받는 N번째 프롬프트(one-shot 호출·
스레드 세션 턴·중첩 ask 모두)가 N번째 턴을 소비한다.

- text(s): 완결 텍스트 세그먼트 1개.
- tool(name, input): 도구 호출 시작(on_tool) → 게이트 wiring → (허용 시) 실행.
- gate_probe(name, input): tool과 같은 실행 경로 — 테스트가 게이트 결과(실행 여부·[gate] 로그)를 관측한다는 표시.
- error(kind, resets_at): provider 오류 신호(usage_limit | auth_expired | generic). 원시 문자열에 RAW_PROVIDER_MARKER 포함.
- wait_interrupt(): 스레드 세션에서 interrupt(또는 세션 종료)까지 스트림 정지. interrupt 후 남은 스텝은 버린다.
- crash(): 런타임 프로세스가 이 지점에서 죽는다(스트림 중 예외, 세션 사용 불가).
- end(): 턴 경계.
"""
from dataclasses import dataclass, field

# fake가 원시 provider 오류 문자열에 넣는 표식 — 사용자 메시지·반환값에 절대 나오면 안 된다.
RAW_PROVIDER_MARKER = "raw-provider-secret.internal.example"

ERROR_KINDS = ("usage_limit", "auth_expired", "generic")


@dataclass(frozen=True)
class Step:
    kind: str
    text: str | None = None
    name: str | None = None
    input: dict = field(default_factory=dict)
    error_kind: str | None = None
    resets_at: int | None = None


def text(s: str) -> Step:
    return Step("text", text=s)


def tool(name: str, input: dict | None = None) -> Step:
    return Step("tool", name=name, input=dict(input or {}))


def gate_probe(name: str, input: dict | None = None) -> Step:
    return Step("gate_probe", name=name, input=dict(input or {}))


def error(kind: str, resets_at: int | None = None) -> Step:
    if kind not in ERROR_KINDS:
        raise ValueError(f"unknown provider error kind: {kind}")
    return Step("error", error_kind=kind, resets_at=resets_at)


def wait_interrupt() -> Step:
    return Step("wait_interrupt")


def crash() -> Step:
    return Step("crash")


def end() -> Step:
    return Step("end")


def split_turns(steps) -> list[list[Step]]:
    """스텝 나열 → end()로 구분된 턴 목록 (마지막 end() 생략 가능)."""
    turns, current = [], []
    for step in steps:
        if step.kind == "end":
            turns.append(current)
            current = []
        else:
            current.append(step)
    if current:
        turns.append(current)
    return turns
