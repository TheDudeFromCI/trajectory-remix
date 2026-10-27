"""Prefer provider counters; estimate accumulated stream text with an offline tokenizer."""

import re
import time

from pydantic_ai.usage import RequestUsage


class RequestStats:
    def __init__(self, model, context_size=0, tokenizer=None):
        self.started = time.monotonic()
        self.first = None
        self.model = model
        self.context_size = context_size
        self.usage = None
        self.previous = None
        self.current = None
        self.prompt = ""
        self.output = ""
        self.reasoning = ""
        self.previous_source = None
        self.inline_reasoning = ""
        self.tokenizer = tokenizer

    def count(self, text):
        if self.tokenizer is not None:
            return len(self.tokenizer.encode(text, add_special_tokens=False).ids)
        return len(re.findall(r"\w+|[^\w\s]", text))

    def estimate(self, prompt, output, reasoning=""):
        self.prompt, self.output, self.reasoning = prompt, output, reasoning
        self.inline_reasoning = "".join(
            re.findall(r"<think>(.*?)(?:</think>|$)", output, flags=re.DOTALL)
        )

    def first_token(self):
        if self.first is None:
            self.first = time.monotonic()

    def sample(self, usage=None, *, final=False, baseline=None):
        now = time.monotonic()
        if usage is not None:
            self.usage = usage
        result = {
            "model": self.model,
            "generation_seconds": round(now - self.started, 3),
        }
        if self.first is not None:
            result["time_to_first_token_seconds"] = round(self.first - self.started, 3)
        u = self.usage
        estimated = bool(self.prompt) and (
            u is None or not u.input_tokens or not u.output_tokens
        )
        if estimated:
            u = RequestUsage(
                input_tokens=(
                    u.input_tokens if u and u.input_tokens else self.count(self.prompt)
                ),
                output_tokens=(
                    u.output_tokens
                    if u and u.output_tokens
                    else self.count(self.output + self.reasoning)
                ),
                cache_read_tokens=u.cache_read_tokens if u else 0,
                details=dict(u.details) if u else {},
            )
            if (
                self.reasoning or self.inline_reasoning
            ) and "reasoning_tokens" not in u.details:
                u.details["reasoning_tokens"] = self.count(
                    self.reasoning + self.inline_reasoning
                )
        result["token_source"] = "estimated" if estimated else "provider"
        if result["token_source"] != self.previous_source:
            self.previous, self.current = None, None
        self.previous_source = result["token_source"]
        if u is not None and (u.input_tokens or u.output_tokens):
            result.update(
                prompt_tokens=u.input_tokens,
                completion_tokens=u.output_tokens,
                total_tokens=u.input_tokens + u.output_tokens,
                cache_read_tokens=u.cache_read_tokens,
            )
            if "reasoning_tokens" in u.details:
                result["reasoning_tokens"] = u.details["reasoning_tokens"]
            # Only a provider that reports counters during streaming permits a live rate.
            if not final and u.output_tokens:
                if self.previous and u.output_tokens != self.previous[1]:
                    self.current = (u.output_tokens - self.previous[1]) / max(
                        now - self.previous[0], 0.001
                    )
                self.previous = (now, u.output_tokens)
            if self.current is not None and not final:
                result["current_tokens_per_second"] = round(self.current, 2)
            if final and self.first is not None and u.output_tokens:
                result["average_tokens_per_second"] = round(
                    u.output_tokens / max(now - self.first, 0.001), 2
                )
            if self.context_size:
                result.update(
                    context_size=self.context_size,
                    context_used_tokens=u.input_tokens + u.output_tokens,
                    first_prompt_tokens=baseline
                    if baseline is not None
                    else u.input_tokens,
                )
        return result
