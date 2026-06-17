"""Test fixtures for chunker tests."""

import pytest


@pytest.fixture
def sample_python_code() -> str:
    """Sample Python code for testing."""
    return '''
import os
import sys
from typing import List, Dict


def authenticate_user(token: str) -> bool:
    """Authenticate a user with their token."""
    if not token:
        return False
    return verify_token(token)


def verify_token(token: str) -> bool:
    """Verify a token is valid."""
    return len(token) > 0


def refresh_session(user_id: int) -> str:
    """Refresh a user's session."""
    return f"session_{user_id}"


class AuthMiddleware:
    """Authentication middleware for requests."""

    def __init__(self):
        self.sessions: Dict[str, str] = {}

    def process(self, token: str) -> bool:
        """Process authentication."""
        return authenticate_user(token)
'''


@pytest.fixture
def sample_json() -> str:
    """Sample JSON for testing."""
    return '''
{
    "users": [
        {"id": 1, "name": "Alice", "email": "alice@example.com"},
        {"id": 2, "name": "Bob", "email": "bob@example.com"}
    ],
    "settings": {
        "theme": "dark",
        "notifications": true
    },
    "meta": {
        "version": "1.0",
        "count": 2
    }
}
'''


@pytest.fixture
def sample_log_output() -> str:
    """Sample log output for testing."""
    return '''
2024-01-01 10:00:00 INFO Starting application
2024-01-01 10:00:01 DEBUG Loading configuration
2024-01-01 10:00:02 INFO Connected to database
2024-01-01 10:00:03 WARN Cache miss for key: user_123
2024-01-01 10:00:04 ERROR Failed to process request: timeout
2024-01-01 10:00:05 INFO Retrying request
'''


@pytest.fixture
def sample_mixed_content(sample_json: str, sample_python_code: str) -> str:
    """Sample mixed format content."""
    return f'''{sample_json}

{sample_python_code}
'''
