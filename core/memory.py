"""영구 메모리 관리 — prompts/memory.md + prompts/user.md."""
import logging
import os
import re

logger = logging.getLogger(__name__)

MAX_MEMORY_CHARS = 2200
MAX_USER_CHARS = 1375
ENTRY_DELIMITER = "\n§\n"

# 프롬프트 인젝션 방지: 시스템 지시처럼 보이는 패턴 차단
_INJECTION_PATTERNS = re.compile(
    r"(system\s*override|ignore\s*(all\s*)?previous|"
    r"you\s+are\s+now|new\s+instructions|forget\s+everything|"
    r"disregard\s*(all)?|override\s+prompt)",
    re.IGNORECASE,
)

EXTRACTION_PROMPT = """\
다음 대화에서 장기적으로 기억할 만한 정보를 추출하세요.

카테고리:
- MEMORY: 환경 사실, 건강 패턴, 운동 습관, 학습 내용 (예: "사용자는 매일 5km 러닝을 함")
- USER: 사용자 선호도, 소통 스타일, 기대치 (예: "간결한 답변 선호")

형식 (해당 카테고리만 출력):
MEMORY: [내용]
USER: [내용]
NONE

저장할 내용이 없으면 NONE만 출력하세요.
이미 알려진 사실이나 일회성 정보는 저장하지 마세요.

대화:
{conversation}"""

CONSOLIDATION_PROMPT = """\
메모리 용량이 부족합니다. 기존 엔트리와 새 엔트리를 통합하여 용량 내로 압축하세요.

용량 한도: {limit}자
현재 사용: {current}자
새 엔트리: {new_entry}

기존 엔트리:
{entries}

규칙:
- 중복/유사 항목은 하나로 합치세요
- 오래되거나 덜 중요한 정보를 제거하세요
- 최신 정보와 새 엔트리를 우선 보존하세요
- 각 엔트리를 §로 구분하여 출력하세요
- 반드시 {limit}자 이내로 출력하세요"""

# 기존 '- ' 형식을 § 구분자로 마이그레이션
_DASH_LINE_PATTERN = re.compile(r"^- ", re.MULTILINE)


def _needs_migration(content: str) -> bool:
    """§ 구분자가 없고 '- ' 형식이면 마이그레이션 필요."""
    return "§" not in content and bool(_DASH_LINE_PATTERN.search(content))


def _migrate_content(content: str) -> str:
    """'- 항목\\n- 항목' → '항목§항목' 형식으로 변환."""
    lines = content.strip().split("\n")
    entries = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("- "):
            entries.append(stripped[2:])
        elif stripped:
            entries.append(stripped)
    return ENTRY_DELIMITER.join(entries)


class MemoryManager:
    """prompts/memory.md + prompts/user.md 기반 영구 메모리."""

    def __init__(self, prompts_dir: str):
        self.prompts_dir = prompts_dir
        self._memory_path = os.path.join(prompts_dir, "memory.md")
        self._user_path = os.path.join(prompts_dir, "user.md")

    def _path_for(self, target: str) -> str:
        if target == "memory":
            return self._memory_path
        if target == "user":
            return self._user_path
        raise ValueError(f"target은 'memory' 또는 'user'여야 합니다: {target}")

    def _limit_for(self, target: str) -> int:
        return MAX_MEMORY_CHARS if target == "memory" else MAX_USER_CHARS

    def read_memory(self) -> str:
        return self._read_raw("memory")

    def read_user(self) -> str:
        return self._read_raw("user")

    def write_memory(self, content: str) -> None:
        self._write_raw("memory", content)

    def write_user(self, content: str) -> None:
        self._write_raw("user", content)

    # ── 내부 I/O ──

    def _read_raw(self, target: str) -> str:
        path = self._path_for(target)
        if not os.path.exists(path):
            return ""
        with open(path) as f:
            return f.read()

    def _write_raw(self, target: str, content: str) -> None:
        limit = self._limit_for(target)
        content = content[:limit]
        with open(self._path_for(target), "w") as f:
            f.write(content)

    def _parse_entries(self, content: str) -> list[str]:
        if not content.strip():
            return []
        return [e.strip() for e in content.split("§") if e.strip()]

    # ── 엔트리 관리 (§ 구분자 기반) ──

    def list_entries(self, target: str) -> list[dict]:
        """엔트리 목록 반환. 필요시 자동 마이그레이션."""
        raw = self._read_raw(target)  # _path_for 검증 포함
        if not raw.strip():
            return []
        if _needs_migration(raw):
            raw = _migrate_content(raw)
            self._write_raw(target, raw)
        entries = self._parse_entries(raw)
        return [{"index": i, "content": e} for i, e in enumerate(entries)]

    def replace_entry(self, target: str, index: int, new_content: str) -> dict:
        """특정 엔트리를 교체."""
        if _INJECTION_PATTERNS.search(new_content):
            return {"success": False, "error": "인젝션 패턴 감지"}
        entries = self.list_entries(target)
        if index < 0 or index >= len(entries):
            return {"success": False, "error": f"인덱스 범위 초과 (0~{len(entries) - 1})"}
        contents = [e["content"] for e in entries]
        contents[index] = new_content
        new_raw = ENTRY_DELIMITER.join(contents)
        limit = self._limit_for(target)
        if len(new_raw) > limit:
            return {"success": False, "error": f"용량 초과 ({len(new_raw)}/{limit})"}
        self._write_raw(target, new_raw)
        return {"success": True}

    def remove_entry(self, target: str, index: int) -> dict:
        """특정 엔트리를 삭제."""
        entries = self.list_entries(target)
        if index < 0 or index >= len(entries):
            return {"success": False, "error": f"인덱스 범위 초과 (0~{len(entries) - 1})"}
        contents = [e["content"] for e in entries]
        contents.pop(index)
        new_raw = ENTRY_DELIMITER.join(contents) if contents else ""
        self._write_raw(target, new_raw)
        return {"success": True}

    # ── append (하위 호환 + 상세 에러) ──

    def append_memory(self, entry: str) -> dict:
        return self._append("memory", entry)

    def append_user(self, entry: str) -> dict:
        return self._append("user", entry)

    def _append(self, target: str, entry: str) -> dict:
        if _INJECTION_PATTERNS.search(entry):
            return {"success": False, "error": "인젝션 패턴 감지"}
        current = self._read_raw(target)
        if _needs_migration(current):
            current = _migrate_content(current)
            self._write_raw(target, current)
        new_content = f"{current}{ENTRY_DELIMITER}{entry}" if current.strip() else entry
        limit = self._limit_for(target)
        if len(new_content) > limit:
            logger.warning("용량 초과, 항목 무시: %s", entry[:50])
            entries = self.list_entries(target)
            return {
                "success": False,
                "current_chars": len(current),
                "limit": limit,
                "entries": entries,
            }
        self._write_raw(target, new_content)
        return {"success": True}

    # ── LLM 연동 ──

    async def extract_and_save(self, llm, conversation: list[dict]) -> None:
        """LLM으로 대화에서 기억할 정보를 추출하여 저장."""
        conv_text = "\n".join(
            f"{'사용자' if m['role'] == 'user' else '어시스턴트'}: {m.get('content', '')}"
            for m in conversation
        )
        prompt = EXTRACTION_PROMPT.format(conversation=conv_text)
        try:
            response = await llm.ask("메모리 추출기", prompt)
        except Exception:
            return

        if not response or response.strip() == "NONE":
            return

        for line in response.strip().split("\n"):
            line = line.strip()
            if line.startswith("MEMORY:"):
                entry = line[len("MEMORY:"):].strip()
                if entry:
                    await self._save_or_consolidate(llm, "memory", entry)
            elif line.startswith("USER:"):
                entry = line[len("USER:"):].strip()
                if entry:
                    await self._save_or_consolidate(llm, "user", entry)

    async def _save_or_consolidate(self, llm, target: str, entry: str) -> None:
        """추가 시도 → 실패 시 LLM으로 통합."""
        result = self._append(target, entry)
        if result["success"]:
            return
        if "entries" not in result:
            return  # 인젝션 거부 등 통합 불필요
        # 용량 초과 → LLM에게 통합 요청
        entries = result["entries"]
        limit = result["limit"]
        current = result["current_chars"]
        entries_text = "\n".join(
            f"[{e['index']}] {e['content']}" for e in entries
        )
        consolidation_prompt = CONSOLIDATION_PROMPT.format(
            limit=limit, current=current,
            new_entry=entry, entries=entries_text,
        )
        try:
            consolidated = await llm.ask("메모리 통합기", consolidation_prompt)
        except Exception:
            logger.warning("메모리 통합 실패, 항목 무시: %s", entry[:50])
            return
        if consolidated and consolidated.strip():
            cleaned = consolidated.strip()[:limit]
            if _INJECTION_PATTERNS.search(cleaned):
                logger.warning("통합 결과에 인젝션 패턴 감지, 무시")
                return
            self._write_raw(target, cleaned)
