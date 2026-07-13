# -*- coding: utf-8 -*-

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from agent.llm_gateway import (  # noqa: E402
    LLMGateway,
    ModelGatewayConfig,
    ModelInvocationError,
    ModelUnavailableError,
)


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class _Session:
    def __init__(self, response):
        self.responses = response if isinstance(response, list) else [response]
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[index]


def _config(**overrides):
    values = {
        "provider": "openai",
        "model": "verified-model",
        "base_url": "https://api.example.test/v1",
        "api_style": "responses",
        "api_key": "secret-key",
        "timeout_seconds": 20,
        "max_output_tokens": 1000,
        "max_input_chars": 10000,
        "retry_count": 0,
        "private_context_enabled": False,
        "data_region": "test-region",
    }
    values.update(overrides)
    return ModelGatewayConfig(**values)


class LLMGatewayTests(unittest.TestCase):
    def test_deepseek_environment_uses_dedicated_key_and_official_endpoint(self):
        with patch.dict(os.environ, {
            "LLM_PROVIDER": "deepseek",
            "LLM_MODEL": "deepseek-v4-flash",
            "DEEPSEEK_API_KEY": "deepseek-secret",
            "LLM_API_KEY": "stale-generic-secret",
        }, clear=True):
            config = ModelGatewayConfig.from_env()

        self.assertTrue(config.configured)
        self.assertEqual(config.api_key, "deepseek-secret")
        self.assertEqual(config.base_url, "https://api.deepseek.com")
        self.assertEqual(config.api_style, "chat_completions")
        self.assertEqual(config.thinking_mode, "disabled")

    def test_deepseek_invalid_thinking_mode_blocks_configuration(self):
        with patch.dict(os.environ, {
            "LLM_PROVIDER": "deepseek",
            "LLM_MODEL": "deepseek-v4-flash",
            "DEEPSEEK_API_KEY": "deepseek-secret",
            "LLM_THINKING_MODE": "automatic",
        }, clear=True):
            config = ModelGatewayConfig.from_env()

        self.assertFalse(config.configured)
        self.assertIn("LLM_THINKING_MODE", config.missing_fields())

    def test_responses_request_uses_strict_schema_and_does_not_expose_secret(self):
        response_text = json.dumps({"status": "ok"})
        session = _Session(_Response({
            "id": "resp_test",
            "model": "verified-model-2026",
            "output": [{
                "type": "message",
                "content": [{"type": "output_text", "text": response_text}],
            }],
            "usage": {"input_tokens": 20, "output_tokens": 6, "total_tokens": 26},
        }))
        gateway = LLMGateway(_config(), session=session, sleep=lambda _: None)

        result = gateway.invoke_structured(
            system_prompt="Return JSON.",
            user_payload={"evidence": "ev_1"},
            output_schema={
                "type": "object",
                "properties": {"status": {"type": "string"}},
                "required": ["status"],
                "additionalProperties": False,
            },
            schema_name="test_schema",
        )

        self.assertEqual(result["text"], response_text)
        self.assertEqual(result["model"], "verified-model-2026")
        self.assertEqual(result["usage"]["total_tokens"], 26)
        url, request = session.calls[0]
        self.assertEqual(url, "https://api.example.test/v1/responses")
        self.assertFalse(request["json"]["store"])
        self.assertTrue(request["json"]["text"]["format"]["strict"])
        self.assertEqual(request["headers"]["Authorization"], "Bearer secret-key")
        self.assertNotIn("secret-key", json.dumps(gateway.public_status()))

    def test_unconfigured_gateway_refuses_to_call_provider(self):
        session = _Session(_Response({}))
        gateway = LLMGateway(_config(api_key=""), session=session)

        with self.assertRaises(ModelUnavailableError):
            gateway.invoke_structured(
                system_prompt="Return JSON.",
                user_payload={},
                output_schema={"type": "object"},
                schema_name="test_schema",
            )

        self.assertEqual(session.calls, [])
        self.assertIn("LLM_API_KEY", gateway.public_status()["missing"])

    def test_provider_error_detail_redacts_api_key(self):
        session = _Session(_Response({
            "error": {"message": "API key secret-key is invalid"},
        }, status_code=401))
        gateway = LLMGateway(_config(), session=session, sleep=lambda _: None)

        with self.assertRaises(ModelInvocationError) as captured:
            gateway.invoke_structured(
                system_prompt="Return JSON.",
                user_payload={},
                output_schema={"type": "object"},
                schema_name="test_schema",
            )

        self.assertIn("[REDACTED]", str(captured.exception))
        self.assertNotIn("secret-key", str(captured.exception))

    def test_chat_completion_request_uses_json_object_and_extracts_usage(self):
        response_text = json.dumps({"status": "ok"})
        session = _Session(_Response({
            "id": "chatcmpl_test",
            "model": "compatible-model-2026",
            "choices": [{"message": {"role": "assistant", "content": response_text}}],
            "usage": {"prompt_tokens": 18, "completion_tokens": 7, "total_tokens": 25},
        }))
        gateway = LLMGateway(
            _config(
                provider="dashscope",
                api_style="chat_completions",
                base_url="https://model.example.test/compatible-mode/v1",
            ),
            session=session,
            sleep=lambda _: None,
        )

        result = gateway.invoke_structured(
            system_prompt="Return JSON.",
            user_payload={"evidence": "ev_1"},
            output_schema={"type": "object"},
            schema_name="test_schema",
        )

        self.assertEqual(result["text"], response_text)
        self.assertEqual(result["usage"]["total_tokens"], 25)
        url, request = session.calls[0]
        self.assertEqual(
            url,
            "https://model.example.test/compatible-mode/v1/chat/completions",
        )
        self.assertEqual(request["json"]["response_format"], {"type": "json_object"})
        self.assertEqual(request["json"]["messages"][0]["role"], "system")

    def test_deepseek_request_sets_json_prompt_and_retries_empty_content(self):
        response_text = json.dumps({"status": "ok"})
        session = _Session([
            _Response({
                "id": "chatcmpl_empty",
                "model": "deepseek-v4-flash",
                "choices": [{"message": {"role": "assistant", "content": ""}}],
            }),
            _Response({
                "id": "chatcmpl_valid",
                "model": "deepseek-v4-flash",
                "choices": [{"message": {"role": "assistant", "content": response_text}}],
                "usage": {"prompt_tokens": 30, "completion_tokens": 8, "total_tokens": 38},
            }),
        ])
        gateway = LLMGateway(
            _config(
                provider="deepseek",
                model="deepseek-v4-flash",
                base_url="https://api.deepseek.com",
                api_style="chat_completions",
                retry_count=1,
                thinking_mode="disabled",
            ),
            session=session,
            sleep=lambda _: None,
        )

        result = gateway.invoke_structured(
            system_prompt="Use only supplied evidence.",
            user_payload={"evidence": "ev_1"},
            output_schema={"type": "object"},
            schema_name="test_schema",
        )

        self.assertEqual(result["text"], response_text)
        self.assertEqual(result["usage"]["total_tokens"], 38)
        self.assertEqual(len(session.calls), 2)
        url, request = session.calls[1]
        self.assertEqual(url, "https://api.deepseek.com/chat/completions")
        self.assertEqual(request["json"]["thinking"], {"type": "disabled"})
        self.assertIn("JSON", request["json"]["messages"][0]["content"])
        self.assertNotIn("secret-key", json.dumps(gateway.public_status()))


if __name__ == "__main__":
    unittest.main()
