"""공용 pytest 픽스처."""
import pytest


@pytest.fixture(autouse=True)
def isolate_session_index(tmp_path, monkeypatch):
    """bot.main.session_index를 테스트별 tmp 인스턴스로 교체.

    handle_health_query/run_agent_to_channel 경로가 실 data/session_index.db에 쓰지 않도록 격리.
    bot.main을 import할 수 없는 환경(자격증명 부재 등)에서는 무해하게 건너뛴다.
    """
    try:
        import bot.main as main_module
        from core.session_index import SessionIndex
    except Exception:
        return
    monkeypatch.setattr(
        main_module,
        "session_index",
        SessionIndex(str(tmp_path / "test_session_index.db")),
        raising=False,
    )


@pytest.fixture(autouse=True)
def reset_health_context_cache():
    """건강 컨텍스트 TTL 캐시를 테스트별로 초기화.

    bot.main._context_cache는 모듈 레벨 가변 상태라 테스트 간 오염(warm cache 재사용)을
    유발한다. _collect_health_context_async를 직접 호출하는 테스트가 매번 신선하게 수집하도록
    각 테스트 전에 무효화한다. import 불가 환경에서는 무해하게 건너뛴다.
    """
    try:
        import bot.main as main_module
    except Exception:
        return
    if hasattr(main_module, "_invalidate_context_cache"):
        main_module._invalidate_context_cache()
