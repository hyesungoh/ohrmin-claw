"""FTS5 세션 인덱스 테스트 — 경로 주입(tmp_path), bm25 랭킹, snippet, 멱등 backfill."""
import sqlite3

import pytest

from core.session_index import SessionIndex, SCHEMA_VERSION


@pytest.fixture
def index(tmp_path):
    return SessionIndex(str(tmp_path / "session_index.db"))


class TestIndexAndSearch:
    def test_index_then_search_returns_row(self, index):
        index.index_message("t1", "2026-01-01T00:00:00", "user", "오늘 수면 효율이 어땠어?")
        results = index.search("수면 효율")
        assert len(results) >= 1
        assert results[0]["thread_id"] == "t1"
        assert results[0]["role"] == "user"
        assert "수면" in results[0]["content"]

    def test_search_empty_query_returns_empty(self, index):
        index.index_message("t1", "ts", "user", "내용")
        assert index.search("") == []
        assert index.search("   ") == []

    def test_search_no_match_returns_empty(self, index):
        index.index_message("t1", "ts", "user", "러닝 페이스 분석")
        assert index.search("존재하지않는키워드") == []


class TestBm25Ordering:
    def test_more_occurrences_ranks_higher(self, index):
        # 3회 등장 문서가 1회 등장 문서보다 상위여야 함(bm25 오름차순).
        index.index_message("t1", "ts1", "user", "수면 수면 수면")
        index.index_message("t1", "ts2", "user", "수면 기타 기타 기타")
        results = index.search("수면")
        assert len(results) == 2
        assert results[0]["content"] == "수면 수면 수면"
        assert results[1]["content"] == "수면 기타 기타 기타"


class TestSnippet:
    def test_snippet_highlight_markup_present(self, index):
        index.index_message("t1", "ts", "user", "어제 러닝 페이스와 수면 효율을 분석해줘")
        results = index.search("수면")
        assert results
        snippet = results[0]["snippet"]
        # snippet()이 매치 토큰을 대괄호로 하이라이트한다.
        assert "[수면]" in snippet


class TestEmptyContentFiltered:
    def test_empty_content_not_indexed(self, index):
        assert index.index_message("t1", "ts", "user", "") is False
        assert index.index_message("t1", "ts", "user", "   ") is False
        assert index.index_message("t1", "ts", "user", None) is False
        # 색인된 게 없어야 함
        assert index.search("아무거나") == []


class TestBackfillIdempotent:
    def test_backfill_twice_no_dup(self, index):
        rows = [
            {"thread_id": "t1", "ts": "ts1", "role": "user", "content": "수면 분석 부탁", "turn_id": "m1"},
            {"thread_id": "t1", "ts": "ts2", "role": "assistant", "content": "수면 효율 88% 입니다", "turn_id": "m1"},
        ]
        first = index.backfill(rows)
        second = index.backfill(rows)
        assert first == 2
        assert second == 0  # 멱등 — 재실행 시 새 행 없음
        results = index.search("수면")
        # 중복 없이 원래 2행만
        assert len(results) == 2

    def test_index_message_dedup(self, index):
        assert index.index_message("t1", "ts", "user", "동일 메시지") is True
        assert index.index_message("t1", "ts", "user", "동일 메시지") is False


class TestThreadKeying:
    def test_first_message_keyed_to_thread_id(self, index):
        # 첫 채널 메시지가 생성된 thread.id로 키잉되면 그 스레드에서 검색된다.
        index.index_message("thread-999", "ts", "user", "오늘 웨이트 트레이닝 어땠어", "msg-1")
        results = index.search("웨이트")
        assert len(results) == 1
        assert results[0]["thread_id"] == "thread-999"
        assert results[0]["turn_id"] == "msg-1"


class TestSchema:
    def test_schema_version_marker(self, tmp_path):
        db = str(tmp_path / "s.db")
        SessionIndex(db)
        conn = sqlite3.connect(db)
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        finally:
            conn.close()
        assert version == SCHEMA_VERSION

    def test_path_injectable_and_persists(self, tmp_path):
        db = str(tmp_path / "nested" / "dir" / "s.db")
        idx = SessionIndex(db)
        idx.index_message("t1", "ts", "user", "영구 저장 확인")
        # 새 인스턴스로 재오픈해도 데이터 유지
        idx2 = SessionIndex(db)
        assert idx2.search("영구")
