"""OpenAI-compatible HTTP client for the local Ubuntu Inference Snap.

Uses stdlib `urllib.request` to avoid pulling in `httpx` / `aiohttp` for
v1. The actual POST runs in a worker thread via `asyncio.to_thread` so
the rest of the orchestrator stays async.

Reads the snapshot, deterministic hypotheses, and optional symptom; sends
them as a single chat-completion request; parses the JSON the model
returned and maps it onto `LLMExplanation`. Every failure mode —
connection refused, timeout, non-200, malformed JSON, missing required
fields — is funnelled into `LLMUnavailable` so callers can degrade.

**Safety:** the LLM is given READ-ONLY input. No tool-use schema is
attached. The model can only produce text; ubuntu-doctor renders any
suggested commands for the user to copy-paste. It never executes them.
"""

from __future__ import annotations

import asyncio
import json
import re
import urllib.error
import urllib.request
from typing import Any, Callable

from ubuntu_doctor.llm.prompts import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_user_prompt,
)
from ubuntu_doctor.llm.types import (
    LLMExplanation,
    LLMUnavailable,
    RankedHypothesis,
)
from ubuntu_doctor.snapshot import Hypothesis, Snapshot

DEFAULT_BASE_URL = "http://localhost:8336/v1"
DEFAULT_MODEL = "gemma:e4b"
DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_MAX_TOKENS = 2048
DEFAULT_TEMPERATURE = 0.2

PostFn = Callable[[str, dict[str, Any], float], dict[str, Any]]


def _default_post(url: str, body: dict[str, Any], timeout: float) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise LLMUnavailable(f"LLM endpoint unreachable: {exc}") from exc
    except TimeoutError as exc:
        raise LLMUnavailable(f"LLM request timed out after {timeout}s") from exc
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise LLMUnavailable(
            f"LLM endpoint returned non-JSON envelope: {payload[:200]!r}"
        ) from exc


def _extract_json_object(content: str) -> dict[str, Any]:
    """Parse a JSON object from raw model output.

    Models sometimes wrap their JSON in ```json fences or in leading
    prose despite the system prompt forbidding it. Pull out the outermost
    `{...}` block as a fallback. Anything we can't parse is an error.
    """
    stripped = content.strip()
    fence = re.match(r"```(?:json)?\s*(.*?)\s*```", stripped, re.DOTALL)
    if fence:
        stripped = fence.group(1).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise LLMUnavailable(
            f"LLM did not return a JSON object: {content[:200]!r}"
        )
    try:
        return json.loads(stripped[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMUnavailable(
            f"LLM output looked like JSON but did not parse: {exc}"
        ) from exc


def _build_explanation(
    parsed: dict[str, Any],
    *,
    valid_hypothesis_ids: set[str],
    model: str,
    raw: str,
) -> LLMExplanation:
    if not isinstance(parsed, dict):
        raise LLMUnavailable(
            f"LLM JSON was not an object: {type(parsed).__name__}"
        )
    summary = str(parsed.get("summary", "")).strip()
    what_missing = str(parsed.get("what_i_did_not_check", "")).strip()
    raw_ranked = parsed.get("ranked_hypotheses", [])
    if not isinstance(raw_ranked, list):
        raise LLMUnavailable("ranked_hypotheses was not a list")

    ranked: list[RankedHypothesis] = []
    for item in raw_ranked:
        if not isinstance(item, dict):
            continue
        hid = str(item.get("hypothesis_id", "")).strip()
        if not hid or hid not in valid_hypothesis_ids:
            # The model hallucinated an id, or omitted it. Drop the row
            # rather than confuse the user with a phantom hypothesis.
            continue
        try:
            confidence = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        commands = tuple(str(c) for c in item.get("commands", []) if c)
        risks = tuple(str(r) for r in item.get("risks", []) if r)
        ranked.append(
            RankedHypothesis(
                hypothesis_id=hid,
                title=str(item.get("title", "")).strip() or hid,
                why=str(item.get("why", "")).strip(),
                confidence=max(0.0, min(1.0, confidence)),
                commands=commands,
                risks=risks,
            )
        )

    return LLMExplanation(
        summary=summary,
        ranked_hypotheses=tuple(ranked),
        what_i_did_not_check=what_missing,
        model=model,
        prompt_version=PROMPT_VERSION,
        raw_response=raw,
    )


class LLMClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        temperature: float = DEFAULT_TEMPERATURE,
        post_fn: PostFn | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._post = post_fn or _default_post

    def _completions_url(self) -> str:
        return f"{self.base_url}/chat/completions"

    def _request_body(self, user_prompt: str) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
        }

    async def explain(
        self,
        snapshot: Snapshot,
        hypotheses: list[Hypothesis],
        symptom: str | None = None,
    ) -> LLMExplanation:
        user_prompt = build_user_prompt(snapshot, hypotheses, symptom)
        body = self._request_body(user_prompt)
        envelope = await asyncio.to_thread(
            self._post, self._completions_url(), body, self.timeout
        )
        try:
            content = envelope["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMUnavailable(
                f"LLM response missing choices[0].message.content: {exc}"
            ) from exc
        if not isinstance(content, str):
            raise LLMUnavailable(
                f"LLM message.content was {type(content).__name__}, not str"
            )
        parsed = _extract_json_object(content)
        valid_ids = {h.id for h in hypotheses}
        return _build_explanation(
            parsed,
            valid_hypothesis_ids=valid_ids,
            model=self.model,
            raw=content,
        )
