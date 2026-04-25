"""세션 타임아웃 관리 — idle 초과 시 세션 리셋."""
import time

DEFAULT_IDLE_TIMEOUT_MINUTES = 1440  # 24시간 (Hermes 기본값)


class SessionManager:
    """스레드별 마지막 활동 시각을 추적하여 세션 만료를 판단."""

    def __init__(self, idle_timeout_minutes: int = DEFAULT_IDLE_TIMEOUT_MINUTES):
        self.idle_timeout_minutes = idle_timeout_minutes
        self._last_activity: dict[int, float] = {}

    def update_activity(self, thread_id: int) -> None:
        self._last_activity[thread_id] = time.time()

    def is_expired(self, thread_id: int) -> bool:
        if thread_id not in self._last_activity:
            return False  # 새 스레드는 만료가 아닌 새 세션
        last = self._last_activity[thread_id]
        elapsed_minutes = (time.time() - last) / 60
        return elapsed_minutes > self.idle_timeout_minutes

    def clear(self, thread_id: int) -> None:
        self._last_activity.pop(thread_id, None)
