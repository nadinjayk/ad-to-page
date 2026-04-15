from __future__ import annotations

import logging
import random
import time
from typing import Callable, TypeVar

import anthropic
from anthropic import Anthropic

from backend.config import (
    ANTHROPIC_INITIAL_RETRY_DELAY_SECONDS,
    ANTHROPIC_MAX_RETRIES,
    ANTHROPIC_MAX_RETRY_DELAY_SECONDS,
    ANTHROPIC_REQUEST_TIMEOUT_SECONDS,
    get_anthropic_api_key,
)


logger = logging.getLogger(__name__)
T = TypeVar("T")


def create_anthropic_client() -> Anthropic:
    return Anthropic(
        api_key=get_anthropic_api_key(),
        timeout=ANTHROPIC_REQUEST_TIMEOUT_SECONDS,
    )


def _is_retryable_error(exc: BaseException) -> bool:
    if isinstance(
        exc,
        (
            anthropic.APIConnectionError,
            anthropic.APITimeoutError,
            anthropic.RateLimitError,
            anthropic.InternalServerError,
        ),
    ):
        return True

    if isinstance(exc, anthropic.APIStatusError):
        status_code = getattr(exc, "status_code", None)
        return bool(status_code in {408, 409, 425, 429, 500, 502, 503, 504, 529})

    return False


def _format_error_message(exc: BaseException) -> str:
    message = str(exc).strip()
    if message:
        return message
    return exc.__class__.__name__


def call_anthropic_with_retries(
    *,
    operation_name: str,
    request_callable: Callable[[Anthropic], T],
) -> T:
    last_error: BaseException | None = None

    for attempt in range(ANTHROPIC_MAX_RETRIES + 1):
        client = create_anthropic_client()
        try:
            return request_callable(client)
        except Exception as exc:
            last_error = exc
            if not _is_retryable_error(exc) or attempt >= ANTHROPIC_MAX_RETRIES:
                break

            base_delay = min(
                ANTHROPIC_MAX_RETRY_DELAY_SECONDS,
                ANTHROPIC_INITIAL_RETRY_DELAY_SECONDS * (2**attempt),
            )
            delay_seconds = max(0.5, base_delay * (0.85 + random.random() * 0.3))
            logger.warning(
                "Retrying Anthropic %s after transient failure on attempt %s/%s: %s",
                operation_name,
                attempt + 1,
                ANTHROPIC_MAX_RETRIES + 1,
                _format_error_message(exc),
            )
            time.sleep(delay_seconds)

    if last_error is None:
        raise RuntimeError(f"Anthropic {operation_name} failed before sending a request.")

    if _is_retryable_error(last_error):
        raise RuntimeError(
            f"Claude was temporarily unavailable during {operation_name}. "
            f"Retried {ANTHROPIC_MAX_RETRIES + 1} times but it still failed. "
            f"Last error: {_format_error_message(last_error)}"
        ) from last_error

    raise last_error
