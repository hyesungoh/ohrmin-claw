"""SkillRegistry — 봇 측 스킬 카탈로그 + `skills` MCP 도구(load_skill/read_skill_file).

적용: Codex/Grok 항상, Claude는 `llm.claude.skills=registry`일 때만(기본 native = CLI 네이티브 스킬).
- scan(): `<skills_dir>/*/SKILL.md`(심링크 추적)의 frontmatter name/description, 이름 정렬. 누락 스킵. 매 턴 스캔.
- catalog_prompt(): system prompt에 넣는 카탈로그 블록(백엔드 무관 바이트 동일).
- load_skill 결과만 백엔드 도구 표기로 렌더(render_tool_refs — Grok). 디스크 파일은 불변.
- 읽기 전용 도구(게이트 대상 아님). core/skill_sync.py·core/learning.py와 독립.
"""
import os
from dataclasses import dataclass

from core.runtimes.tool_names import SKILLS_SERVER, render_tool_refs
from core.tool_server.spec import ParamSpec, ServerSpec, tool

SKILL_FILE = "SKILL.md"
CATALOG_HEADER = "[전문 분석 스킬]"
CATALOG_FOOTER = "필요한 스킬은 load_skill 도구로 본문을 불러와 그 절차를 따르세요. 참조 파일은 read_skill_file로 읽으세요."
_BLOCK_SCALARS = ("|", ">", "|-", ">-", "|+", ">+")


@dataclass(frozen=True)
class SkillInfo:
    name: str
    description: str
    directory: str


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def parse_skill_file(text: str) -> tuple[dict, str] | None:
    """SKILL.md → (frontmatter dict, 본문). frontmatter가 없으면 None. 단순 `key: value`(+블록 스칼라)만 지원."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return None
    meta: dict = {}
    i = 1
    while i < end:
        line = lines[i]
        i += 1
        if not line.strip() or line.startswith((" ", "\t")) or ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = value.strip()
        if value in _BLOCK_SCALARS:
            block = []
            while i < end and (lines[i].startswith((" ", "\t")) or not lines[i].strip()):
                block.append(lines[i].strip())
                i += 1
            joiner = "\n" if value.startswith("|") else " "
            value = joiner.join(b for b in block if b)
        meta[key.strip()] = _strip_quotes(value)
    body = "".join(text.splitlines(keepends=True)[end + 1:]).lstrip("\r\n")
    return meta, body


class SkillRegistry:
    def __init__(self, skills_dir: str, backend: str = "claude"):
        self.skills_dir = skills_dir
        self.backend = backend

    def scan(self) -> list[SkillInfo]:
        """스킬 목록 (frontmatter name 기준 정렬). name/description 누락 스킬은 제외."""
        if not os.path.isdir(self.skills_dir):
            return []
        skills = {}
        for entry in sorted(os.listdir(self.skills_dir)):
            directory = os.path.join(self.skills_dir, entry)
            path = os.path.join(directory, SKILL_FILE)
            if not os.path.isfile(path):
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    parsed = parse_skill_file(f.read())
            except (OSError, UnicodeDecodeError):
                continue
            if parsed is None:
                continue
            meta, _ = parsed
            name, description = meta.get("name", "").strip(), meta.get("description", "").strip()
            if not name or not description or name in skills:
                continue
            skills[name] = SkillInfo(name=name, description=description, directory=directory)
        return [skills[name] for name in sorted(skills)]

    def catalog_prompt(self) -> str:
        """카탈로그 블록. 스킬이 없으면 ""."""
        skills = self.scan()
        if not skills:
            return ""
        lines = [CATALOG_HEADER] + [f"- {s.name}: {s.description}" for s in skills] + [CATALOG_FOOTER]
        return "\n".join(lines)

    def _find(self, name: str) -> SkillInfo | None:
        return next((s for s in self.scan() if s.name == name), None)

    def load_skill(self, name: str) -> str:
        """스킬 본문(frontmatter 제외, 백엔드 도구 표기 렌더). 없으면 KeyError."""
        skill = self._find(name)
        if skill is None:
            raise KeyError(name)
        with open(os.path.join(skill.directory, SKILL_FILE), encoding="utf-8") as f:
            _, body = parse_skill_file(f.read())
        return render_tool_refs(body, self.backend)

    def read_skill_file(self, name: str, path: str) -> str:
        """스킬 디렉터리 안의 참조 파일. 절대경로·`..`·디렉터리 밖(심링크 해석 후) → ValueError, 없는 스킬 → KeyError."""
        skill = self._find(name)
        if skill is None:
            raise KeyError(name)
        if not path or os.path.isabs(path) or ".." in path.replace("\\", "/").split("/"):
            raise ValueError(path)
        base = os.path.realpath(skill.directory)
        target = os.path.realpath(os.path.join(skill.directory, path))
        if os.path.commonpath([base, target]) != base or not os.path.isfile(target):
            raise ValueError(path)
        with open(target, encoding="utf-8") as f:
            return f.read()


def _text(text: str, is_error: bool = False) -> dict:
    result = {"content": [{"type": "text", "text": text}]}
    if is_error:
        result["is_error"] = True
    return result


def create_skills_mcp_server(registry: SkillRegistry) -> ServerSpec:
    """`skills` ServerSpec — mcp__skills__load_skill · mcp__skills__read_skill_file (읽기 전용)."""

    @tool("load_skill", "전문 분석 스킬 본문을 불러온다 (카탈로그의 스킬 이름)", {
        "name": ParamSpec("string", "스킬 이름 (예: sleep-analysis)", required=True),
    })
    async def load_skill(args):
        try:
            return _text(registry.load_skill(args["name"]))
        except KeyError:
            return _text(f"스킬을 찾을 수 없습니다: {args['name']}", is_error=True)

    @tool("read_skill_file", "스킬 디렉터리 안의 참조 파일을 읽는다 (스킬 기준 상대경로)", {
        "name": ParamSpec("string", "스킬 이름", required=True),
        "path": ParamSpec("string", "스킬 디렉터리 기준 상대경로 (예: references/cutoffs.md)", required=True),
    })
    async def read_skill_file(args):
        try:
            return _text(registry.read_skill_file(args["name"], args["path"]))
        except KeyError:
            return _text(f"스킬을 찾을 수 없습니다: {args['name']}", is_error=True)
        except (ValueError, OSError, UnicodeDecodeError):
            return _text(f"스킬 디렉터리 밖이거나 읽을 수 없는 경로입니다: {args['path']}", is_error=True)

    return ServerSpec(name=SKILLS_SERVER, tools=[load_skill, read_skill_file])
