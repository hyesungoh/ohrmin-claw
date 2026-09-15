"""config.json · config.examples/*.json 이 실제 로더 검증을 통과하는지 고정."""
import json
import os

import pytest

from core.llm_config import load_llm_config

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_root_config_is_claude_default():
    config = load_llm_config(os.path.join(ROOT, "config.json"), {})
    assert config.backend == "claude"
    assert config.claude["model"] is None or isinstance(config.claude["model"], str)
    assert config.claude["skills"] == "native"


@pytest.mark.parametrize("backend", ["codex", "grok"])
def test_example_config_loads_for_backend(backend):
    path = os.path.join(ROOT, "config.examples", f"{backend}.json")
    config = load_llm_config(path, {})
    assert config.backend == backend
    assert config.skills_mode == "registry"


def test_grok_example_marks_model_as_placeholder():
    with open(os.path.join(ROOT, "config.examples", "grok.json"), encoding="utf-8") as f:
        raw = json.load(f)
    assert raw["llm"]["grok"]["model"] == "REPLACE_WITH_GROK_MODEL_ID"
