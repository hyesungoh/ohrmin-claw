"""런타임 서브프로세스 — spawn(실제 런타임 실행 가드) · stdio transport · 종료(terminate → 5s → kill).

실제 런타임 실행 가드(§3.3): `OHRMIN_FORBID_REAL_RUNTIMES=1`이면 spawn은 **자식 프로그램이 현재 인터프리터
(`sys.executable`, 심링크 해석 후 동일)일 때만** 허용한다 — 테스트의 tmp 에코 에이전트·게이트 훅 스크립트
(`[sys.executable, <script>]`). 그 외 실행 파일(codex·claude·grok 바이너리 등)은 exec 전에 RealRuntimeForbidden.
어댑터 테스트는 spawn을 거치지 않는 in-memory transport를 어댑터의 `transport_factory` seam으로 주입한다.
"""
import asyncio
import os
import shutil
import sys

from core.llm_errors import forbid_real_runtime

# 줄 단위 JSON 메시지 상한 (asyncio StreamReader 기본 64KiB는 도구 결과·명령 출력에 부족).
STREAM_LIMIT = 16 * 1024 * 1024
TERMINATE_TIMEOUT = 5.0


def _resolve_program(program: str) -> str | None:
    if os.path.dirname(program):
        path = os.path.expanduser(program)
        return path if os.path.isfile(path) else None
    return shutil.which(program)


def is_current_interpreter(argv: list[str]) -> bool:
    """argv[0]이 현재 인터프리터(sys.executable)인지 — 가드 예외 판정."""
    if not argv:
        return False
    resolved = _resolve_program(argv[0])
    return resolved is not None and os.path.realpath(resolved) == os.path.realpath(sys.executable)


def check_spawn_allowed(argv: list[str]) -> None:
    """테스트(OHRMIN_FORBID_REAL_RUNTIMES=1)에서 현재 인터프리터가 아닌 프로그램 실행을 막는다."""
    if not is_current_interpreter(argv):
        forbid_real_runtime()


async def spawn(argv: list[str], *, env: dict | None = None, cwd: str | None = None, stderr=None):
    """stdin/stdout 파이프로 자식 프로세스를 띄운다 (stderr 기본 = 부모 상속, 버퍼 교착 방지)."""
    check_spawn_allowed(argv)
    return await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=stderr,
        env=env,
        cwd=cwd,
        limit=STREAM_LIMIT,
    )


async def terminate(proc, timeout: float = TERMINATE_TIMEOUT) -> None:
    """terminate → timeout 대기 → kill (멱등)."""
    if proc.returncode is not None:
        return
    try:
        proc.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.wait()


class ProcessTransport:
    """jsonrpc_stdio transport — 자식 프로세스 stdin(쓰기)·stdout(줄 읽기)."""

    def __init__(self, proc):
        self.proc = proc

    async def readline(self) -> bytes:
        return await self.proc.stdout.readline()

    def write(self, data: bytes) -> None:
        self.proc.stdin.write(data)

    async def drain(self) -> None:
        await self.proc.stdin.drain()

    async def close(self) -> None:
        stdin = self.proc.stdin
        if stdin is not None and not stdin.is_closing():
            stdin.close()
        await terminate(self.proc)


async def spawn_transport(argv: list[str], env: dict | None, cwd: str | None) -> ProcessTransport:
    """기본 transport_factory — 실제 프로세스를 띄워 stdio transport로 감싼다 (가드 적용)."""
    return ProcessTransport(await spawn(argv, env=env, cwd=cwd))
