"""대화 장기기억 — stdlib sqlite3 + FTS5 전문 검색 인덱스.

경로 주입 가능(SessionIndex(db_path)) — 모듈 레벨 하드코딩 경로 없음(tmp_path 테스트 대응).
FTS5는 ALTER를 지원하지 않아 스키마(turn_id 포함)를 처음부터 확정한다.
연결은 op 단위로 열고 닫아(WAL 모드) asyncio.to_thread 오프로드 시 스레드 안전을 확보한다.
"""
import hashlib
import os
import sqlite3

# 스키마 버전 마커 (PRAGMA user_version). FTS5 컬럼 추가는 불가하므로 마이그레이션 시 참조.
SCHEMA_VERSION = 1

# content는 FTS5 테이블에서 인덱싱되는 유일한 컬럼(0-based index 4). snippet() 대상.
_CONTENT_COL = 4


class SessionIndex:
    """스레드 대화를 FTS5로 색인하고 bm25 랭킹으로 검색한다."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure_schema(self):
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        conn = self._connect()
        try:
            # turn_id/thread_id/ts/role는 UNINDEXED — content만 전문 색인.
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS messages USING fts5("
                "turn_id UNINDEXED, thread_id UNINDEXED, ts UNINDEXED, role UNINDEXED, content)"
            )
            # (thread_id, ts, role, content) 해시로 멱등 색인 — backfill 재실행 시 중복 방지.
            conn.execute(
                "CREATE TABLE IF NOT EXISTS message_keys (hash TEXT PRIMARY KEY)"
            )
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _hash(thread_id, ts, role, content) -> str:
        key = "\x00".join([str(thread_id), str(ts), str(role), str(content)])
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    def index_message(self, thread_id, ts, role, content, turn_id=None) -> bool:
        """단일 메시지를 색인. 빈 content는 스킵, (thread_id,ts,role,content) 중복은 무시.

        반환: 실제로 새 행이 색인되면 True, 스킵/중복이면 False.
        """
        if content is None or not str(content).strip():
            return False
        h = self._hash(thread_id, ts, role, content)
        conn = self._connect()
        try:
            cur = conn.execute(
                "INSERT OR IGNORE INTO message_keys(hash) VALUES(?)", (h,)
            )
            if cur.rowcount == 0:
                return False  # 이미 색인됨
            conn.execute(
                "INSERT INTO messages(turn_id, thread_id, ts, role, content) "
                "VALUES(?, ?, ?, ?, ?)",
                (
                    str(turn_id) if turn_id is not None else None,
                    str(thread_id),
                    str(ts),
                    str(role),
                    str(content),
                ),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    @staticmethod
    def _prepare_query(query: str) -> str:
        """자유 텍스트를 FTS5 MATCH 식으로 변환.

        각 토큰을 인용(특수문자 오류 방지) + prefix(`*`) 매칭한다. prefix는 한국어 조사
        (효율→효율이/효율을)로 인해 정확 토큰 매칭이 빗나가는 문제를 완화한다.
        """
        terms = [t for t in query.split() if t]
        if not terms:
            return '""'
        return " ".join('"' + t.replace('"', '""') + '"*' for t in terms)

    def search(self, query: str, limit: int = 10, mode: str = "verbatim") -> list[dict]:
        """content를 전문 검색해 bm25 오름차순(관련도순)으로 반환.

        각 행: turn_id, thread_id, ts, role, content, snippet(하이라이트), score(bm25).
        mode="verbatim"이 필수 경로 — 원문 행을 그대로 반환한다.
        mode="digest"는 호출자 측 LLM 요약을 위한 예약 값으로, 인덱스는 언제나 verbatim 행을
        반환한다(요약은 상위 계층 책임 — 인덱스는 순수 sqlite 레이어로 유지).
        """
        if not query or not query.strip():
            return []
        match_expr = self._prepare_query(query)
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT turn_id, thread_id, ts, role, content, "
                f"snippet(messages, {_CONTENT_COL}, '[', ']', '…', 10) AS snippet, "
                "bm25(messages) AS score "
                "FROM messages WHERE messages MATCH ? "
                "ORDER BY bm25(messages) LIMIT ?",
                (match_expr, limit),
            ).fetchall()
        finally:
            conn.close()
        return [
            {
                "turn_id": r[0],
                "thread_id": r[1],
                "ts": r[2],
                "role": r[3],
                "content": r[4],
                "snippet": r[5],
                "score": r[6],
            }
            for r in rows
        ]

    def backfill(self, rows) -> int:
        """행 시퀀스를 일괄 색인(멱등). 각 행은 thread_id/ts/role/content(+turn_id) 키를 가진 dict.

        index_message의 해시 중복 방지를 재사용하므로 2회 호출해도 중복이 생기지 않는다.
        반환: 새로 색인된 행 수.
        """
        count = 0
        for row in rows:
            ok = self.index_message(
                row.get("thread_id"),
                row.get("ts"),
                row.get("role"),
                row.get("content"),
                row.get("turn_id"),
            )
            if ok:
                count += 1
        return count
