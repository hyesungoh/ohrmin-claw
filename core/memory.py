"""영구 메모리 관리 — prompts/memory.md + prompts/user.md."""
import logging
import os
import re

logger = logging.getLogger(__name__)

MAX_MEMORY_CHARS = 2200
MAX_USER_CHARS = 1375

# 프롬프트 인젝션 방지: 시스템 지시처럼 보이는 패턴 차단
_INJECTION_PATTERNS = re.compile(
    r"(system\s*override|ignore\s*(all\s*)?previous|"
    r"you\s+are\s+now|new\s+instructions|forget\s+everything|"
    r"disregard\s*(all)?|override\s+prompt)",
    re.IGNORECASE,
)

EXTRACTION_PROMPT = """\
다음 대화에서 장기적으로 기억할 만한 정보를 추��하세요.

카테고리:
- MEMORY: 환경 사실, 건강 패턴, 운동 습관, 학습 내용 (예: "사용자는 매일 5km 러닝을 함")
- USER: 사용자 선호도, 소통 스타일, 기대치 (예: "간결한 답변 선호")

형식 (해당 카테고리만 출력):
MEMORY: [내용]
USER: [내용]
NONE

저장할 내용이 없으면 NONE만 ��력하세요.
이미 알려진 사실이나 일회성 정보는 저장하지 마세요.

대화:
{conversation}"""


class MemoryManager:
    """prompts/memory.md + prompts/user.md 기반 영구 메모리."""

    def __init__(self, prompts_dir: str):
        self.prompts_dir = prompts_dir
        self._memory_path = os.path.join(prompts_dir, "memory.md")
        self._user_path = os.path.join(prompts_dir, "user.md")

    def read_memory(self) -> str:
        if os.path.exists(self._memory_path):
            with open(self._memory_path) as f:
                return f.read()
        return ""

    def read_user(self) -> str:
        if os.path.exists(self._user_path):
            with open(self._user_path) as f:
                return f.read()
        return ""

    def write_memory(self, content: str) -> None:
        content = content[:MAX_MEMORY_CHARS]
        with open(self._memory_path, "w") as f:
            f.write(content)

    def write_user(self, content: str) -> None:
        content = content[:MAX_USER_CHARS]
        with open(self._user_path, "w") as f:
            f.write(content)

    def append_memory(self, entry: str) -> bool:
        current = self.read_memory()
        new_content = f"{current}\n{entry}" if current else entry
        if len(new_content) > MAX_MEMORY_CHARS:
            logger.warning("메모리 용량 초과, 항목 무시: %s", entry[:50])
            return False
        self.write_memory(new_content)
        return True

    def append_user(self, entry: str) -> bool:
        current = self.read_user()
        new_content = f"{current}\n{entry}" if current else entry
        if len(new_content) > MAX_USER_CHARS:
            logger.warning("사용자 프로필 용량 초과, 항목 무���: %s", entry[:50])
            return False
        self.write_user(new_content)
        return True

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
            return  # 추출 실패 시 조용히 무시 (핵심 기능 아님)

        if not response or response.strip() == "NONE":
            return

        for line in response.strip().split("\n"):
            line = line.strip()
            if line.startswith("MEMORY:"):
                entry = line[len("MEMORY:"):].strip()
                if entry and not _INJECTION_PATTERNS.search(entry):
                    self.append_memory(f"- {entry}")
                elif entry:
                    logger.warning("인젝션 패턴 감지, 메모리 저장 거부: %s", entry[:80])
            elif line.startswith("USER:"):
                entry = line[len("USER:"):].strip()
                if entry and not _INJECTION_PATTERNS.search(entry):
                    self.append_user(f"- {entry}")
                elif entry:
                    logger.warning("인��션 패턴 감지, 사용자 프로필 저장 거부: %s", entry[:80])
