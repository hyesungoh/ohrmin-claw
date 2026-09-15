"""SkillRegistry — tmp_path 스킬 디렉터리(심링크 스킬 포함) · frontmatter 누락 스킵 · 경로 탈출 거부 · Grok 렌더 · skills ServerSpec."""
import json
import os

import pytest

from core.skill_registry import SkillRegistry, create_skills_mcp_server, parse_skill_file
from core.tool_server.capability import CallerCapability, wrap_handler
from core.tool_server.schema import to_strict_json_schema, validate_server_spec

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BETA_BODY = "# Beta\n\n수면은 mcp__garmin__get_sleep 으로, 체성분은 `mcp__body_metrics__get_body_metrics_history`.\n"


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


@pytest.fixture
def skills_dir(tmp_path):
    root = tmp_path / "proj"
    skills = root / ".claude" / "skills"
    _write(str(skills / "beta" / "SKILL.md"), f"---\nname: beta\ndescription: 베타 스킬 설명\ntrigger: b\n---\n\n{BETA_BODY}")
    _write(str(skills / "beta" / "references" / "cutoffs.md"), "컷오프 표\n")
    # 심링크 스킬 (.agent-made/<name> → .claude/skills/<name>, core/skill_sync.py 패턴)
    _write(str(root / ".agent-made" / "alpha" / "SKILL.md"), '---\nname: alpha\ndescription: "알파 스킬"\n---\n# Alpha\n')
    _write(str(root / ".agent-made" / "alpha" / "notes.md"), "알파 노트\n")
    os.symlink(os.path.join("..", "..", ".agent-made", "alpha"), str(skills / "alpha"))
    # frontmatter 누락 / description 누락 / SKILL.md 없음 → 스킵
    _write(str(skills / "no-frontmatter" / "SKILL.md"), "# 그냥 문서\n")
    _write(str(skills / "no-description" / "SKILL.md"), "---\nname: no-description\n---\n본문\n")
    _write(str(skills / "unclosed" / "SKILL.md"), "---\nname: unclosed\ndescription: x\n")
    os.makedirs(str(skills / "empty-dir"))
    # 블록 스칼라 description
    _write(str(skills / "gamma" / "SKILL.md"), "---\nname: gamma\ndescription: >\n  감마 첫 줄\n  둘째 줄\n---\n감마\n")
    # 스킬 밖 비밀 파일 + 스킬 안에서 밖을 가리키는 심링크
    _write(str(root / "secret.txt"), "비밀\n")
    os.symlink(str(root / "secret.txt"), str(skills / "beta" / "escape.md"))
    return str(skills)


class TestScan:
    def test_sorted_names_include_symlinked_and_skip_invalid(self, skills_dir):
        skills = SkillRegistry(skills_dir).scan()
        assert [s.name for s in skills] == ["alpha", "beta", "gamma"]
        assert [s.description for s in skills] == ["알파 스킬", "베타 스킬 설명", "감마 첫 줄 둘째 줄"]
        assert os.path.islink(skills[0].directory)

    def test_missing_dir_is_empty(self, tmp_path):
        assert SkillRegistry(str(tmp_path / "nope")).scan() == []
        assert SkillRegistry(str(tmp_path / "nope")).catalog_prompt() == ""

    def test_rescans_every_call(self, skills_dir):
        registry = SkillRegistry(skills_dir)
        assert len(registry.scan()) == 3
        _write(os.path.join(skills_dir, "delta", "SKILL.md"), "---\nname: delta\ndescription: 델타\n---\n")
        assert [s.name for s in registry.scan()] == ["alpha", "beta", "delta", "gamma"]

    def test_repo_tracked_skills_present(self):
        names = [s.name for s in SkillRegistry(os.path.join(PROJECT_ROOT, ".claude", "skills")).scan()]
        assert {"activity-evaluation", "body-composition", "science-reference", "sleep-analysis"} <= set(names)

    def test_parse_skill_file(self):
        assert parse_skill_file("no frontmatter") is None
        assert parse_skill_file("---\nname: x\n") is None
        assert parse_skill_file("---\nname: 'x'\ndescription: d: e\n---\n\n\nbody\n") == ({"name": "x", "description": "d: e"}, "body\n")


class TestCatalog:
    EXPECTED = (
        "[전문 분석 스킬]\n"
        "- alpha: 알파 스킬\n"
        "- beta: 베타 스킬 설명\n"
        "- gamma: 감마 첫 줄 둘째 줄\n"
        "필요한 스킬은 load_skill 도구로 본문을 불러와 그 절차를 따르세요. 참조 파일은 read_skill_file로 읽으세요."
    )

    @pytest.mark.parametrize("backend", ["claude", "codex", "grok"])
    def test_catalog_bytes_identical_across_backends(self, skills_dir, backend):
        assert SkillRegistry(skills_dir, backend=backend).catalog_prompt() == self.EXPECTED


class TestLoadSkill:
    @pytest.mark.parametrize("backend", ["claude", "codex"])
    def test_body_without_frontmatter_unrendered(self, skills_dir, backend):
        assert SkillRegistry(skills_dir, backend=backend).load_skill("beta") == BETA_BODY

    def test_grok_renders_tool_refs_file_unchanged(self, skills_dir):
        path = os.path.join(skills_dir, "beta", "SKILL.md")
        before = open(path, "rb").read()
        body = SkillRegistry(skills_dir, backend="grok").load_skill("beta")
        assert body == BETA_BODY.replace("mcp__garmin__get_sleep", "garmin__get_sleep").replace(
            "mcp__body_metrics__get_body_metrics_history", "body_metrics__get_body_metrics_history"
        )
        assert open(path, "rb").read() == before

    def test_symlinked_skill(self, skills_dir):
        assert SkillRegistry(skills_dir).load_skill("alpha") == "# Alpha\n"

    @pytest.mark.parametrize("name", ["missing", "no-frontmatter", "../beta"])
    def test_unknown_skill(self, skills_dir, name):
        with pytest.raises(KeyError):
            SkillRegistry(skills_dir).load_skill(name)


class TestReadSkillFile:
    def test_reference_file(self, skills_dir):
        assert SkillRegistry(skills_dir).read_skill_file("beta", "references/cutoffs.md") == "컷오프 표\n"
        assert SkillRegistry(skills_dir).read_skill_file("beta", "./references/cutoffs.md") == "컷오프 표\n"

    def test_symlinked_skill_file(self, skills_dir):
        assert SkillRegistry(skills_dir).read_skill_file("alpha", "notes.md") == "알파 노트\n"

    @pytest.mark.parametrize("path", [
        "../alpha/notes.md",
        "references/../../beta/SKILL.md",
        "..",
        "escape.md",  # 스킬 안 심링크가 밖을 가리킴 → realpath 탈출
        "references",  # 디렉터리
        "missing.md",
        "",
    ])
    def test_escape_and_invalid_rejected(self, skills_dir, path):
        with pytest.raises(ValueError):
            SkillRegistry(skills_dir).read_skill_file("beta", path)

    def test_absolute_path_rejected(self, skills_dir):
        target = os.path.join(skills_dir, "beta", "references", "cutoffs.md")
        with pytest.raises(ValueError):
            SkillRegistry(skills_dir).read_skill_file("beta", target)

    def test_unknown_skill(self, skills_dir):
        with pytest.raises(KeyError):
            SkillRegistry(skills_dir).read_skill_file("missing", "x.md")


class TestSkillsServerSpec:
    def _tools(self, skills_dir, backend="claude"):
        spec = create_skills_mcp_server(SkillRegistry(skills_dir, backend=backend))
        return spec, {t.name: t for t in spec.tools}

    def test_spec_shape_and_strict_schema(self, skills_dir):
        spec, tools = self._tools(skills_dir)
        validate_server_spec(spec)
        assert spec.name == "skills"
        assert list(tools) == ["load_skill", "read_skill_file"]
        assert to_strict_json_schema(tools["load_skill"].params) == {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "스킬 이름 (예: sleep-analysis)"}},
            "required": ["name"],
            "additionalProperties": False,
        }
        assert to_strict_json_schema(tools["read_skill_file"].params)["required"] == ["name", "path"]

    @pytest.mark.asyncio
    async def test_handlers(self, skills_dir):
        _, tools = self._tools(skills_dir, backend="grok")
        loaded = await tools["load_skill"].handler({"name": "beta"})
        assert "garmin__get_sleep" in loaded["content"][0]["text"]
        assert "is_error" not in loaded
        missing = await tools["load_skill"].handler({"name": "nope"})
        assert missing["is_error"] is True
        ref = await tools["read_skill_file"].handler({"name": "beta", "path": "references/cutoffs.md"})
        assert ref == {"content": [{"type": "text", "text": "컷오프 표\n"}]}
        escaped = await tools["read_skill_file"].handler({"name": "beta", "path": "../alpha/notes.md"})
        assert escaped["is_error"] is True
        assert "비밀" not in json.dumps(escaped, ensure_ascii=False)
        unknown = await tools["read_skill_file"].handler({"name": "nope", "path": "x"})
        assert unknown["is_error"] is True

    @pytest.mark.asyncio
    async def test_read_only_capability_allows_without_gate_log(self, skills_dir, capsys):
        spec, tools = self._tools(skills_dir)
        result = await wrap_handler(spec.name, tools["load_skill"], CallerCapability.READ_ONLY)({"name": "alpha"})
        assert result == {"content": [{"type": "text", "text": "# Alpha\n"}]}
        null_name = await wrap_handler(spec.name, tools["load_skill"], CallerCapability.READ_ONLY)({"name": None})
        assert null_name["is_error"] is True
        assert "[gate]" not in capsys.readouterr().out
