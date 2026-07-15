"""ContextMesh - MCP-native context compression runtime for AI agents."""

from typing import Any

__version__ = "0.1.0"

__all__ = ["CompressionMetadata", "CompressionResult", "ContextMesh", "__version__"]


def __getattr__(name: str) -> Any:
    # Lazy re-export of the SDK so `from contextmesh import ContextMesh`
    # works without importing the pipeline at package import time.
    if name in ("ContextMesh", "CompressionMetadata", "CompressionResult"):
        from contextmesh.proxy.sdk.python import contextmesh as sdk

        return getattr(sdk, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
