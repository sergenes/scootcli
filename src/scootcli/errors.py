"""Typed errors for scoot, so callers can react to specific failure modes.

Each error can carry a short ``hint`` with an actionable remedy for the user (surfaced by the REPL).
"""

from __future__ import annotations


class ScootError(Exception):
    """Base class for all scoot errors."""

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint


class ConfigError(ScootError):
    """Invalid or missing configuration."""


# ── Transport-level ────────────────────────────────────────────────────────────
class TransportError(ScootError):
    """Low-level transport failure (connection died, TLS failed, timed out)."""


class ProxyError(TransportError):
    """The HTTPS proxy refused the CONNECT (bad or missing credentials)."""


class NetworkError(TransportError):
    """No connectivity: DNS resolution or TCP connect failed."""


class RequestTimeout(TransportError):
    """The request timed out."""


class SSLError(TransportError):
    """TLS/certificate failure."""


# ── Auth ───────────────────────────────────────────────────────────────────────
class AuthError(ScootError):
    """Authentication failed: no OAuth token, or token exchange/JWT rejected."""


# ── API-level ──────────────────────────────────────────────────────────────────
class ApiError(ScootError):
    """The API returned an error response."""

    def __init__(self, message: str, status: int = 0, payload: object = None, hint: str = "", code: str = ""):
        super().__init__(message, hint=hint)
        self.status = status
        self.payload = payload
        self.code = code


class RateLimitError(ApiError):
    """HTTP 429 — too many requests."""


class ServerError(ApiError):
    """HTTP 5xx — server-side failure."""


class QuotaError(ApiError):
    """HTTP 402/403 or quota exhaustion: billing, quota, or permission problem."""


class ContextLengthError(ApiError):
    """The request exceeded the model's context window."""


class ModelUnavailableError(ApiError):
    """The requested model is not supported/accessible on this endpoint."""


# ── Control flow ───────────────────────────────────────────────────────────────
class Interrupted(ScootError):
    """The in-flight request/tool was cancelled by the user (ESC)."""


# Errors worth retrying with backoff (transient).
RETRIABLE = (NetworkError, RequestTimeout, RateLimitError, ServerError)



