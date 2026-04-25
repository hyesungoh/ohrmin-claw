"""세션 타임아웃 관리 테스트 — idle 초과 시 세션 리셋."""
import time
import pytest
from unittest.mock import patch

from core.session_manager import SessionManager, DEFAULT_IDLE_TIMEOUT_MINUTES


class TestSessionManagerDefaults:
    """기본 설정 테스트."""

    def test_default_timeout_is_1440(self):
        assert DEFAULT_IDLE_TIMEOUT_MINUTES == 1440

    def test_custom_timeout(self):
        mgr = SessionManager(idle_timeout_minutes=60)
        assert mgr.idle_timeout_minutes == 60


class TestUpdateActivity:
    """활동 기록 테스트."""

    def test_update_registers_thread(self):
        mgr = SessionManager()
        mgr.update_activity(12345)
        assert not mgr.is_expired(12345)

    def test_unknown_thread_is_not_expired(self):
        """처음 보는 스레드는 만료가 아니라 새 세션."""
        mgr = SessionManager()
        assert not mgr.is_expired(99999)


class TestSessionExpiry:
    """세션 만료 판단 테스트."""

    def test_session_expires_after_timeout(self):
        mgr = SessionManager(idle_timeout_minutes=30)
        mgr.update_activity(100)

        # 31분 후 시뮬레이션
        with patch("core.session_manager.time") as mock_time:
            mock_time.time.return_value = time.time() + (31 * 60)
            assert mgr.is_expired(100) is True

    def test_session_not_expired_within_timeout(self):
        mgr = SessionManager(idle_timeout_minutes=30)
        mgr.update_activity(100)

        # 10분 후 시뮬레이션
        with patch("core.session_manager.time") as mock_time:
            mock_time.time.return_value = time.time() + (10 * 60)
            assert mgr.is_expired(100) is False

    def test_update_resets_timer(self):
        mgr = SessionManager(idle_timeout_minutes=30)
        mgr.update_activity(100)

        # 20분 후 활동 갱신
        t0 = time.time()
        with patch("core.session_manager.time") as mock_time:
            mock_time.time.return_value = t0 + (20 * 60)
            mgr.update_activity(100)

        # 갱신 후 15분 (총 35분이지만 갱신 후 15분이므로 만료 아님)
        with patch("core.session_manager.time") as mock_time:
            mock_time.time.return_value = t0 + (35 * 60)
            assert mgr.is_expired(100) is False

        # 갱신 후 31분 (총 51분, 만료)
        with patch("core.session_manager.time") as mock_time:
            mock_time.time.return_value = t0 + (51 * 60)
            assert mgr.is_expired(100) is True


class TestClearSession:
    """세션 초기화 테스트."""

    def test_clear_removes_tracking(self):
        mgr = SessionManager()
        mgr.update_activity(100)
        mgr.clear(100)
        # 초기화 후 다시 새 세션 취급
        assert not mgr.is_expired(100)

    def test_clear_nonexistent_no_error(self):
        mgr = SessionManager()
        mgr.clear(99999)  # 에러 없어야 함


class TestMultipleThreads:
    """여러 스레드 독립 관리 테스트."""

    def test_threads_tracked_independently(self):
        mgr = SessionManager(idle_timeout_minutes=30)
        mgr.update_activity(1)
        mgr.update_activity(2)

        t0 = time.time()
        # 스레드 1만 31분 경과
        with patch("core.session_manager.time") as mock_time:
            mock_time.time.return_value = t0 + (31 * 60)
            assert mgr.is_expired(1) is True
            assert mgr.is_expired(2) is True  # 둘 다 같은 시간 등록

        # 스레드 2만 갱신
        with patch("core.session_manager.time") as mock_time:
            mock_time.time.return_value = t0 + (31 * 60)
            mgr.update_activity(2)

        # 스레드 2는 갱신 후 10분 → 만료 아님
        with patch("core.session_manager.time") as mock_time:
            mock_time.time.return_value = t0 + (41 * 60)
            assert mgr.is_expired(1) is True
            assert mgr.is_expired(2) is False
