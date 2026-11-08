"""Hosted LLM provider (Groq, Google AI Studio, OpenRouter) with schema enforcement and retry fallback."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from pydantic import ValidationError

from ...config import Settings
from ...schemas import Answer, Passage
from .ollama import FALLBACK_ANSWER, SYSTEM_PROMPT

log = logging.getLogger("rahbar.llm.hosted")


class HostedProvider:
    name = "hosted"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _build_prompt(self, question: str, passages: list[Passage]) -> str:
        evidence_lines = []
        for i, p in enumerate(passages, start=1):
            evidence_lines.append(
                f"[{i}] Source: {p.source} | Section: {p.section} | Page: {p.page or 'N/A'}\n"
                f"<passage_{i}>\n{p.text}\n</passage_{i}>"
            )
        evidence_block = "\n\n".join(evidence_lines)

        return (
            f"Here is the verified context:\n"
            f"<context>\n{evidence_block}\n</context>\n\n"
            f"Student Question:\n<question>\n{question}\n</question>\n\n"
            f"Respond strictly in valid JSON format matching the schema."
        )

    def generate(self, question: str, passages: list[Passage]) -> Answer:
        """Call hosted OpenAI-compatible chat completions endpoint with constrained decoding, retry, and fallback."""
        if not passages:
            return Answer(
                reasoning="No context passages provided",
                answer="The verified corpus contains no relevant information regarding this query.",
                citations=[],
                answered_from_context=False,
                confidence="low",
            )

        prompt = self._build_prompt(question, passages)
        base_url = self.settings.hosted_base_url.rstrip("/") or "https://api.groq.com/openai/v1"
        url = f"{base_url}/chat/completions"

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.settings.llm_api_key}",
        }

        # Request constrained JSON output: response_format
        payload: dict[str, Any] = {
            "model": self.settings.llm_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": self.settings.temperature,
        }

        for attempt in range(1, 3):
            try:
                with httpx.Client(timeout=30.0) as client:
                    res = client.post(url, json=payload, headers=headers)
                    if res.status_code == 200:
                        data = res.json()
                        content = data["choices"][0]["message"]["content"]
                        parsed = json.loads(content)
                        # Validate against Answer schema with Pydantic
                        answer = Answer.model_validate(parsed)
                        return answer
            except (httpx.RequestError, json.JSONDecodeError, ValidationError, KeyError) as exc:
                log.warning(
                    "hosted provider generation failure, retrying",
                    extra={"attempt": attempt, "error": str(exc)},
                )
                if attempt == 1:
                    payload["messages"].append(
                        {"role": "user", "content": "Previous output was invalid JSON. Respond only with JSON conforming to the schema."}
                    )

        log.error("hosted provider retries exhausted, returning safe fallback")
        return FALLBACK_ANSWER
