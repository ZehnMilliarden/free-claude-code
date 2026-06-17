"""Tests for GLM Open Platform (OpenAI-compatible) provider."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from providers.base import ProviderConfig
from providers.glm import GLM_DEFAULT_BASE, GlmProvider


class MockMessage:
    def __init__(self, role, content):
        self.role = role
        self.content = content


class MockRequest:
    def __init__(self, **kwargs):
        self.model = "glm-5.2"
        self.messages = [MockMessage("user", "Hello")]
        self.max_tokens = 100
        self.temperature = 0.5
        self.top_p = 0.9
        self.system = "System prompt"
        self.stop_sequences = None
        self.tools = []
        self.thinking = MagicMock()
        self.thinking.enabled = True
        for key, value in kwargs.items():
            setattr(self, key, value)


@pytest.fixture
def glm_config():
    return ProviderConfig(
        api_key="test_glm_key",
        base_url=GLM_DEFAULT_BASE,
        rate_limit=10,
        rate_window=60,
        enable_thinking=True,
    )


@pytest.fixture(autouse=True)
def mock_rate_limiter():
    """Mock the global rate limiter to prevent waiting."""

    @asynccontextmanager
    async def _slot():
        yield

    with patch("providers.openai_compat.GlobalRateLimiter") as mock:
        instance = mock.get_scoped_instance.return_value

        async def _passthrough(fn, *args, **kwargs):
            return await fn(*args, **kwargs)

        instance.execute_with_retry = AsyncMock(side_effect=_passthrough)
        instance.concurrency_slot.side_effect = _slot
        yield instance


@pytest.fixture
def glm_provider(glm_config):
    return GlmProvider(glm_config)


def test_init(glm_config):
    """Test provider initialization."""
    with patch("providers.openai_compat.AsyncOpenAI") as mock_openai:
        provider = GlmProvider(glm_config)
        assert provider._api_key == "test_glm_key"
        assert provider._base_url == GLM_DEFAULT_BASE
        mock_openai.assert_called_once()


def test_default_base_url_constant():
    assert GLM_DEFAULT_BASE == "https://open.bigmodel.cn/api/paas/v4"


def test_build_request_body_enables_preserved_thinking(glm_provider):
    req = MockRequest()

    body = glm_provider._build_request_body(req)

    assert body["model"] == "glm-5.2"
    assert body["extra_body"]["thinking"] == {
        "type": "enabled",
        "clear_thinking": False,
    }
    assert body["max_tokens"] == 100


def test_build_request_body_disables_thinking_when_requested():
    provider = GlmProvider(
        ProviderConfig(
            api_key="test_glm_key",
            base_url=GLM_DEFAULT_BASE,
            rate_limit=10,
            rate_window=60,
            enable_thinking=False,
        )
    )

    body = provider._build_request_body(MockRequest())

    assert body["extra_body"]["thinking"] == {"type": "disabled"}


def test_build_request_body_preserves_caller_extra_body(glm_provider):
    req = MockRequest(extra_body={"custom": "value"})

    body = glm_provider._build_request_body(req)

    assert body["extra_body"]["custom"] == "value"
    assert body["extra_body"]["thinking"]["type"] == "enabled"


def test_build_request_body_respects_caller_thinking_override(glm_provider):
    req = MockRequest(extra_body={"thinking": {"type": "disabled"}})

    body = glm_provider._build_request_body(req)

    assert body["extra_body"]["thinking"] == {"type": "disabled"}


@pytest.mark.asyncio
async def test_stream_response_reasoning_content(glm_provider):
    """reasoning_content deltas are emitted as thinking blocks."""
    req = MockRequest()

    mock_chunk = MagicMock()
    mock_chunk.choices = [
        MagicMock(
            delta=MagicMock(
                content=None,
                reasoning_content="Thinking...",
                tool_calls=None,
            ),
            finish_reason="stop",
        )
    ]
    mock_chunk.usage = MagicMock(completion_tokens=2, prompt_tokens=10)

    async def mock_stream():
        yield mock_chunk

    with patch.object(
        glm_provider._client.chat.completions, "create", new_callable=AsyncMock
    ) as mock_create:
        mock_create.return_value = mock_stream()

        events = [event async for event in glm_provider.stream_response(req)]

        assert any(
            '"thinking_delta"' in event and "Thinking..." in event for event in events
        )


@pytest.mark.asyncio
async def test_cleanup(glm_provider):
    glm_provider._client = AsyncMock()

    await glm_provider.cleanup()

    glm_provider._client.close.assert_called_once()
