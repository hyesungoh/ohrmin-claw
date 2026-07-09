"""core/skill_sync.py — `.agent-made/` → `.claude/skills/` 심링크 동기화 테스트."""
import os

from core.skill_sync import sync_agent_made_symlinks, AGENT_MADE_DIRNAME


def _mk_agent_skill(root, name, content="---\nname: x\ndescription: y\n---\n"):
    d = os.path.join(root, AGENT_MADE_DIRNAME, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "SKILL.md"), "w") as f:
        f.write(content)


def test_creates_symlink_and_resolves(tmp_path):
    root = str(tmp_path)
    _mk_agent_skill(root, "foo")
    assert sync_agent_made_symlinks(root) == ["foo"]
    link = os.path.join(root, ".claude", "skills", "foo")
    assert os.path.islink(link)
    assert os.readlink(link) == os.path.join("..", "..", AGENT_MADE_DIRNAME, "foo")
    # SKILL.md 가 심링크를 통해 실제로 해석됨(디스커버리 전제)
    assert os.path.exists(os.path.join(link, "SKILL.md"))


def test_idempotent(tmp_path):
    root = str(tmp_path)
    _mk_agent_skill(root, "foo")
    assert sync_agent_made_symlinks(root) == ["foo"]
    assert sync_agent_made_symlinks(root) == []  # 두 번째: 새로 링크할 것 없음


def test_does_not_clobber_real_dir(tmp_path):
    """같은 이름의 실제 디렉터리(수동 스킬/science-reference)는 보호."""
    root = str(tmp_path)
    _mk_agent_skill(root, "science-reference")
    real = os.path.join(root, ".claude", "skills", "science-reference")
    os.makedirs(real, exist_ok=True)
    with open(os.path.join(real, "SKILL.md"), "w") as f:
        f.write("REAL")
    linked = sync_agent_made_symlinks(root)
    assert "science-reference" not in linked
    assert not os.path.islink(real)  # 실제 디렉터리 그대로
    with open(os.path.join(real, "SKILL.md")) as f:
        assert f.read() == "REAL"


def test_no_agent_made_dir(tmp_path):
    assert sync_agent_made_symlinks(str(tmp_path)) == []


def test_ignores_non_directories(tmp_path):
    root = str(tmp_path)
    os.makedirs(os.path.join(root, AGENT_MADE_DIRNAME))
    with open(os.path.join(root, AGENT_MADE_DIRNAME, "loose.txt"), "w") as f:
        f.write("x")
    assert sync_agent_made_symlinks(root) == []


def test_repairs_wrong_symlink(tmp_path):
    root = str(tmp_path)
    _mk_agent_skill(root, "foo")
    skills = os.path.join(root, ".claude", "skills")
    os.makedirs(skills)
    os.symlink(os.path.join("..", "..", "wrong", "foo"), os.path.join(skills, "foo"))
    assert sync_agent_made_symlinks(root) == ["foo"]  # 교정됨
    assert os.readlink(os.path.join(skills, "foo")) == os.path.join(
        "..", "..", AGENT_MADE_DIRNAME, "foo"
    )
