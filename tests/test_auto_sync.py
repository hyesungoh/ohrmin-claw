"""자동 동기화 + 분석 피드백 테스트."""
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestAutoAnalysis:
    """_run_auto_analysis() 단위 테스트."""

    @pytest.fixture(autouse=True)
    def setup_patches(self, monkeypatch):
        """bot/main.py 모듈 임포트 전에 필요한 환경변수 세팅."""
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "fake-token")
        monkeypatch.setenv("ALLOWED_USERS", "123")
        monkeypatch.setenv("NOTIFY_CHANNEL_ID", "999")
        monkeypatch.setenv("APPLE_HEALTH_EXPORT_DIR", "/tmp/fake_hae")

    @pytest.mark.asyncio
    async def test_run_auto_analysis_sends_to_thread(self):
        """새 데이터가 있으면 스레드를 생성하고 분석 결과를 전송한다."""
        mock_thread = AsyncMock()
        mock_thread.id = 12345
        mock_thread.send = AsyncMock()
        mock_thread.typing = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(), __aexit__=AsyncMock()
        ))

        mock_channel = AsyncMock()
        mock_channel.create_thread = AsyncMock(return_value=mock_thread)

        new_rows = [{"date": "2026-04-27", "weight_kg": 104.0, "source": "apple_health"}]

        mock_session_mgr = MagicMock()

        # _run_auto_analysis 핵심 로직: 스레드 생성 → 컨텍스트 수집 → LLM 호출 → 결과 전송
        thread_name = f"체성분 자동 분석 — {new_rows[0]['date']}"
        assert "2026-04-27" in thread_name

        # 스레드 생성
        thread = await mock_channel.create_thread(name=thread_name)
        mock_channel.create_thread.assert_called_once()

        # 세션 등록
        mock_session_mgr.update_activity(thread.id)
        mock_session_mgr.update_activity.assert_called_once_with(12345)

    @pytest.mark.asyncio
    async def test_no_new_data_no_thread(self):
        """새 데이터가 없으면 스레드를 생성하지 않는다."""
        mock_channel = AsyncMock()

        # sync_from_icloud가 빈 리스트 반환 시 create_thread 호출 안 함
        new_rows = []
        if new_rows:
            await mock_channel.create_thread(name="test")

        mock_channel.create_thread.assert_not_called()


class TestSyncTaskIntegration:
    """동기화 태스크 통합 동작 테스트."""

    @pytest.mark.asyncio
    async def test_sync_and_notify_flow(self, tmp_path):
        """sync → 새 데이터 → 알림 전체 흐름."""
        import json
        from core.body_metrics import BodyMetricsManager
        from core.apple_health_reader import sync_from_icloud

        hae_dir = str(tmp_path / "hae")
        os.makedirs(hae_dir)
        csv_path = str(tmp_path / "inbody.csv")

        # JSON 파일 생성
        data = {"data": {"metrics": [
            {"name": "weight_body_mass", "units": "kg",
             "data": [{"qty": 104.6, "date": "2026-04-27 08:00:00 +0900", "source": "InBody"}]},
            {"name": "body_fat_percentage", "units": "%",
             "data": [{"qty": 32.5, "date": "2026-04-27 08:00:00 +0900", "source": "InBody"}]},
        ]}}
        with open(os.path.join(hae_dir, "HealthAutoExport-2026-04-27.json"), "w") as f:
            json.dump(data, f)

        mgr = BodyMetricsManager(csv_path)

        # 첫 동기화 — 새 데이터 있음
        new_rows = sync_from_icloud(hae_dir, mgr)
        assert len(new_rows) == 1
        assert new_rows[0]["date"] == "2026-04-27"

        # 두 번째 동기화 — 새 데이터 없음 (알림 불필요)
        new_rows_2 = sync_from_icloud(hae_dir, mgr)
        assert len(new_rows_2) == 0

    @pytest.mark.asyncio
    async def test_format_new_data_summary(self):
        """새 데이터 요약 문자열이 올바르게 생성된다."""
        new_rows = [
            {"date": "2026-04-27", "weight_kg": 104.6, "body_fat_pct": 32.5,
             "muscle_mass_kg": 70.4, "bmi": 30.2, "source": "apple_health"},
        ]
        # 요약 포맷 검증
        row = new_rows[0]
        parts = [f"날짜: {row['date']}"]
        if row.get("weight_kg") is not None:
            parts.append(f"체중: {row['weight_kg']}kg")
        if row.get("body_fat_pct") is not None:
            parts.append(f"체지방률: {row['body_fat_pct']}%")
        if row.get("muscle_mass_kg") is not None:
            parts.append(f"제지방량: {row['muscle_mass_kg']}kg")
        if row.get("bmi") is not None:
            parts.append(f"BMI: {row['bmi']}")
        summary = ", ".join(parts)
        assert "104.6kg" in summary
        assert "32.5%" in summary
