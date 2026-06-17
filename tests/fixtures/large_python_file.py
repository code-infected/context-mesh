"""Authentication module for ContextMesh testing.

This is a large Python file designed to test the code chunker.
It contains multiple functions, classes, and import blocks
to verify that the chunker correctly segments code into
semantic units.
"""

import os
import sys
import hashlib
import secrets
import logging
from typing import Optional, Dict, List, Tuple, Any
from datetime import datetime, timedelta
from enum import Enum
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class AuthError(Exception):
    """Base exception for authentication errors."""

    def __init__(self, message: str, code: str = "AUTH_ERROR") -> None:
        super().__init__(message)
        self.code = code


class TokenExpiredError(AuthError):
    """Raised when a token has expired."""

    def __init__(self, message: str = "Token has expired") -> None:
        super().__init__(message, code="TOKEN_EXPIRED")


class InvalidTokenError(AuthError):
    """Raised when a token is invalid."""

    def __init__(self, message: str = "Invalid token") -> None:
        super().__init__(message, code="INVALID_TOKEN")


class PermissionDeniedError(AuthError):
    """Raised when a user lacks required permissions."""

    def __init__(self, message: str = "Permission denied") -> None:
        super().__init__(message, code="PERMISSION_DENIED")


class TokenType(Enum):
    """Types of authentication tokens."""

    ACCESS = "access"
    REFRESH = "refresh"
    API_KEY = "api_key"
    SESSION = "session"


class PermissionLevel(Enum):
    """Permission levels for authorization."""

    READ = "read"
    WRITE = "write"
    ADMIN = "admin"
    SUPER_ADMIN = "super_admin"


@dataclass
class User:
    """User model for authentication."""

    id: str
    username: str
    email: str
    password_hash: str
    permissions: List[PermissionLevel] = field(default_factory=list)
    is_active: bool = True
    created_at: datetime = field(default_factory=datetime.now)
    last_login: Optional[datetime] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Token:
    """Authentication token model."""

    value: str
    user_id: str
    token_type: TokenType
    expires_at: datetime
    created_at: datetime = field(default_factory=datetime.now)
    is_revoked: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Session:
    """User session model."""

    id: str
    user_id: str
    token: str
    expires_at: datetime
    created_at: datetime = field(default_factory=datetime.now)
    last_accessed: datetime = field(default_factory=datetime.now)
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None


class PasswordHasher:
    """Secure password hashing utility.

    Uses bcrypt-style hashing with salt and multiple rounds.
    """

    DEFAULT_ROUNDS = 12
    SALT_LENGTH = 32

    @classmethod
    def hash(cls, password: str, rounds: int = DEFAULT_ROUNDS) -> str:
        """Hash a password with salt.

        Args:
            password: Plain text password.
            rounds: Number of hashing rounds.

        Returns:
            Hashed password string.
        """
        salt = secrets.token_hex(cls.SALT_LENGTH)
        hashed = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            100000 * rounds,
        )
        return f"{rounds}${salt}${hashed.hex()}"

    @classmethod
    def verify(cls, password: str, hashed: str) -> bool:
        """Verify a password against a hash.

        Args:
            password: Plain text password to verify.
            hashed: Hashed password string.

        Returns:
            True if password matches.
        """
        try:
            rounds, salt, _ = hashed.split("$", 2)
            rounds = int(rounds)
        except ValueError:
            return False

        expected = cls.hash(password, rounds)
        return secrets.compare_digest(expected, hashed)


class TokenManager:
    """Manages authentication token lifecycle.

    Handles token creation, validation, refresh, and revocation.
    """

    DEFAULT_ACCESS_TTL = timedelta(hours=1)
    DEFAULT_REFRESH_TTL = timedelta(days=30)
    TOKEN_LENGTH = 64

    def __init__(self) -> None:
        """Initialize token manager."""
        self._tokens: Dict[str, Token] = {}
        self._revoked: set[str] = set()

    def create_token(
        self,
        user_id: str,
        token_type: TokenType = TokenType.ACCESS,
        ttl: Optional[timedelta] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Token:
        """Create a new authentication token.

        Args:
            user_id: User identifier.
            token_type: Type of token to create.
            ttl: Time to live for the token.
            metadata: Additional token metadata.

        Returns:
            Created Token object.
        """
        if ttl is None:
            ttl = (
                self.DEFAULT_ACCESS_TTL
                if token_type == TokenType.ACCESS
                else self.DEFAULT_REFRESH_TTL
            )

        value = secrets.token_hex(self.TOKEN_LENGTH)
        now = datetime.now()

        token = Token(
            value=value,
            user_id=user_id,
            token_type=token_type,
            expires_at=now + ttl,
            metadata=metadata or {},
        )

        self._tokens[value] = token
        logger.info(f"Created {token_type.value} token for user {user_id}")
        return token

    def validate_token(self, token_value: str) -> Token:
        """Validate a token and return it.

        Args:
            token_value: Token string to validate.

        Returns:
            Valid Token object.

        Raises:
            InvalidTokenError: If token is invalid.
            TokenExpiredError: If token has expired.
        """
        if token_value in self._revoked:
            raise InvalidTokenError("Token has been revoked")

        token = self._tokens.get(token_value)
        if token is None:
            raise InvalidTokenError("Token not found")

        if token.is_revoked:
            raise InvalidTokenError("Token has been revoked")

        if datetime.now() > token.expires_at:
            token.is_revoked = True
            raise TokenExpiredError(
                f"Token expired at {token.expires_at.isoformat()}"
            )

        return token

    def refresh_token(self, token_value: str) -> Token:
        """Refresh an access token using a refresh token.

        Args:
            token_value: Refresh token string.

        Returns:
            New access Token object.

        Raises:
            InvalidTokenError: If refresh token is invalid.
        """
        refresh_token = self.validate_token(token_value)

        if refresh_token.token_type != TokenType.REFRESH:
            raise InvalidTokenError("Not a refresh token")

        self._revoked.add(token_value)
        refresh_token.is_revoked = True

        return self.create_token(
            user_id=refresh_token.user_id,
            token_type=TokenType.ACCESS,
            metadata=refresh_token.metadata,
        )

    def revoke_token(self, token_value: str) -> None:
        """Revoke a token.

        Args:
            token_value: Token string to revoke.
        """
        if token_value in self._tokens:
            self._tokens[token_value].is_revoked = True
        self._revoked.add(token_value)
        logger.info(f"Revoked token {token_value[:8]}...")

    def cleanup_expired(self) -> int:
        """Remove expired tokens from storage.

        Returns:
            Number of tokens cleaned up.
        """
        now = datetime.now()
        expired = [
            v for v, t in self._tokens.items()
            if t.expires_at < now and not t.is_revoked
        ]

        for value in expired:
            del self._tokens[value]

        logger.info(f"Cleaned up {len(expired)} expired tokens")
        return len(expired)


class SessionManager:
    """Manages user sessions.

    Handles session creation, validation, and cleanup.
    """

    DEFAULT_SESSION_TTL = timedelta(hours=8)
    MAX_SESSIONS_PER_USER = 5

    def __init__(self) -> None:
        """Initialize session manager."""
        self._sessions: Dict[str, Session] = {}
        self._user_sessions: Dict[str, List[str]] = {}

    def create_session(
        self,
        user_id: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Session:
        """Create a new user session.

        Args:
            user_id: User identifier.
            ip_address: Client IP address.
            user_agent: Client user agent string.

        Returns:
            Created Session object.
        """
        existing = self._user_sessions.get(user_id, [])
        if len(existing) >= self.MAX_SESSIONS_PER_USER:
            oldest = existing.pop(0)
            if oldest in self._sessions:
                del self._sessions[oldest]

        session_id = secrets.token_hex(32)
        session = Session(
            id=session_id,
            user_id=user_id,
            token=secrets.token_hex(64),
            expires_at=datetime.now() + self.DEFAULT_SESSION_TTL,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        self._sessions[session_id] = session
        self._user_sessions.setdefault(user_id, []).append(session_id)

        logger.info(f"Created session for user {user_id}")
        return session

    def validate_session(self, session_id: str) -> Session:
        """Validate a session and return it.

        Args:
            session_id: Session identifier.

        Returns:
            Valid Session object.

        Raises:
            InvalidTokenError: If session is invalid or expired.
        """
        session = self._sessions.get(session_id)
        if session is None:
            raise InvalidTokenError("Session not found")

        if datetime.now() > session.expires_at:
            self._remove_session(session)
            raise TokenExpiredError("Session has expired")

        session.last_accessed = datetime.now()
        return session

    def _remove_session(self, session: Session) -> None:
        """Remove a session from storage.

        Args:
            session: Session to remove.
        """
        if session.id in self._sessions:
            del self._sessions[session.id]

        user_sessions = self._user_sessions.get(session.user_id, [])
        if session.id in user_sessions:
            user_sessions.remove(session.id)

    def revoke_session(self, session_id: str) -> None:
        """Revoke a user session.

        Args:
            session_id: Session identifier.
        """
        session = self._sessions.get(session_id)
        if session:
            self._remove_session(session)
            logger.info(f"Revoked session {session_id[:8]}...")

    def revoke_all_sessions(self, user_id: str) -> int:
        """Revoke all sessions for a user.

        Args:
            user_id: User identifier.

        Returns:
            Number of sessions revoked.
        """
        session_ids = self._user_sessions.get(user_id, [])
        count = len(session_ids)

        for session_id in session_ids:
            if session_id in self._sessions:
                del self._sessions[session_id]

        self._user_sessions.pop(user_id, None)
        logger.info(f"Revoked {count} sessions for user {user_id}")
        return count

    def cleanup_expired_sessions(self) -> int:
        """Remove expired sessions.

        Returns:
            Number of sessions cleaned up.
        """
        now = datetime.now()
        expired = [
            s for s in self._sessions.values()
            if s.expires_at < now
        ]

        for session in expired:
            self._remove_session(session)

        logger.info(f"Cleaned up {len(expired)} expired sessions")
        return len(expired)


class AuthenticationService:
    """Main authentication service.

    Coordinates password hashing, token management,
    and session management for user authentication.
    """

    def __init__(self) -> None:
        """Initialize authentication service."""
        self.password_hasher = PasswordHasher()
        self.token_manager = TokenManager()
        self.session_manager = SessionManager()
        self._users: Dict[str, User] = {}

    def register_user(
        self,
        username: str,
        email: str,
        password: str,
        permissions: Optional[List[PermissionLevel]] = None,
    ) -> User:
        """Register a new user.

        Args:
            username: User's username.
            email: User's email address.
            password: User's password.
            permissions: Initial permission levels.

        Returns:
            Created User object.

        Raises:
            AuthError: If username or email already exists.
        """
        for user in self._users.values():
            if user.username == username:
                raise AuthError(f"Username '{username}' already exists")
            if user.email == email:
                raise AuthError(f"Email '{email}' already exists")

        user_id = secrets.token_hex(16)
        password_hash = self.password_hasher.hash(password)

        user = User(
            id=user_id,
            username=username,
            email=email,
            password_hash=password_hash,
            permissions=permissions or [PermissionLevel.READ],
        )

        self._users[user_id] = user
        logger.info(f"Registered user {username} (id={user_id[:8]}...)")
        return user

    def authenticate(
        self,
        username: str,
        password: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> Tuple[User, Session, Token]:
        """Authenticate a user with username and password.

        Args:
            username: User's username.
            password: User's password.
            ip_address: Client IP address.
            user_agent: Client user agent string.

        Returns:
            Tuple of (User, Session, Token).

        Raises:
            AuthError: If authentication fails.
        """
        user = self._find_user_by_username(username)
        if user is None:
            raise AuthError("Invalid username or password")

        if not user.is_active:
            raise AuthError("User account is disabled")

        if not self.password_hasher.verify(password, user.password_hash):
            raise AuthError("Invalid username or password")

        user.last_login = datetime.now()

        session = self.session_manager.create_session(
            user_id=user.id,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        access_token = self.token_manager.create_token(
            user_id=user.id,
            token_type=TokenType.ACCESS,
        )

        refresh_token = self.token_manager.create_token(
            user_id=user.id,
            token_type=TokenType.REFRESH,
        )

        logger.info(f"User {username} authenticated successfully")
        return user, session, access_token

    def verify_token(self, token_value: str) -> User:
        """Verify a token and return the associated user.

        Args:
            token_value: Token string to verify.

        Returns:
            User associated with the token.

        Raises:
            InvalidTokenError: If token is invalid.
            TokenExpiredError: If token has expired.
        """
        token = self.token_manager.validate_token(token_value)
        user = self._users.get(token.user_id)

        if user is None:
            raise InvalidTokenError("User not found")

        if not user.is_active:
            raise AuthError("User account is disabled")

        return user

    def check_permission(
        self,
        token_value: str,
        required_permission: PermissionLevel,
    ) -> bool:
        """Check if a token has the required permission.

        Args:
            token_value: Token string to check.
            required_permission: Required permission level.

        Returns:
            True if user has the required permission.

        Raises:
            PermissionDeniedError: If user lacks permission.
        """
        user = self.verify_token(token_value)

        permission_order = [
            PermissionLevel.READ,
            PermissionLevel.WRITE,
            PermissionLevel.ADMIN,
            PermissionLevel.SUPER_ADMIN,
        ]

        user_max = max(
            (permission_order.index(p) for p in user.permissions),
            default=-1,
        )
        required_idx = permission_order.index(required_permission)

        if user_max < required_idx:
            raise PermissionDeniedError(
                f"User {user.username} lacks {required_permission.value} permission"
            )

        return True

    def _find_user_by_username(self, username: str) -> Optional[User]:
        """Find a user by username.

        Args:
            username: Username to search for.

        Returns:
            User object or None.
        """
        for user in self._users.values():
            if user.username == username:
                return user
        return None

    def get_user(self, user_id: str) -> Optional[User]:
        """Get a user by ID.

        Args:
            user_id: User identifier.

        Returns:
            User object or None.
        """
        return self._users.get(user_id)

    def update_user_permissions(
        self,
        user_id: str,
        permissions: List[PermissionLevel],
    ) -> User:
        """Update a user's permissions.

        Args:
            user_id: User identifier.
            permissions: New permission levels.

        Returns:
            Updated User object.

        Raises:
            AuthError: If user not found.
        """
        user = self._users.get(user_id)
        if user is None:
            raise AuthError(f"User {user_id} not found")

        user.permissions = permissions
        logger.info(f"Updated permissions for user {user.username}")
        return user

    def deactivate_user(self, user_id: str) -> None:
        """Deactivate a user and revoke all sessions.

        Args:
            user_id: User identifier.

        Raises:
            AuthError: If user not found.
        """
        user = self._users.get(user_id)
        if user is None:
            raise AuthError(f"User {user_id} not found")

        user.is_active = False
        self.session_manager.revoke_all_sessions(user_id)
        logger.info(f"Deactivated user {user.username}")

    def cleanup(self) -> Dict[str, int]:
        """Run cleanup tasks for expired tokens and sessions.

        Returns:
            Dictionary with cleanup counts.
        """
        tokens_cleaned = self.token_manager.cleanup_expired()
        sessions_cleaned = self.session_manager.cleanup_expired_sessions()

        return {
            "tokens_cleaned": tokens_cleaned,
            "sessions_cleaned": sessions_cleaned,
        }


class AuthMiddleware:
    """Authentication middleware for request processing.

    Intercepts requests, validates tokens, and injects
    user information into the request context.
    """

    def __init__(self, auth_service: AuthenticationService) -> None:
        """Initialize auth middleware.

        Args:
            auth_service: Authentication service instance.
        """
        self.auth_service = auth_service
        self._excluded_paths: List[str] = [
            "/health",
            "/api/v1/auth/register",
            "/api/v1/auth/login",
        ]

    def add_excluded_path(self, path: str) -> None:
        """Add a path to the exclusion list.

        Args:
            path: Path to exclude from authentication.
        """
        if path not in self._excluded_paths:
            self._excluded_paths.append(path)

    def should_authenticate(self, path: str) -> bool:
        """Check if a path requires authentication.

        Args:
            path: Request path.

        Returns:
            True if authentication is required.
        """
        return not any(path.startswith(excluded) for excluded in self._excluded_paths)

    def process_request(
        self,
        path: str,
        headers: Dict[str, str],
    ) -> Optional[User]:
        """Process a request for authentication.

        Args:
            path: Request path.
            headers: Request headers.

        Returns:
            User object if authenticated, None if path is excluded.

        Raises:
            AuthError: If authentication fails.
        """
        if not self.should_authenticate(path):
            return None

        auth_header = headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            raise AuthError("Missing or invalid Authorization header")

        token_value = auth_header[7:]
        return self.auth_service.verify_token(token_value)

    def process_response(
        self,
        user: Optional[User],
        status_code: int,
    ) -> Dict[str, str]:
        """Process a response to add auth headers.

        Args:
            user: Authenticated user (if any).
            status_code: Response status code.

        Returns:
            Response headers to add.
        """
        headers: Dict[str, str] = {}

        if user:
            headers["X-User-Id"] = user.id
            headers["X-Username"] = user.username

        if status_code == 401:
            headers["WWW-Authenticate"] = "Bearer"

        return headers
