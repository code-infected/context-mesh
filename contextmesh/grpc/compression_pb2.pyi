from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable
from typing import ClassVar as _ClassVar, Optional as _Optional

DESCRIPTOR: _descriptor.FileDescriptor

class CompressRequest(_message.Message):
    __slots__ = ("session_id", "task_id", "tool_name", "tool_args_json", "raw_output", "task_description", "recent_steps", "budget_tokens")
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
    def __init__(self, session_id: _Optional[str] = ..., task_id: _Optional[str] = ..., tool_name: _Optional[str] = ..., tool_args_json: _Optional[str] = ..., raw_output: _Optional[str] = ..., task_description: _Optional[str] = ..., recent_steps: _Optional[_Iterable[str]] = ..., budget_tokens: _Optional[int] = ...) -> None: ...

class CompressResponse(_message.Message):
    __slots__ = ("compressed_output", "original_tokens", "compressed_tokens", "compression_ratio", "chunks_selected", "chunks_total", "trace_id", "chunk_types_selected")
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
    def __init__(self, compressed_output: _Optional[str] = ..., original_tokens: _Optional[int] = ..., compressed_tokens: _Optional[int] = ..., compression_ratio: _Optional[float] = ..., chunks_selected: _Optional[int] = ..., chunks_total: _Optional[int] = ..., trace_id: _Optional[str] = ..., chunk_types_selected: _Optional[_Iterable[str]] = ...) -> None: ...

class HealthRequest(_message.Message):
    __slots__ = ()
    def __init__(self) -> None: ...

class HealthResponse(_message.Message):
    __slots__ = ("status",)
    STATUS_FIELD_NUMBER: _ClassVar[int]
    status: str
    def __init__(self, status: _Optional[str] = ...) -> None: ...
