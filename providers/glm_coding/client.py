"""GLM Coding Plan provider (OpenAI-compatible chat completions)."""

from __future__ import annotations

from typing import Any

from providers.base import ProviderConfig
from providers.defaults import GLM_CODING_DEFAULT_BASE
from providers.glm.request import build_request_body
from providers.openai_compat import OpenAIChatTransport


class GlmCodingProvider(OpenAIChatTransport):
    """GLM Coding Plan API at ``https://open.bigmodel.cn/api/coding/paas/v4``."""

    def __init__(self, config: ProviderConfig):
        super().__init__(
            config,
            provider_name="GLM_CODING",
            base_url=config.base_url or GLM_CODING_DEFAULT_BASE,
            api_key=config.api_key,
        )

    def _build_request_body(
        self, request: Any, thinking_enabled: bool | None = None
    ) -> dict:
        return build_request_body(
            request,
            thinking_enabled=self._is_thinking_enabled(request, thinking_enabled),
        )
