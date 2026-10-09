import json
import os
import unittest
import urllib.request
from unittest.mock import patch

from pipeline.llm import (WorkersAIError, WorkersAILLM, extract_response_text,
                          build_user_prompt, _NoRedirects, MAX_RESPONSE_BYTES)
from .support import ACCOUNT, TOKEN, SUMMARY, pack


class LLMTests(unittest.TestCase):
    def client(self, callback):
        with patch.dict(os.environ, {"CF_ACCOUNT_ID": ACCOUNT, "CF_API_TOKEN": TOKEN}):
            return WorkersAILLM(_http=callback)

    def test_exact_smoke_configuration_and_no_credentials_in_messages(self):
        requests = []
        client = self.client(lambda req: requests.append(req) or json.dumps({"success": True, "result": {"choices": [{"message": {"content": SUMMARY}}]}}).encode())
        self.assertEqual(client.summarize(vars(pack())), SUMMARY)
        payload = json.loads(requests[0].data)
        self.assertEqual(payload["max_completion_tokens"], 512)
        self.assertEqual(payload["chat_template_kwargs"], {"enable_thinking": False})
        self.assertNotIn("max_tokens", payload)
        self.assertNotIn("tools", payload)
        self.assertNotIn(TOKEN, requests[0].data.decode())
        self.assertNotIn(ACCOUNT, requests[0].data.decode())
        self.assertEqual([m["role"] for m in payload["messages"]], ["system", "user"])
        self.assertTrue(requests[0].full_url.endswith("@cf/zai-org/glm-4.7-flash"))

    def test_secret_in_exception_is_not_exposed(self):
        def fail(req):
            raise RuntimeError(TOKEN + " private article")
        with self.assertRaises(WorkersAIError) as cm:
            self.client(fail).summarize(vars(pack()))
        self.assertEqual(str(cm.exception), "request_failed")
        self.assertTrue(cm.exception.__suppress_context__)

    def test_api_errors_are_not_exposed(self):
        client = self.client(lambda req: json.dumps({"success": False, "errors": [{"message": TOKEN}]}).encode())
        with self.assertRaisesRegex(WorkersAIError, "^api_error$"):
            client.summarize(vars(pack()))

    def test_arbitrary_active_token_echo_is_rejected(self):
        client = self.client(lambda req: json.dumps({"result": {"response": TOKEN}}).encode())
        with self.assertRaisesRegex(WorkersAIError, "^unsafe_response$"):
            client.summarize(vars(pack()))

    def test_active_account_id_echo_is_rejected(self):
        client = self.client(lambda req: json.dumps({"result": {"response": ACCOUNT}}).encode())
        with self.assertRaisesRegex(WorkersAIError, "^unsafe_response$"):
            client.summarize(vars(pack()))

    def test_source_token_is_rejected_before_http(self):
        requests = []
        fields = vars(pack()).copy()
        fields["key_points"] = [TOKEN]
        with self.assertRaisesRegex(WorkersAIError, "^unsafe_fact_pack$"):
            self.client(lambda req: requests.append(req)).summarize(fields)
        self.assertEqual(requests, [])

    def test_missing_credentials_stop_before_network(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(WorkersAIError, "^credentials_missing$"):
                WorkersAILLM()

    def test_unknown_model_denied(self):
        with patch.dict(os.environ, {"CF_ACCOUNT_ID": ACCOUNT, "CF_API_TOKEN": TOKEN}):
            with self.assertRaisesRegex(WorkersAIError, "^model_not_allowed$"):
                WorkersAILLM(model="@cf/unapproved/model")

    def test_account_path_injection_denied(self):
        with patch.dict(os.environ, {"CF_ACCOUNT_ID": "../another-account", "CF_API_TOKEN": TOKEN}):
            with self.assertRaisesRegex(WorkersAIError, "^invalid_account_id$"):
                WorkersAILLM()

    def test_authorization_header_injection_denied(self):
        with patch.dict(os.environ, {"CF_ACCOUNT_ID": ACCOUNT, "CF_API_TOKEN": "secret\r\nHost: evil"}):
            with self.assertRaisesRegex(WorkersAIError, "^invalid_credentials$"):
                WorkersAILLM()

    def test_worker_redirect_never_forwards_authorization(self):
        req = urllib.request.Request("https://api.cloudflare.com/client/v4/", headers={"Authorization": "Bearer " + TOKEN})
        with self.assertRaisesRegex(WorkersAIError, "^redirect_rejected$"):
            _NoRedirects().redirect_request(req, None, 302, "", {}, "https://evil.invalid/")

    def test_prompt_contains_only_selected_data_and_fixed_boundary(self):
        prompt = build_user_prompt(vars(pack()))
        payload = json.loads(prompt.split("\n", 1)[1])
        self.assertEqual(set(payload), {"title", "key_points"})
        self.assertTrue(prompt.startswith("UNTRUSTED_SOURCE_DATA_JSON\n"))
        self.assertNotIn("article_text", payload)


GOOD_RESPONSES = {
    "standard": {"success": True, "result": {"response": " answer "}},
    "nested_choices": {"success": True, "result": {"choices": [{"message": {"content": " answer "}}]}},
    "top_choices": {"choices": [{"message": {"content": "answer"}}]},
    "empty_standard_nested_choices": {"result": {"response": " ", "choices": [{"message": {"content": "answer"}}]}},
    "empty_result_top_choices": {"result": {}, "choices": [{"message": {"content": "answer"}}]},
    "no_text": {},
    "reasoning_only": {"result": {"choices": [{"message": {"reasoning_content": "hidden", "content": None}}]}},
}
BAD_RESPONSES = {
    "list_root": [], "null_root": None,
    "api_failure": {"success": False, "result": {"response": "answer"}},
    "invalid_success_type": {"success": "false"},
    "top_error": {"error": {"message": TOKEN}, "choices": [{"message": {"content": "answer"}}]},
    "error_list": {"success": True, "errors": [TOKEN]},
    "scalar_result": {"result": 42},
    "integer_response": {"result": {"response": 42}},
    "list_response": {"result": {"response": []}},
    "empty_choices": {"choices": []},
    "non_list_choices": {"choices": {}},
    "scalar_choice": {"choices": [42]},
    "null_message": {"choices": [{"message": None}]},
    "missing_message": {"choices": [{}]},
    "integer_content": {"choices": [{"message": {"content": 42}}]},
    "array_content": {"choices": [{"message": {"content": ["answer"]}}]},
    "truncated": {"choices": [{"message": {"content": "answer"}, "finish_reason": "length"}]},
    "tool_call": {"choices": [{"message": {"content": "answer", "tool_calls": [{}]}}]},
    "content_filtered": {"choices": [{"message": {"content": "answer"}, "finish_reason": "content_filter"}]},
}


def response_test(value, good):
    def test(self):
        if good:
            self.assertEqual(extract_response_text(value), "" if value in ({}, GOOD_RESPONSES["reasoning_only"]) else "answer")
        else:
            with self.assertRaises(WorkersAIError):
                extract_response_text(value)
    return test


for label, value in GOOD_RESPONSES.items():
    setattr(LLMTests, "test_response_" + label, response_test(value, True))
for label, value in BAD_RESPONSES.items():
    setattr(LLMTests, "test_response_" + label, response_test(value, False))


def failed_http_test(value, expected):
    def test(self):
        with self.assertRaisesRegex(WorkersAIError, "^" + expected + "$"):
            self.client(lambda req: value).summarize(vars(pack()))
    return test


for label, raw, expected in [
    ("invalid_json", b"not json", "request_failed"),
    ("invalid_utf8", b"\xff", "request_failed"),
    ("oversized", b"x" * (MAX_RESPONSE_BYTES + 1), "invalid_response_size"),
    ("not_bytes", "text", "invalid_response_size"),
    ("empty", b'{"result":{"response":" "}}', "empty_response"),
]:
    setattr(LLMTests, "test_http_" + label, failed_http_test(raw, expected))
