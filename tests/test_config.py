"""Test suite for the configuration loader."""

import os
from unittest import mock

from contextmesh.config import DEFAULTS, Config, load_config


class TestConfigLoader:
    def test_defaults_without_file(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)  # no config.yaml here
        config = load_config()
        assert config.get("compression", "default_budget_tokens") == 8000
        assert config.source is None

    def test_file_values_merge_over_defaults(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "config.yaml").write_text(
            "compression:\n  default_budget_tokens: 4000\n", encoding="utf-8"
        )
        config = load_config()
        assert config.get("compression", "default_budget_tokens") == 4000
        # untouched defaults survive
        assert config.get("scorer", "cache_size") == 10000

    def test_env_override_wins(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        with mock.patch.dict(os.environ, {
            "CONTEXTMESH_COMPRESSION_DEFAULT_BUDGET_TOKENS": "2000",
            "CONTEXTMESH_DATABASE_URL": "postgresql://x:y@h:5432/db",
            "CONTEXTMESH_CHUNKER_CODE_MAX_CHUNK_TOKENS": "150",
        }):
            config = load_config()
        assert config.get("compression", "default_budget_tokens") == 2000
        assert config.database_url == "postgresql://x:y@h:5432/db"
        assert config.get("chunker", "code", "max_chunk_tokens") == 150

    def test_budget_for_tool(self) -> None:
        config = Config({
            "compression": {
                "default_budget_tokens": 8000,
                "tool_budgets": {"read_file": 6000},
            }
        })
        assert config.budget_for_tool("read_file") == 6000
        assert config.budget_for_tool("unknown_tool") == 8000

    def test_defaults_are_not_mutated(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        with mock.patch.dict(os.environ, {"CONTEXTMESH_PROXY_PORT": "9999"}):
            load_config()
        assert DEFAULTS["proxy"]["port"] == 8081
