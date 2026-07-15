"""gRPC server exposing the compression pipeline.

Provides a gRPC interface to the Python compression pipeline,
allowing the TypeScript MCP proxy to invoke compression over
high-performance IPC.

Service definition:
    CompressionService.Compress(CompressionRequest) -> CompressResponse
    CompressionService.Health(HealthRequest) -> HealthResponse

Performance target: <80ms end-to-end for 100k token inputs

Usage:
    python grpc_server.py --port 50051
"""

from __future__ import annotations

import argparse
import json
import logging
from concurrent import futures
from typing import Any

import grpc

from contextmesh.core.chunker.base import CompressionInput
from contextmesh.core.pipeline import CompressionPipeline
from contextmesh.grpc import compression_pb2, compression_pb2_grpc

logger = logging.getLogger(__name__)


class CompressionServicer(compression_pb2_grpc.CompressionServiceServicer):
    """gRPC servicer for compression service.

    Implements the CompressionService interface defined in
    compression.proto. Wraps the CompressionPipeline.
    """

    def __init__(self, pipeline: CompressionPipeline | None = None) -> None:
        """Initialize compression servicer.

        Args:
            pipeline: Optional pipeline override; when omitted, one is
                built from config.yaml/env with tracing enabled.
        """
        from contextmesh.config import load_config

        self.config = load_config()
        if pipeline is None:
            from contextmesh.config import create_pipeline
            from contextmesh.feedback.trace_store import TraceStore

            pipeline = create_pipeline(self.config)
            pipeline.trace_store = TraceStore(
                database_url=self.config.database_url,
                batch_size=int(
                    self.config.get("feedback", "trace_batch_size", default=100)
                ),
            )
        self.pipeline = pipeline

    def Compress(
        self,
        request: compression_pb2.CompressRequest,
        context: grpc.ServicerContext,
    ) -> compression_pb2.CompressResponse:
        """Compress tool output via gRPC.

        Args:
            request: CompressRequest proto message.
            context: gRPC servicer context.

        Returns:
            CompressResponse proto message.
        """
        try:
            tool_args = self._parse_tool_args(request.tool_args_json)

            inp = CompressionInput(
                session_id=request.session_id,
                task_id=request.task_id,
                tool_name=request.tool_name,
                tool_args=tool_args,
                raw_output=request.raw_output,
                task_description=request.task_description,
                recent_steps=list(request.recent_steps),
                budget_tokens=request.budget_tokens
                or self.config.budget_for_tool(request.tool_name),
            )

            result = self.pipeline.compress(inp)

            return compression_pb2.CompressResponse(
                compressed_output=result.compressed_output,
                original_tokens=result.original_tokens,
                compressed_tokens=result.compressed_tokens,
                compression_ratio=result.compression_ratio,
                chunks_selected=result.chunks_selected,
                chunks_total=result.chunks_total,
                trace_id=result.trace_id or "",
                chunk_types_selected=result.chunk_types_selected,
            )

        except Exception as e:
            logger.error(f"Compression failed: {e}", exc_info=True)
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details(f"Compression failed: {e}")
            return compression_pb2.CompressResponse()

    def Health(
        self,
        request: compression_pb2.HealthRequest,
        context: grpc.ServicerContext,
    ) -> compression_pb2.HealthResponse:
        """Health check via gRPC.

        Args:
            request: HealthRequest proto message.
            context: gRPC servicer context.

        Returns:
            HealthResponse proto message.
        """
        return compression_pb2.HealthResponse(status="healthy")

    @staticmethod
    def _parse_tool_args(tool_args_json: str) -> dict[str, Any]:
        """Parse tool arguments from JSON string.

        Args:
            tool_args_json: JSON-encoded tool arguments.

        Returns:
            Parsed arguments dictionary.
        """
        if not tool_args_json:
            return {}

        try:
            return json.loads(tool_args_json)
        except json.JSONDecodeError:
            logger.warning(f"Failed to parse tool_args_json: {tool_args_json}")
            return {}


def create_server(
    port: int = 50051,
    max_workers: int = 4,
) -> grpc.Server:
    """Create and configure gRPC server.

    Args:
        port: Port to listen on.
        max_workers: Maximum concurrent workers.

    Returns:
        Configured gRPC server.
    """
    # Giant tool outputs (DB dumps, big files) exceed gRPC's 4MB default.
    message_limit = 64 * 1024 * 1024
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=max_workers),
        options=[
            ("grpc.max_receive_message_length", message_limit),
            ("grpc.max_send_message_length", message_limit),
        ],
    )
    servicer = CompressionServicer()
    compression_pb2_grpc.add_CompressionServiceServicer_to_server(servicer, server)
    server.add_insecure_port(f"[::]:{port}")
    return server


def serve(
    port: int = 50051,
    log_level: str = "INFO",
) -> None:
    """Start the gRPC compression server.

    Args:
        port: Port to listen on.
        log_level: Logging level.
    """
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    server = create_server(port)
    server.start()
    logger.info(f"Compression gRPC server started on port {port}")

    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        logger.info("Shutting down gRPC server")
        server.stop(0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ContextMesh gRPC Compression Server")
    parser.add_argument("--port", type=int, default=50051, help="Port to listen on")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARN", "ERROR"],
    )
    args = parser.parse_args()

    serve(port=args.port, log_level=args.log_level)
