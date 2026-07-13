# -*- coding: utf-8 -*-
"""Provider-neutral LLM gateway for auditable Agent synthesis.

The gateway deliberately exposes no tools to the model. Domain tools run first,
their normalized evidence is then supplied to the model for structured
synthesis. Provider secrets and raw prompts are never returned to callers.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import requests


class ModelUnavailableError(RuntimeError):
    """The approved model provider is not configured for this deployment."""


class ModelInvocationError(RuntimeError):
    """The configured model provider failed or returned an unusable envelope."""


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _bounded_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _provider_key(provider: str) -> str:
    explicit = os.getenv("LLM_API_KEY", "").strip()
    if explicit:
        return explicit
    if provider == "openai":
        return os.getenv("OPENAI_API_KEY", "").strip()
    if provider == "dashscope":
        return os.getenv("DASHSCOPE_API_KEY", "").strip()
    return ""


def _default_base_url(provider: str) -> str:
    if provider == "openai":
        return "https://api.openai.com/v1"
    if provider == "dashscope":
        return "https://dashscope.aliyuncs.com/compatible-mode/v1"
    return ""


@dataclass(frozen=True)
class ModelGatewayConfig:
    provider: str
    model: str
    base_url: str
    api_style: str
    api_key: str
    timeout_seconds: int
    max_output_tokens: int
    max_input_chars: int
    retry_count: int
    private_context_enabled: bool
    data_region: str

    @classmethod
    def from_env(cls) -> "ModelGatewayConfig":
        provider = os.getenv("LLM_PROVIDER", "").strip().lower()
        api_style = os.getenv("LLM_API_STYLE", "").strip().lower()
        if not api_style:
            api_style = "responses" if provider == "openai" else "chat_completions"
        return cls(
            provider=provider,
            model=os.getenv("LLM_MODEL", "").strip(),
            base_url=(os.getenv("LLM_BASE_URL", "").strip() or _default_base_url(provider)).rstrip("/"),
            api_style=api_style,
            api_key=_provider_key(provider),
            timeout_seconds=_bounded_int("LLM_TIMEOUT_SECONDS", 75, 10, 180),
            max_output_tokens=_bounded_int("LLM_MAX_OUTPUT_TOKENS", 2600, 600, 8000),
            max_input_chars=_bounded_int("LLM_MAX_INPUT_CHARS", 60000, 10000, 160000),
            retry_count=_bounded_int("LLM_RETRY_COUNT", 2, 0, 3),
            private_context_enabled=_env_bool("LLM_PRIVATE_CONTEXT_ENABLED", False),
            data_region=os.getenv("LLM_DATA_REGION", "unspecified").strip() or "unspecified",
        )

    def missing_fields(self) -> list[str]:
        missing = []
        if self.provider not in {"openai", "dashscope", "openai_compatible"}:
            missing.append("LLM_PROVIDER")
        if not self.model:
            missing.append("LLM_MODEL")
        if not self.base_url:
            missing.append("LLM_BASE_URL")
        if not self.api_key:
            missing.append("LLM_API_KEY")
        if self.api_style not in {"responses", "chat_completions"}:
            missing.append("LLM_API_STYLE")
        return missing

    @property
    def configured(self) -> bool:
        return not self.missing_fields()


class LLMGateway:
    """Calls one explicitly configured and approved model provider."""

    def __init__(
        self,
        config: ModelGatewayConfig | None = None,
        *,
        session: requests.Session | None = None,
        sleep=time.sleep,
    ) -> None:
        self.config = config or ModelGatewayConfig.from_env()
        self.session = session or requests.Session()
        self._sleep = sleep

    def public_status(self) -> dict[str, Any]:
        parsed = urlparse(self.config.base_url) if self.config.base_url else None
        endpoint_host = parsed.hostname if parsed else None
        missing = self.config.missing_fields()
        return {
            "configured": not missing,
            "provider": self.config.provider or None,
            "model": self.config.model or None,
            "api_style": self.config.api_style or None,
            "endpoint_host": endpoint_host,
            "data_region": self.config.data_region,
            "private_context_enabled": self.config.private_context_enabled,
            "strict_schema_requested": self.config.api_style == "responses",
            "missing": missing,
            "reason": (
                None
                if not missing
                else "大模型网关尚未配置完整；系统不会用模板文本冒充模型研判。"
            ),
        }

    def _endpoint(self) -> str:
        if not self.config.configured:
            raise ModelUnavailableError(
                "大模型网关未配置完整:" + ",".join(self.config.missing_fields())
            )
        parsed = urlparse(self.config.base_url)
        local_hosts = {"127.0.0.1", "localhost", "::1"}
        if parsed.scheme != "https" and not (
            parsed.hostname in local_hosts or _env_bool("LLM_ALLOW_INSECURE_HTTP", False)
        ):
            raise ModelUnavailableError("大模型网关必须使用 HTTPS，除非明确配置本机模型端点")
        suffix = "/responses" if self.config.api_style == "responses" else "/chat/completions"
        return self.config.base_url + suffix

    @staticmethod
    def _responses_text(payload: dict[str, Any]) -> tuple[str, str | None]:
        texts: list[str] = []
        refusal = None
        for item in payload.get("output") or []:
            if item.get("type") != "message":
                continue
            for content in item.get("content") or []:
                if content.get("type") == "output_text" and content.get("text"):
                    texts.append(str(content["text"]))
                if content.get("type") == "refusal" and content.get("refusal"):
                    refusal = str(content["refusal"])
        return "".join(texts).strip(), refusal

    @staticmethod
    def _chat_text(payload: dict[str, Any]) -> tuple[str, str | None]:
        choices = payload.get("choices") or []
        if not choices:
            return "", None
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(
                str(item.get("text") or "") for item in content if isinstance(item, dict)
            )
        return str(content or "").strip(), str(message.get("refusal") or "") or None

    def invoke_structured(
        self,
        *,
        system_prompt: str,
        user_payload: dict[str, Any],
        output_schema: dict[str, Any],
        schema_name: str,
    ) -> dict[str, Any]:
        endpoint = self._endpoint()
        user_json = _canonical(user_payload)
        if len(user_json) > self.config.max_input_chars:
            raise ModelInvocationError(
                f"模型上下文超过部署上限:{len(user_json)}>{self.config.max_input_chars}"
            )
        input_hash = _sha256(system_prompt + "\n" + user_json)
        if self.config.api_style == "responses":
            body = {
                "model": self.config.model,
                "instructions": system_prompt,
                "input": [{
                    "role": "user",
                    "content": [{"type": "input_text", "text": user_json}],
                }],
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": schema_name,
                        "schema": output_schema,
                        "strict": True,
                    }
                },
                "max_output_tokens": self.config.max_output_tokens,
                "store": False,
            }
        else:
            body = {
                "model": self.config.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_json},
                ],
                "response_format": {"type": "json_object"},
                "max_tokens": self.config.max_output_tokens,
                "stream": False,
            }

        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        started = time.monotonic()
        response = None
        last_error: Exception | None = None
        for attempt in range(self.config.retry_count + 1):
            try:
                response = self.session.post(
                    endpoint,
                    headers=headers,
                    json=body,
                    timeout=self.config.timeout_seconds,
                )
                if response.status_code < 400:
                    break
                retryable = response.status_code == 429 or response.status_code >= 500
                if not retryable or attempt >= self.config.retry_count:
                    detail = ""
                    try:
                        envelope = response.json()
                        detail = str((envelope.get("error") or {}).get("message") or "")
                    except Exception:
                        detail = ""
                    raise ModelInvocationError(
                        f"模型提供者返回 HTTP {response.status_code}"
                        + (f":{detail[:180]}" if detail else "")
                    )
            except ModelInvocationError:
                raise
            except (requests.Timeout, requests.ConnectionError) as error:
                last_error = error
                if attempt >= self.config.retry_count:
                    raise ModelInvocationError(
                        f"模型提供者连接失败:{type(error).__name__}"
                    ) from error
            if attempt < self.config.retry_count:
                self._sleep(min(4.0, 0.6 * (2 ** attempt) + random.random() * 0.2))

        if response is None:
            raise ModelInvocationError("模型提供者没有返回响应") from last_error
        try:
            envelope = response.json()
        except ValueError as error:
            raise ModelInvocationError("模型提供者返回的不是 JSON 响应") from error

        if self.config.api_style == "responses":
            text, refusal = self._responses_text(envelope)
        else:
            text, refusal = self._chat_text(envelope)
        if refusal:
            raise ModelInvocationError(f"模型拒绝本次结构化研判:{refusal[:180]}")
        if not text:
            raise ModelInvocationError("模型提供者没有返回结构化文本")

        usage = envelope.get("usage") or {}
        input_tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
        output_tokens = usage.get("output_tokens", usage.get("completion_tokens"))
        total_tokens = usage.get("total_tokens")
        return {
            "provider": self.config.provider,
            "model": str(envelope.get("model") or self.config.model),
            "api_style": self.config.api_style,
            "response_id": str(envelope.get("id") or "") or None,
            "input_sha256": input_hash,
            "output_sha256": _sha256(text),
            "latency_ms": int((time.monotonic() - started) * 1000),
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
            },
            "text": text,
        }
