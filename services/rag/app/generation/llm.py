"""LLM providers.

Streaming matters more here than it usually does. A grounded answer needs
retrieval, reranking and a long context before the model writes a word, so the
time to the first token is seconds. Streaming turns that into something a
student watches arrive rather than something they wait out.

Two implementations behind one shape, so the provider can be swapped for a
local model or another host without the chat pipeline noticing:

  GeminiLLMProvider   the real one
  MockLLMProvider     deterministic, no key, no quota -- and it honours the
                      grounding contract, so pipeline tests can assert on
                      refusal behaviour rather than only on plumbing
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from app.core.models import LLMMessage, LLMResponse, LLMUsage
from app.core.registry import llms
from app.embedding.rate_limit import (
    RateLimiter,
    RateLimitError,
    TransientError,
    with_retries,
)
from app.generation.prompts import UNSUPPORTED_TOKEN

logger = logging.getLogger(__name__)

_API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"


def _split_messages(
    messages: Sequence[LLMMessage],
) -> tuple[str, list[dict[str, Any]]]:
    """Separates the system instruction from the turns.

    Gemini takes the system prompt in its own field rather than as a message,
    and folding it into the first user turn measurably weakens instruction
    following -- which here means weakening the grounding rules.
    """
    system_parts: list[str] = []
    contents: list[dict[str, Any]] = []

    for message in messages:
        if message.role == "system":
            system_parts.append(message.content)
            continue
        # Gemini names the assistant role "model".
        role = "model" if message.role == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": message.content}]})

    return "\n\n".join(system_parts), contents


class MockLLMProvider:
    """Deterministic stand-in that respects the grounding contract.

    Returns the refusal token when the prompt carries no sources, and otherwise
    echoes a cited answer. That lets the chat pipeline be tested end to end --
    including the refusal path, which is the behaviour that matters most --
    with no key and no quota.
    """

    model_id = "mock-llm"

    def __init__(self, reply: str | None = None) -> None:
        self.reply = reply
        self.calls: list[list[LLMMessage]] = []

    def _answer(self, messages: Sequence[LLMMessage]) -> str:
        self.calls.append(list(messages))
        if self.reply is not None:
            return self.reply

        prompt = "\n".join(m.content for m in messages)
        if "[S1]" not in prompt:
            return UNSUPPORTED_TOKEN
        return "Based on the material, this is the answer [S1]."

    async def complete(
        self,
        messages: Sequence[LLMMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        text = self._answer(messages)
        return LLMResponse(
            text=text,
            usage=LLMUsage(prompt_tokens=0, completion_tokens=len(text.split())),
            model_id=self.model_id,
            finish_reason="stop",
        )

    async def stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        # Chunked by word, so tests exercise the same reassembly path as the
        # real provider rather than receiving one whole string.
        for word in self._answer(messages).split(" "):
            yield word + " "


class GeminiLLMProvider:
    def __init__(
        self,
        api_key: str,
        *,
        model_id: str = "gemini-3.8-flash",
        max_rpm: int = 15,
        timeout: float = 180.0,
        thinking_budget: int | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("GeminiLLMProvider requires an API key")
        self.model_id = model_id
        self._api_key = api_key
        self._timeout = timeout
        # None leaves the model's own default. Set to 0 for tasks where
        # deliberation buys nothing and only costs latency and quota.
        self._thinking_budget = thinking_budget
        # Not every model accepts thinkingConfig -- the lite models reject it
        # outright with a 400, having no thinking to configure. Rather than
        # hardcode a capability list that will drift as models change, the
        # first rejection disables it for this provider and the request is
        # retried without it.
        self._thinking_unsupported = False
        self._limiter = RateLimiter(max_rpm)

    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self._api_key, "content-type": "application/json"}

    def _payload(
        self,
        messages: Sequence[LLMMessage],
        temperature: float,
        max_tokens: int | None,
        json_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        system, contents = _split_messages(messages)
        config: dict[str, Any] = {"temperature": temperature}
        if max_tokens is not None:
            config["maxOutputTokens"] = max_tokens
        if self._thinking_budget is not None and not self._thinking_unsupported:
            config["thinkingConfig"] = {"thinkingBudget": self._thinking_budget}
        if json_schema is not None:
            config["responseMimeType"] = "application/json"
            config["responseSchema"] = json_schema

        payload: dict[str, Any] = {"contents": contents, "generationConfig": config}
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        return payload

    def _check(self, response: httpx.Response, *, sent_thinking: bool) -> None:
        if response.status_code == 200:
            return
        detail = response.text[:300]
        if response.status_code == 429:
            raise RateLimitError(f"llm rate limited: {detail}")
        if response.status_code >= 500:
            raise TransientError(f"llm upstream {response.status_code}: {detail}")
        if response.status_code == 400 and sent_thinking:
            # Almost certainly the thinkingConfig this model does not accept.
            # Disable it and let the caller retry rather than losing the turn.
            self._thinking_unsupported = True
            logger.info(
                "%s rejected thinkingConfig; disabling it for this provider",
                self.model_id,
            )
            raise TransientError(f"retrying without thinkingConfig: {detail}")
        raise RuntimeError(f"llm request failed ({response.status_code}): {detail}")

    @staticmethod
    def _text_from(payload: dict[str, Any]) -> str:
        candidates = payload.get("candidates") or []
        if not candidates:
            return ""
        parts = candidates[0].get("content", {}).get("parts", [])
        # Thinking parts carry no "text", so this naturally skips them.
        return "".join(p.get("text", "") for p in parts)

    async def complete(
        self,
        messages: Sequence[LLMMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        async def call() -> LLMResponse:
            # Rebuilt each attempt, so a retry after a thinkingConfig rejection
            # sends the corrected payload.
            payload = self._payload(messages, temperature, max_tokens, json_schema)
            sent_thinking = "thinkingConfig" in payload["generationConfig"]

            await self._limiter.acquire()
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                try:
                    response = await client.post(
                        f"{_API_ROOT}/{self.model_id}:generateContent",
                        headers=self._headers(),
                        json=payload,
                    )
                except httpx.HTTPError as exc:
                    raise TransientError(f"llm request errored: {exc}") from exc

            self._check(response, sent_thinking=sent_thinking)
            body = response.json()
            usage = body.get("usageMetadata", {})
            candidates = body.get("candidates") or [{}]

            return LLMResponse(
                text=self._text_from(body).strip(),
                usage=LLMUsage(
                    prompt_tokens=int(usage.get("promptTokenCount") or 0),
                    completion_tokens=int(usage.get("candidatesTokenCount") or 0),
                ),
                model_id=self.model_id,
                finish_reason=candidates[0].get("finishReason"),
            )

        return await with_retries(call, attempts=4, description="llm complete")

    async def stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """Yields text deltas as they arrive.

        Retried only while nothing has been emitted yet. A 429 or a 503 on the
        way in is common on a free tier and costs the student the whole answer;
        once the first token has been sent, a retry would replay text already
        on screen, so a later failure surfaces to the caller instead.
        """
        attempts = 4
        for attempt in range(attempts):
            emitted = False
            try:
                async for delta in self._stream_once(
                    messages, temperature, max_tokens
                ):
                    emitted = True
                    yield delta
                return
            except (RateLimitError, TransientError) as exc:
                if emitted or attempt == attempts - 1:
                    raise
                delay = min(2.0 * (2**attempt), 30.0) * (0.75 + random.random() * 0.5)
                logger.warning(
                    "llm stream failed before first token (attempt %s/%s): %s "
                    "-- retrying in %.1fs",
                    attempt + 1,
                    attempts,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)

    async def _stream_once(
        self,
        messages: Sequence[LLMMessage],
        temperature: float,
        max_tokens: int | None,
    ) -> AsyncIterator[str]:
        payload = self._payload(messages, temperature, max_tokens)
        sent_thinking = "thinkingConfig" in payload["generationConfig"]
        await self._limiter.acquire()

        async with (
            httpx.AsyncClient(timeout=self._timeout) as client,
            client.stream(
                "POST",
                f"{_API_ROOT}/{self.model_id}:streamGenerateContent",
                params={"alt": "sse"},
                headers=self._headers(),
                json=payload,
            ) as response,
        ):
                if response.status_code != 200:
                    await response.aread()
                    self._check(response, sent_thinking=sent_thinking)

                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw or raw == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(raw)
                    except json.JSONDecodeError:
                        logger.debug("skipping unparseable stream line: %s", raw[:120])
                        continue

                    text = self._text_from(chunk)
                    if text:
                        yield text


@llms.register("mock")
def _create_mock(**_: object) -> MockLLMProvider:
    return MockLLMProvider()


@llms.register("gemini")
def _create_gemini(
    api_key: str,
    model_id: str = "gemini-3.8-flash",
    max_rpm: int = 15,
    thinking_budget: int | None = None,
) -> GeminiLLMProvider:
    return GeminiLLMProvider(
        api_key,
        model_id=model_id,
        max_rpm=max_rpm,
        thinking_budget=thinking_budget,
    )
