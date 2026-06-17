"""Tests for GLM Coding Plan provider."""

from unittest.mock import patch

from providers.base import ProviderConfig
from providers.glm_coding import GLM_CODING_DEFAULT_BASE, GlmCodingProvider


def test_init():
    config = ProviderConfig(
        api_key="test_glm_coding_key",
        base_url=GLM_CODING_DEFAULT_BASE,
        rate_limit=10,
        rate_window=60,
        enable_thinking=True,
    )

    with patch("providers.openai_compat.AsyncOpenAI") as mock_openai:
        provider = GlmCodingProvider(config)

    assert provider._api_key == "test_glm_coding_key"
    assert provider._base_url == GLM_CODING_DEFAULT_BASE
    assert provider._provider_name == "GLM_CODING"
    mock_openai.assert_called_once()


def test_default_base_url_constant():
    assert GLM_CODING_DEFAULT_BASE == "https://open.bigmodel.cn/api/coding/paas/v4"
