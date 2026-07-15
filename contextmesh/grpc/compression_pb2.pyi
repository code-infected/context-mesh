from collections.abc import Iterable as _Iterable
from typing import ClassVar as _ClassVar

from google.protobuf import descriptor as _descriptor, message as _message
from google.protobuf.internal import containers as _containers

DESCRIPTOR: _descriptor.FileDescriptor

class CompressRequest(_message.Message):
    __slots__ = ("budget_tokens", "raw_output", "recent_steps", "session_id", "task_description", "task_id", "tool_args_json", "tool_name")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    TOOL_NAME_FIELD_NUMBER: _ClassVar[int]
    TOOL_ARGS_JSON_FIELD_NUMBER: _ClassVar[int]
    RAW_OUTPUT_FIELD_NUMBER: _ClassVar[int]
    TASK_DESCRIPTION_FIELD_NUMBER: _ClassVar[int]
    RECENT_STEPS_FIELD_NUMBER: _ClassVar[int]
    BUDGET_TOKENS_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    task_id: str
    tool_name: str
    tool_args_json: str
    raw_output: str
    task_description: str
    recent_steps: _containers.RepeatedScalarFieldContainer[str]
    budget_tokens: int
    def __init__(self, session_id: str | None = ..., task_id: str | None = ..., tool_name: str | None = ..., tool_args_json: str | None = ..., raw_output: str | None = ..., task_description: str | None = ..., recent_steps: _Iterable[str] | None = ..., budget_tokens: int | None = ...) -> None: ...

class CompressResponse(_message.Message):
    __slots__ = ("chunk_types_selected", "chunks_selected", "chunks_total", "compressed_output", "compressed_tokens", "compression_ratio", "original_tokens", "trace_id")
    COMPRESSED_OUTPUT_FIELD_NUMBER: _ClassVar[int]
    ORIGINAL_TOKENS_FIELD_NUMBER: _ClassVar[int]
    COMPRESSED_TOKENS_FIELD_NUMBER: _ClassVar[int]
    COMPRESSION_RATIO_FIELD_NUMBER: _ClassVar[int]
    CHUNKS_SELECTED_FIELD_NUMBER: _ClassVar[int]
    CHUNKS_TOTAL_FIELD_NUMBER: _ClassVar[int]
    TRACE_ID_FIELD_NUMBER: _ClassVar[int]
    CHUNK_TYPES_SELECTED_FIELD_NUMBER: _ClassVar[int]
    compressed_output: str
    original_tokens: int
    compressed_tokens: int
    compression_ratio: float
    chunks_selected: int
    chunks_total: int
    trace_id: str
    chunk_types_selected: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, compressed_output: str | None = ..., original_tokens: int | None = ..., compressed_tokens: int | None = ..., compression_ratio: float | None = ..., chunks_selected: int | None = ..., chunks_total: int | None = ..., trace_id: str | None = ..., chunk_types_selected: _Iterable[str] | None = ...) -> None: ...

class HealthRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class HealthResponse(_message.Message):
    __slots__ = ("status",)
    STATUS_FIELD_NUMBER: _ClassVar[int]
    status: str
    def __init__(self, status: str | None = ...) -> None: ...
