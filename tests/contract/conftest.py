"""계약 스위트 파라미터화 — `backend` ∈ claude_native · claude_registry · codex · grok.

- `@pytest.mark.ac16` 테스트는 claude_native 파라미터를 **생성하지 않는다**(skip 아님 — native는 CLI 네이티브 스킬).
- `@pytest.mark.backends(*names)` 테스트는 지정 백엔드만 생성한다(예: 비관적 fake = codex·grok).
- 네 백엔드 모두 연결됐다(codex P5, grok P6) — 대기(xfail) 파라미터 없음.

새 백엔드 연결 = fakes/<backend>_fake.py의 make_harness 구현 + BACKENDS·_FACTORIES 등록.
"""
import pytest
import pytest_asyncio

from tests.contract.fakes import claude_fake, codex_fake, grok_fake
from tests.contract.harness import build_env

BACKENDS = ("claude_native", "claude_registry", "codex", "grok")
_FACTORIES = {
    "claude_native": claude_fake.make_harness,
    "claude_registry": claude_fake.make_harness,
    "codex": codex_fake.make_harness,
    "grok": grok_fake.make_harness,
}


def pytest_configure(config):
    config.addinivalue_line("markers", "ac16: AC-16 스킬 registry 계약 — claude_native 파라미터 미생성")
    config.addinivalue_line("markers", "backends(*names): 계약 backend 파라미터를 지정 백엔드로 제한")


def pytest_generate_tests(metafunc):
    if "backend" not in metafunc.fixturenames:
        return
    names = list(BACKENDS)
    restrict = metafunc.definition.get_closest_marker("backends")
    if restrict is not None:
        names = [n for n in names if n in restrict.args]
    if metafunc.definition.get_closest_marker("ac16") is not None:
        names = [n for n in names if n != "claude_native"]
    metafunc.parametrize("backend", [pytest.param(n, id=n) for n in names])


@pytest.fixture
def contract_env(tmp_path):
    return build_env(tmp_path)


@pytest.fixture
def harness_factory(backend, contract_env):
    """options(예: gate_signals=False)를 받아 이 백엔드의 BackendHarness를 만든다 (기동 전)."""

    def make(**options):
        return _FACTORIES[backend](backend, contract_env, **options)

    return make


@pytest_asyncio.fixture
async def harness(harness_factory):
    """기동된 기본 하니스 — 테스트 후 close_all(+tool server 정지)."""
    h = harness_factory()
    await h.start()
    try:
        yield h
    finally:
        await h.close()
