"""gRPC client for ContextMesh compression service.

Used by the TypeScript MCP proxy to invoke compression
over high-performance IPC.

Usage:
    from contextmesh.grpc.client import CompressionClient

    client = CompressionClient(host="localhost", port=50051)
    result = client.compress(
        session_id="s1",
        task_id="t1",
        tool_name="read_file",
        raw_output="def foo(): pass",
        task_description="find all functions",
        budget_tokens=6000,
    )
"""

from __future__ import annotations

import json
import logging
from typing import Any

import grpc

from contextmesh.grpc import compression_pb2
from contextmesh.grpc import compression_pb2_grpc

logger = logging.getLogger(__name__)


class CompressionClient:
    """gRPC client for the compression service.

    Provides a Python interface to the gRPC compression service.
    Used by the SDK and custom agent loops.

    Attributes:
        host: gRPC server host.
        port: gRPC server port.
        channel: gRPC channel instance.
    """

    def __init__(self, host: str = "localhost", port: int = 50051) -> None:
        """Initialize gRPC client.

        Args:
            host: Server hostname.
            port: Server port.
        """
        self.host = host
        self.port = port
        self.channel = grpc.insecure_channel(f"{host}:{port}")
        self.stub = compression_pb2_grpc.CompressionServiceStub(self.channel)

    def compress(
        self,
        session_id: str,
        task_id: str,
        tool_name: str,
        raw_output: str,
        task_description: str,
        tool_args: dict[str, Any] | None = None,
        recent_steps: list[str] | None = None,
        budget_tokens: int = 8000,
    ) -> dict[str, Any]:
        """Compress tool output via gRPC.

        Args:
            session_id: Session identifier.
            task_id: Task identifier.
            tool_name: Tool that produced the output.
            raw_output: Raw tool output text.
            task_description: User's task description.
            tool_args: Tool arguments as dict.
            recent_steps: Recent agent reasoning steps.
            budget_tokens: Token budget for compression.

        Returns:
            Dictionary with compression result.

        Raises:
            grpc.RpcError: If gRPC call fails.
        """
        request = compression_pb2.CompressRequest(
            session_id=session_id,
            task_id=task_id,
            tool_name=tool_name,
            tool_args_json=json.dumps(tool_args or {}),
            raw_output=raw_output,
            task_description=task_description,
            recent_steps=recent_steps or [],
            budget_tokens=budget_tokens,
        )

        try:
            response = self.stub.Compress(request)
            return {
                "compressed_output": response.compressed_output,
                "original_tokens": response.original_tokens,
                "compressed_tokens": response.compressed_tokens,
                "compression_ratio": response.compression_ratio,
                "chunks_selected": response.chunks_selected,
                "chunks_total": response.chunks_total,
                "trace_id": response.trace_id,
                "chunk_types_selected": list(response.chunk_types_selected),
            }
        except grpc.RpcError as e:
            logger.error(f"gRPC compression failed: {e}")
            raise

    def health_check(self) -> bool:
        """Check if the compression service is healthy.

        Returns:
            True if service is healthy.
        """
        try:
            response = self.stub.Health(compression_pb2.HealthRequest())
            return response.status == "healthy"
        except grpc.RpcError:
            return False

    def close(self) -> None:
        """Close the gRPC channel."""
        self.channel.close()

    def __enter__(self) -> CompressionClient:
        """Context manager entry.

        Returns:
            Self.
        """
        return self

    def __exit__(self, *args: Any) -> None:
        """Context manager exit.

        Args:
            *args: Exception info.
        """
        self.close()
