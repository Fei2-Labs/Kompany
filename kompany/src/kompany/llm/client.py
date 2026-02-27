"""Anthropic client wrapper with structured output and cost tracking."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Type, TypeVar

import anthropic
from pydantic import BaseModel

from kompany.llm.cost_tracker import CostTracker
from kompany.llm.models import estimate_cost

T = TypeVar("T", bound=BaseModel)


@dataclass
class LLMResponse:
    text: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    model: str
    parsed: Any = None


class LLMClient:
    """Wrapper around Anthropic API with cost tracking."""

    def __init__(self, api_key: str, cost_tracker: CostTracker):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.cost_tracker = cost_tracker

    def call(
        self,
        model: str,
        system: str,
        prompt: str,
        agent_name: str = "unknown",
        directive_id: str | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Make a freeform LLM call."""
        response = self.client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text
        inp = response.usage.input_tokens
        out = response.usage.output_tokens
        cost = self.cost_tracker.record(
            model=model,
            input_tokens=inp,
            output_tokens=out,
            description=f"{agent_name}: {prompt[:60]}",
            directive_id=directive_id,
        )
        return LLMResponse(
            text=text,
            input_tokens=inp,
            output_tokens=out,
            cost_usd=cost,
            model=model,
        )

    def call_structured(
        self,
        model: str,
        system: str,
        prompt: str,
        output_schema: Type[T],
        agent_name: str = "unknown",
        directive_id: str | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Make an LLM call expecting JSON conforming to a Pydantic schema."""
        schema_json = json.dumps(output_schema.model_json_schema(), indent=2)
        full_prompt = (
            f"{prompt}\n\n"
            f"Respond with ONLY valid JSON matching this schema:\n{schema_json}"
        )
        resp = self.call(
            model=model,
            system=system,
            prompt=full_prompt,
            agent_name=agent_name,
            directive_id=directive_id,
            max_tokens=max_tokens,
        )
        # Parse the JSON from the response text
        text = resp.text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        parsed = output_schema.model_validate_json(text)
        resp.parsed = parsed
        return resp
