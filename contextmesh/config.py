"""Configuration loading for ContextMesh.

Loads config.yaml and applies environment variable overrides of the
form CONTEXTMESH_<SECTION>_<KEY> (e.g. CONTEXTMESH_DATABASE_URL,
CONTEXTMESH_COMPRESSION_DEFAULT_BUDGET_TOKENS). Values are parsed
with YAML semantics, so "8000" becomes an int and "true" a bool.

Search order for the config file:
    1. Explicit path argument
    2. $CONTEXTMESH_CONFIG
    3. ./config.yaml
    4. ./contextmesh.yaml

A missing file is not an error — built-in defaults apply.

Usage:
    from contextmesh.config import load_config, create_pipeline

    config = load_config()
    pipeline = create_pipeline(config)
    budget = config.budget_for_tool("read_file")
"""

from __future__ import annotations

import copy
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from contextmesh.core.pipeline import CompressionPipeline

logger = logging.getLogger(__name__)

ENV_PREFIX = "CONTEXTMESH_"

DEFAULTS: dict[str, Any] = {
    "compression": {
        "default_budget_tokens": 8000,
        "max_overhead_ms": 80,
        "hard_timeout_ms": 10000,
        "min_compression_ratio": 0.3,
        "max_chunks": 2000,
        "prefilter_half_tokens": 30000,
        "result_cache_size": 256,
        "session_dedup": False,
        "tool_budgets": {},
    },
    "scorer": {
        "model": "BAAI/bge-small-en-v1.5",
        "cache_size": 10000,
        "task_context_window": 3,
    },
    "chunker": {
        "code": {
            "max_chunk_tokens": 300,
            "min_chunk_tokens": 20,
            "languages": ["python", "typescript", "javascript", "rust", "go"],
        },
        "json": {"max_depth": 4, "min_chunk_tokens": 30},
        "log": {"event_window_ms": 100, "max_lines_per_chunk": 20},
        "html": {
            "semantic_tags": ["article", "section", "main", "aside", "nav"],
            "min_chunk_tokens": 50,
        },
        "csv": {"rows_per_chunk": 50, "group_by_cardinality_threshold": 20},
        "shell": {"min_lines_per_chunk": 3, "blank_line_separator": True},
        "markdown": {"max_chunk_tokens": 400, "min_chunk_tokens": 20},
    },
    "extractor": {
        "dependency_budget_slack": 0.15,
        "max_coherence_iterations": 3,
    },
    "feedback": {
        "enabled": True,
        "trace_batch_size": 100,
        "failure_analysis_delay_minutes": 5,
        "min_failures_before_update": 3,
        "max_multiplier": 3.0,
        "multiplier_decay_days": 30,
    },
    "proxy": {
        "port": 8081,
        "grpc_compression_port": 50051,
        "session_timeout_minutes": 60,
        "log_level": "info",
    },
    "database": {
        "url": "",
        "pool_size": 10,
        "vector_dimension": 384,
    },
    "dashboard": {
        "backend_port": 8082,
        "frontend_port": 3000,
    },
}

# Sections with one nested level whose env override needs a subsection
# token (CONTEXTMESH_CHUNKER_CODE_MAX_CHUNK_TOKENS).
_NESTED_SECTIONS: dict[str, tuple[str, ...]] = {
    "chunker": ("code", "json", "log", "html", "csv", "shell", "markdown"),
    "compression": ("tool_budgets",),
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge override into base recursively, returning a new dict."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


class Config:
    """Resolved ContextMesh configuration.

    Wraps the merged defaults + file + environment configuration and
    provides typed access helpers.
    """

    def __init__(self, data: dict[str, Any], source: str | None = None) -> None:
        self.data = data
        self.source = source

    def get(self, *path: str, default: Any = None) -> Any:
        """Get a config value by nested path.

        Args:
            path: Nested keys, e.g. get("compression", "default_budget_tokens").
            default: Value when the path is absent.

        Returns:
            The config value or default.
        """
        node: Any = self.data
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

    def budget_for_tool(self, tool_name: str) -> int:
        """Token budget for a tool, honoring per-tool overrides.

        Args:
            tool_name: Tool name as seen by the proxy/SDK.

        Returns:
            Budget in tokens.
        """
        budgets = self.get("compression", "tool_budgets", default={}) or {}
        default = self.get("compression", "default_budget_tokens", default=8000)
        value = budgets.get(tool_name, default)
        return int(value)

    @property
    def database_url(self) -> str:
        """PostgreSQL connection URL ("" when unconfigured)."""
        return str(self.get("database", "url", default="") or "")

    @property
    def feedback_enabled(self) -> bool:
        return bool(self.get("feedback", "enabled", default=True))


def _find_config_file(path: str | Path | None) -> Path | None:
    """Resolve which config file to read, if any."""
    candidates: list[Path] = []
    if path:
        candidates.append(Path(path))
    env_path = os.environ.get(ENV_PREFIX + "CONFIG")
    if env_path:
        candidates.append(Path(env_path))
    candidates.append(Path("config.yaml"))
    candidates.append(Path("contextmesh.yaml"))

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    if path:
        logger.warning("Config file %s not found; using defaults", path)
    return None


def _parse_env_value(raw: str) -> Any:
    """Parse an env var value with YAML semantics."""
    try:
        return yaml.safe_load(raw)
    except yaml.YAMLError:
        return raw


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    """Apply CONTEXTMESH_* environment overrides onto config data."""
    for name, raw in os.environ.items():
        if not name.startswith(ENV_PREFIX) or name == ENV_PREFIX + "CONFIG":
            continue

        parts = name[len(ENV_PREFIX):].lower().split("_")
        if len(parts) < 2:
            continue

        section = parts[0]
        if section not in data:
            continue

        rest = parts[1:]
        target = data[section]

        subsections = _NESTED_SECTIONS.get(section, ())
        matched_sub = None
        for sub in subsections:
            sub_parts = sub.split("_")
            if rest[: len(sub_parts)] == sub_parts and len(rest) > len(sub_parts):
                matched_sub = sub
                rest = rest[len(sub_parts):]
                break
        if matched_sub is not None:
            target = target.setdefault(matched_sub, {})

        key = "_".join(rest)
        target[key] = _parse_env_value(raw)

    return data


def load_config(path: str | Path | None = None) -> Config:
    """Load configuration from defaults, file, and environment.

    Args:
        path: Optional explicit config file path.

    Returns:
        Resolved Config.
    """
    data = copy.deepcopy(DEFAULTS)
    source = None

    config_file = _find_config_file(path)
    if config_file is not None:
        try:
            with open(config_file, encoding="utf-8") as f:
                file_data = yaml.safe_load(f) or {}
            if not isinstance(file_data, dict):
                raise TypeError(f"Top level of {config_file} must be a mapping")
            data = _deep_merge(data, file_data)
            source = str(config_file)
        except (OSError, yaml.YAMLError, TypeError) as e:
            logger.warning("Failed to load %s (%s); using defaults", config_file, e)

    data = _apply_env_overrides(data)
    return Config(data, source=source)


def create_pipeline(config: Config | None = None) -> CompressionPipeline:
    """Build a CompressionPipeline from configuration.

    Args:
        config: Resolved config; loaded from the default search path
            when omitted.

    Returns:
        Configured CompressionPipeline.
    """
    from contextmesh.core.extractor.budget_extractor import (
        BudgetExtractor,
        ExtractorConfig,
    )
    from contextmesh.core.pipeline import CompressionPipeline, PipelineConfig
    from contextmesh.core.scorer.embed_scorer import EmbedScorer
    from contextmesh.core.validator.coherence_checker import CoherenceChecker

    config = config or load_config()

    pipeline_config = PipelineConfig(
        default_budget_tokens=int(
            config.get("compression", "default_budget_tokens", default=8000)
        ),
        max_overhead_ms=int(config.get("compression", "max_overhead_ms", default=80)),
        hard_timeout_ms=int(config.get("compression", "hard_timeout_ms", default=10000)),
        min_compression_ratio=float(
            config.get("compression", "min_compression_ratio", default=0.3)
        ),
        max_chunks=int(config.get("compression", "max_chunks", default=2000)),
        prefilter_half_tokens=int(
            config.get("compression", "prefilter_half_tokens", default=30000)
        ),
        result_cache_size=int(
            config.get("compression", "result_cache_size", default=256)
        ),
        session_dedup_enabled=bool(
            config.get("compression", "session_dedup", default=False)
        ),
    )

    scorer = EmbedScorer(
        model_name=str(config.get("scorer", "model", default="BAAI/bge-small-en-v1.5")),
        cache_size=int(config.get("scorer", "cache_size", default=10000)),
    )

    extractor = BudgetExtractor(
        ExtractorConfig(
            dependency_budget_slack=float(
                config.get("extractor", "dependency_budget_slack", default=0.15)
            ),
            max_coherence_iterations=int(
                config.get("extractor", "max_coherence_iterations", default=3)
            ),
        )
    )

    validator = CoherenceChecker(
        max_iterations=int(
            config.get("extractor", "max_coherence_iterations", default=3)
        ),
        budget_slack=float(
            config.get("extractor", "dependency_budget_slack", default=0.15)
        ),
    )

    return CompressionPipeline(
        config=pipeline_config,
        scorer=scorer,
        extractor=extractor,
        validator=validator,
    )
