"""Integration test for gRPC compression service.

Starts the gRPC server in a thread, sends a compression request,
and verifies the response.

Usage:
    python tests/test_grpc_integration.py
"""

from __future__ import annotations

import threading
import time
import unittest

from contextmesh.grpc.client import CompressionClient
from contextmesh.grpc.compression_pb2_grpc import add_CompressionServiceServicer_to_server
from contextmesh.grpc.compression_pb2 import HealthRequest
from contextmesh.core.pipeline import CompressionPipeline

import grpc
from concurrent import futures


class TestGrpcIntegration(unittest.TestCase):
    """Integration tests for gRPC compression service."""

    @classmethod
    def setUpClass(cls) -> None:
        """Start gRPC server for tests."""
        from contextmesh.grpc.compression_pb2_grpc import CompressionServiceServicer
        from contextmesh.grpc import compression_pb2_grpc

        cls.port = 50052
        cls.pipeline = CompressionPipeline()

        class TestServicer(CompressionServiceServicer):
            def __init__(self) -> None:
                pass

            def Compress(self, request, context):
                from contextmesh.core.chunker.base import CompressionInput
                import json

                try:
                    tool_args = json.loads(request.tool_args_json) if request.tool_args_json else {}
                    inp = CompressionInput(
                        session_id=request.session_id,
                        task_id=request.task_id,
                        tool_name=request.tool_name,
                        tool_args=tool_args,
                        raw_output=request.raw_output,
                        task_description=request.task_description,
                        recent_steps=list(request.recent_steps),
                        budget_tokens=request.budget_tokens or 8000,
                    )
                    result = cls.pipeline.compress(inp)

                    from contextmesh.grpc import compression_pb2
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
                    context.set_code(grpc.StatusCode.INTERNAL)
                    context.set_details(str(e))
                    from contextmesh.grpc import compression_pb2
                    return compression_pb2.CompressResponse()

            def Health(self, request, context):
                from contextmesh.grpc import compression_pb2
                return compression_pb2.HealthResponse(status="healthy")

        cls.server = grpc.server(futures.ThreadPoolExecutor(max_workers=2))
        servicer = TestServicer()
        add_CompressionServiceServicer_to_server(servicer, cls.server)
        cls.server.add_insecure_port(f"[::]:{cls.port}")
        cls.server.start()
        time.sleep(0.5)

    @classmethod
    def tearDownClass(cls) -> None:
        """Stop gRPC server."""
        cls.server.stop(0)

    def test_health_check(self) -> None:
        """Test health check endpoint."""
        client = CompressionClient(port=self.port)
        try:
            result = client.health_check()
            self.assertTrue(result)
        finally:
            client.close()

    def test_compress_simple_text(self) -> None:
        """Test compression of simple text."""
        client = CompressionClient(port=self.port)
        try:
            result = client.compress(
                session_id="test-session",
                task_id="test-task",
                tool_name="read_file",
                raw_output="def foo():\n    pass\n\ndef bar():\n    pass",
                task_description="find all functions",
                budget_tokens=1000,
            )

            self.assertIn("compressed_output", result)
            self.assertIn("original_tokens", result)
            self.assertIn("compressed_tokens", result)
            self.assertIn("compression_ratio", result)
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
