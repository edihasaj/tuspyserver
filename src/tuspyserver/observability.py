"""Sentry error/crash reporting for tuspyserver.

Unhandled exceptions and errors only — no performance tracing, no profiling, no
default PII (client IP, cookies, request bodies). The DSN comes from the
``SENTRY_DSN`` env var; when it is empty, :func:`init_sentry` is a no-op, so
local and CI runs never send anything off-box unless the DSN is set explicitly.

tuspyserver is a library (a FastAPI router), so it does not call this itself.
Consumers should call :func:`init_sentry` once, as early as possible at server
startup (before creating the FastAPI app). The Sentry SDK auto-detects the
FastAPI/Starlette integrations, so unhandled request errors are captured with
route context automatically.

Example::

    from tuspyserver import init_sentry

    init_sentry()  # no-op unless SENTRY_DSN is set
    app = FastAPI()
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def init_sentry() -> bool:
    """Initialize Sentry if a DSN is configured. Safe to call once at startup.

    Reads ``SENTRY_DSN`` (required) and ``ENVIRONMENT`` (optional, defaults to
    ``"production"``) from the environment. Returns ``True`` when Sentry was
    initialized, ``False`` when it was a no-op (no DSN configured or the SDK is
    not installed).
    """
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        logger.info("Sentry disabled (SENTRY_DSN unset)")
        return False

    try:
        import sentry_sdk
    except ImportError:
        logger.warning("SENTRY_DSN set but sentry-sdk is not installed; skipping")
        return False

    environment = os.getenv("ENVIRONMENT", "production").strip() or "production"

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        # Errors/crashes only — no performance or profiling traffic.
        traces_sample_rate=0.0,
        # Don't attach the client IP, cookies, or request body.
        send_default_pii=False,
    )
    logger.info("Sentry initialized (environment=%s)", environment)
    return True
