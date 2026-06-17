"""Request builder for GLM OpenAI-compatible chat completions."""

from __future__ import annotations

from typing import Any

from loguru import logger

from core.anthropic import ReasoningReplayMode, build_base_request_body
from core.anthropic.conversion import OpenAIConversionError
from providers.exceptions import InvalidRequestError


def _glm_thinking_options(*, thinking_enabled: bool) -> dict[str, Any]:
    if not thinking_enabled:
        return {"type": "disabled"}
    return {"type": "enabled", "clear_thinking": False}


def build_request_body(request_data: Any, *, thinking_enabled: bool) -> dict:
    """Build OpenAI-format request body from an Anthropic request for GLM."""
    logger.debug(
        "GLM_REQUEST: conversion start model={} msgs={}",
        getattr(request_data, "model", "?"),
        len(getattr(request_data, "messages", [])),
    )
    try:
        body = build_base_request_body(
            request_data,
            reasoning_replay=ReasoningReplayMode.REASONING_CONTENT
            if thinking_enabled
            else ReasoningReplayMode.DISABLED,
        )
    except OpenAIConversionError as exc:
        raise InvalidRequestError(str(exc)) from exc

    extra_body = dict(getattr(request_data, "extra_body", None) or {})
    extra_body.setdefault(
        "thinking", _glm_thinking_options(thinking_enabled=thinking_enabled)
    )
    body["extra_body"] = extra_body

    logger.debug(
        "GLM_REQUEST: conversion done model={} msgs={} tools={}",
        body.get("model"),
        len(body.get("messages", [])),
        len(body.get("tools", [])),
    )
    return body
