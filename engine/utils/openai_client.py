"""
ABOUTME: OpenAI-compatible client wrapper (works with OpenAI-format APIs)
ABOUTME: Mirrors GeminiModelWrapper interface for OpenDraft pipeline
"""

import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Optional

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

logger = logging.getLogger(__name__)

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>\s*", re.DOTALL)


def _strip_reasoning(text: str) -> str:
    """Drop inline ``<think>`` blocks that reasoning models (e.g. MiniMax
    M-series) prepend to ``content``. Agents downstream parse the text as
    markdown/JSON, so the reasoning trace would corrupt every parser."""
    if "<think>" not in text:
        return text
    text = _THINK_BLOCK_RE.sub("", text)
    # Unterminated block: the response was cut mid-thought, nothing usable follows.
    idx = text.find("<think>")
    if idx != -1:
        text = text[:idx]
    return text.strip()


@dataclass
class UsageMetadata:
    """Mimics Gemini's usage_metadata structure."""

    prompt_token_count: int
    candidates_token_count: int
    total_token_count: int


@dataclass
class Part:
    """Mimics Gemini's Part structure."""

    text: str
    function_call: Optional[object] = None


@dataclass
class Content:
    """Mimics Gemini's Content structure."""

    parts: list

    @classmethod
    def from_text(cls, text: str) -> "Content":
        return cls(parts=[Part(text=text)])


@dataclass
class Candidate:
    """Mimics Gemini's Candidate structure."""

    content: Content
    finish_reason: str = "stop"


@dataclass
class OpenAIResponse:
    """Mimics Gemini's response structure for compatibility."""

    text: str
    usage_metadata: UsageMetadata
    candidates: list = None

    def __post_init__(self):
        if self.candidates is None:
            self.candidates = [
                Candidate(
                    content=Content.from_text(self.text),
                    finish_reason="stop",
                )
            ]


class OpenAIModelWrapper:
    """
    OpenAI-compatible client wrapper.

    Usage:
        model = OpenAIModelWrapper(
            model_name="gpt-4o-mini",
            api_key="...",
            base_url="https://api.example.com/v1",
        )
        response = model.generate_content("Hello")
        print(response.text)
    """

    def __init__(
        self,
        model_name: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 8192,
    ):
        if OpenAI is None:
            raise ImportError("openai required. Install with: pip install openai>=1.0.0")

        self.model_name = model_name
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.base_url = (
            base_url
            or os.getenv("OPENAI_BASE_URL")
            or os.getenv("OPENAI_API_BASE")
            or os.getenv("OPENAI_API_URL")
        )
        self.temperature = temperature
        self.max_tokens = max_tokens

        if not self.api_key:
            raise ValueError("OPENAI_API_KEY required for OpenAI-compatible models")

        if self.base_url:
            self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        else:
            self.client = OpenAI(api_key=self.api_key)

    def generate_content(
        self,
        prompt: Any,
        generation_config: Any = None,
        safety_settings: Any = None,
    ) -> OpenAIResponse:
        _ = safety_settings
        config = {"temperature": self.temperature, "max_tokens": self.max_tokens}
        response_format = None

        if generation_config:
            if hasattr(generation_config, "temperature"):
                config["temperature"] = generation_config.temperature
            if hasattr(generation_config, "max_output_tokens"):
                config["max_tokens"] = generation_config.max_output_tokens
            if hasattr(generation_config, "response_mime_type"):
                if generation_config.response_mime_type == "application/json":
                    response_format = {"type": "json_object"}

            if isinstance(generation_config, dict):
                if "temperature" in generation_config:
                    config["temperature"] = generation_config["temperature"]
                if "max_output_tokens" in generation_config:
                    config["max_tokens"] = generation_config["max_output_tokens"]
                if "max_tokens" in generation_config:
                    config["max_tokens"] = generation_config["max_tokens"]
                if generation_config.get("response_mime_type") == "application/json":
                    response_format = {"type": "json_object"}

        if isinstance(prompt, str):
            content = prompt
        elif isinstance(prompt, list):
            content = "\n".join(str(p) for p in prompt)
        else:
            content = str(prompt)

        kwargs = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": content}],
            "temperature": config["temperature"],
        }
        if config.get("max_tokens"):
            kwargs["max_tokens"] = config["max_tokens"]
        if response_format is not None:
            kwargs["response_format"] = response_format
        # MiniMax M-series models think by default and inline <think> blocks
        # into content; _strip_reasoning removes them so downstream markdown/
        # JSON parsing stays intact. Thinking is kept ON for writing quality —
        # set OPENAI_DISABLE_THINKING=true to opt out. Give thinking room in
        # the budget so long sections aren't truncated mid-thought.
        if "minimax" in (self.base_url or "").lower():
            if os.getenv("OPENAI_DISABLE_THINKING", "").lower() == "true":
                kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
            else:
                kwargs["max_tokens"] = max(int(kwargs.get("max_tokens", 0) or 0), 32768)

        result = self.client.chat.completions.create(**kwargs)
        choice = result.choices[0]
        text = _strip_reasoning(choice.message.content or "")
        finish_reason = choice.finish_reason or "stop"

        usage = result.usage or {}
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or usage.get("prompt_tokens", 0)
        completion_tokens = getattr(usage, "completion_tokens", 0) or usage.get(
            "completion_tokens", 0
        )
        total_tokens = getattr(usage, "total_tokens", 0) or usage.get("total_tokens", 0)

        usage_metadata = UsageMetadata(
            prompt_token_count=prompt_tokens,
            candidates_token_count=completion_tokens,
            total_token_count=total_tokens,
        )

        return OpenAIResponse(
            text=text,
            usage_metadata=usage_metadata,
            candidates=[
                Candidate(
                    content=Content.from_text(text),
                    finish_reason=finish_reason,
                )
            ],
        )
