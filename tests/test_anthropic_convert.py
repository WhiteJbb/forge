"""Anthropic <-> OpenAI 변환 모듈 단위 테스트 (src/api/anthropic_convert.py).

DESIGN.md §5.8 Anthropic Messages API 변환 규약 검증. unittest 사용.
"""

import json
import unittest

from src.api.anthropic_convert import (
    OpenAIToAnthropicStream,
    request_to_openai,
    response_to_anthropic,
)


# ---------------------------------------------------------------------------
# 요청 변환: Anthropic -> OpenAI
# ---------------------------------------------------------------------------

class RequestBasicTests(unittest.TestCase):
    def test_text_roundtrip_and_required_fields(self):
        body = {
            "model": "claude-3-5-sonnet",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": "안녕"}],
        }
        out = request_to_openai(body)
        self.assertEqual(out["model"], "claude-3-5-sonnet")
        self.assertEqual(out["max_tokens"], 1024)
        self.assertEqual(out["messages"], [{"role": "user", "content": "안녕"}])

    def test_single_text_block_reduces_to_string(self):
        body = {
            "model": "m", "max_tokens": 8,
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        }
        out = request_to_openai(body)
        self.assertEqual(out["messages"][0], {"role": "user", "content": "hi"})

    def test_system_string_prepended(self):
        body = {
            "model": "m", "max_tokens": 8,
            "system": "너는 도우미다",
            "messages": [{"role": "user", "content": "q"}],
        }
        out = request_to_openai(body)
        self.assertEqual(out["messages"][0], {"role": "system", "content": "너는 도우미다"})
        self.assertEqual(out["messages"][1]["role"], "user")

    def test_system_block_array_concatenated(self):
        body = {
            "model": "m", "max_tokens": 8,
            "system": [
                {"type": "text", "text": "A"},
                {"type": "text", "text": "B"},
            ],
            "messages": [{"role": "user", "content": "q"}],
        }
        out = request_to_openai(body)
        self.assertEqual(out["messages"][0], {"role": "system", "content": "AB"})

    def test_sampling_and_metadata_fields(self):
        body = {
            "model": "m", "max_tokens": 8,
            "messages": [{"role": "user", "content": "q"}],
            "stop_sequences": ["END"],
            "temperature": 0.5,
            "top_p": 0.9,
            "stream": True,
            "metadata": {"user_id": "u-123"},
        }
        out = request_to_openai(body)
        self.assertEqual(out["stop"], ["END"])
        self.assertEqual(out["temperature"], 0.5)
        self.assertEqual(out["top_p"], 0.9)
        self.assertTrue(out["stream"])
        self.assertEqual(out["user"], "u-123")


class RequestImageTests(unittest.TestCase):
    def test_image_block_to_data_uri(self):
        body = {
            "model": "m", "max_tokens": 8,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "이 그림은?"},
                {"type": "image", "source": {
                    "type": "base64", "media_type": "image/png", "data": "AAAA"}},
            ]}],
        }
        out = request_to_openai(body)
        parts = out["messages"][0]["content"]
        self.assertIsInstance(parts, list)
        self.assertEqual(parts[0], {"type": "text", "text": "이 그림은?"})
        self.assertEqual(parts[1], {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,AAAA"},
        })


class RequestToolTests(unittest.TestCase):
    def test_assistant_tool_use_to_tool_calls(self):
        body = {
            "model": "m", "max_tokens": 8,
            "messages": [{"role": "assistant", "content": [
                {"type": "text", "text": "확인해볼게"},
                {"type": "tool_use", "id": "tu_1", "name": "get_weather",
                 "input": {"city": "서울"}},
            ]}],
        }
        out = request_to_openai(body)
        msg = out["messages"][0]
        self.assertEqual(msg["role"], "assistant")
        self.assertEqual(msg["content"], "확인해볼게")
        self.assertEqual(len(msg["tool_calls"]), 1)
        tc = msg["tool_calls"][0]
        self.assertEqual(tc["id"], "tu_1")
        self.assertEqual(tc["type"], "function")
        self.assertEqual(tc["function"]["name"], "get_weather")
        self.assertEqual(json.loads(tc["function"]["arguments"]), {"city": "서울"})

    def test_assistant_tool_use_only_has_null_content(self):
        body = {
            "model": "m", "max_tokens": 8,
            "messages": [{"role": "assistant", "content": [
                {"type": "tool_use", "id": "tu_1", "name": "f", "input": {}},
            ]}],
        }
        out = request_to_openai(body)
        msg = out["messages"][0]
        self.assertIsNone(msg["content"])
        self.assertEqual(len(msg["tool_calls"]), 1)

    def test_user_tool_result_becomes_tool_message(self):
        body = {
            "model": "m", "max_tokens": 8,
            "messages": [{"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu_1", "content": "맑음"},
            ]}],
        }
        out = request_to_openai(body)
        self.assertEqual(out["messages"], [
            {"role": "tool", "tool_call_id": "tu_1", "content": "맑음"},
        ])

    def test_tool_result_block_array_content_joined(self):
        body = {
            "model": "m", "max_tokens": 8,
            "messages": [{"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu_1", "content": [
                    {"type": "text", "text": "부분1 "},
                    {"type": "text", "text": "부분2"},
                ]},
            ]}],
        }
        out = request_to_openai(body)
        self.assertEqual(out["messages"][0]["content"], "부분1 부분2")

    def test_multiple_tool_results_plus_text(self):
        body = {
            "model": "m", "max_tokens": 8,
            "messages": [{"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu_1", "content": "r1"},
                {"type": "tool_result", "tool_use_id": "tu_2", "content": "r2"},
                {"type": "text", "text": "이어서 진행"},
            ]}],
        }
        out = request_to_openai(body)
        self.assertEqual(len(out["messages"]), 3)
        self.assertEqual(out["messages"][0],
                         {"role": "tool", "tool_call_id": "tu_1", "content": "r1"})
        self.assertEqual(out["messages"][1],
                         {"role": "tool", "tool_call_id": "tu_2", "content": "r2"})
        self.assertEqual(out["messages"][2],
                         {"role": "user", "content": "이어서 진행"})

    def test_tools_and_tool_choice_conversions(self):
        body = {
            "model": "m", "max_tokens": 8,
            "messages": [{"role": "user", "content": "q"}],
            "tools": [{
                "name": "search", "description": "웹 검색",
                "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
            }],
            "tool_choice": {"type": "tool", "name": "search"},
        }
        out = request_to_openai(body)
        self.assertEqual(out["tools"][0], {
            "type": "function",
            "function": {
                "name": "search",
                "description": "웹 검색",
                "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
            },
        })
        self.assertEqual(out["tool_choice"],
                         {"type": "function", "function": {"name": "search"}})

    def test_tool_choice_auto_and_any(self):
        base = {"model": "m", "max_tokens": 8,
                "messages": [{"role": "user", "content": "q"}]}
        self.assertEqual(
            request_to_openai({**base, "tool_choice": {"type": "auto"}})["tool_choice"],
            "auto")
        self.assertEqual(
            request_to_openai({**base, "tool_choice": {"type": "any"}})["tool_choice"],
            "required")


# ---------------------------------------------------------------------------
# 응답 변환: OpenAI -> Anthropic
# ---------------------------------------------------------------------------

class ResponseTests(unittest.TestCase):
    def test_text_response(self):
        resp = {
            "id": "chatcmpl-1",
            "choices": [{"message": {"role": "assistant", "content": "결과입니다"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        out = response_to_anthropic(resp, "claude-x")
        self.assertEqual(out["id"], "chatcmpl-1")
        self.assertEqual(out["type"], "message")
        self.assertEqual(out["role"], "assistant")
        self.assertEqual(out["model"], "claude-x")
        self.assertEqual(out["content"], [{"type": "text", "text": "결과입니다"}])
        self.assertEqual(out["stop_reason"], "end_turn")
        self.assertIsNone(out["stop_sequence"])
        self.assertEqual(out["usage"], {"input_tokens": 10, "output_tokens": 5})

    def test_tool_call_response(self):
        resp = {
            "choices": [{"message": {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "call_1", "type": "function", "function": {
                    "name": "get_weather", "arguments": '{"city": "서울"}'}}],
            }, "finish_reason": "tool_calls"}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3},
        }
        out = response_to_anthropic(resp, "m")
        self.assertEqual(out["content"], [{
            "type": "tool_use", "id": "call_1", "name": "get_weather",
            "input": {"city": "서울"},
        }])
        self.assertEqual(out["stop_reason"], "tool_use")

    def test_tool_call_bad_arguments_preserved_as_raw(self):
        resp = {
            "choices": [{"message": {
                "role": "assistant", "content": None,
                "tool_calls": [{"id": "c", "type": "function", "function": {
                    "name": "f", "arguments": "{broken"}}],
            }, "finish_reason": "tool_calls"}],
        }
        out = response_to_anthropic(resp, "m")
        self.assertEqual(out["content"][0]["input"], {"_raw": "{broken"})

    def test_finish_reason_mapping_and_default_usage(self):
        for fr, expected in [("length", "max_tokens"), ("content_filter", "end_turn"),
                             ("stop", "end_turn"), ("tool_calls", "tool_use")]:
            resp = {"choices": [{"message": {"content": "x"}, "finish_reason": fr}]}
            out = response_to_anthropic(resp, "m")
            self.assertEqual(out["stop_reason"], expected)
            # usage 없으면 0
            self.assertEqual(out["usage"], {"input_tokens": 0, "output_tokens": 0})

    def test_generated_id_when_missing(self):
        resp = {"choices": [{"message": {"content": "x"}, "finish_reason": "stop"}]}
        out = response_to_anthropic(resp, "m")
        self.assertTrue(out["id"].startswith("msg_"))


# ---------------------------------------------------------------------------
# 스트리밍 변환: OpenAI 청크 -> Anthropic SSE 이벤트
# ---------------------------------------------------------------------------

def _names(events):
    return [name for name, _ in events]


class StreamTextTests(unittest.TestCase):
    def test_text_only_stream_sequence(self):
        st = OpenAIToAnthropicStream("claude-x")
        ev = []
        ev += st.feed({"id": "c1", "choices": [{"delta": {"role": "assistant", "content": ""}}]})
        ev += st.feed({"choices": [{"delta": {"content": "안녕"}}]})
        ev += st.feed({"choices": [{"delta": {"content": " 세계"}}]})
        ev += st.feed({"choices": [{"delta": {}, "finish_reason": "stop"}]})
        ev += st.finish()

        self.assertEqual(_names(ev), [
            "message_start",
            "content_block_start",
            "content_block_delta",
            "content_block_delta",
            "content_block_stop",
            "message_delta",
            "message_stop",
        ])
        # message_start 세부
        start = ev[0][1]
        self.assertEqual(start["type"], "message_start")
        self.assertEqual(start["message"]["model"], "claude-x")
        self.assertEqual(start["message"]["id"], "c1")
        # 텍스트 블록 index 0
        self.assertEqual(ev[1][1]["index"], 0)
        self.assertEqual(ev[1][1]["content_block"]["type"], "text")
        self.assertEqual(ev[2][1]["delta"], {"type": "text_delta", "text": "안녕"})
        # 종결
        self.assertEqual(ev[-2][1]["delta"]["stop_reason"], "end_turn")

    def test_usage_chunk_reflected_in_message_delta(self):
        st = OpenAIToAnthropicStream("m")
        st.feed({"id": "c1", "choices": [{"delta": {"content": "hi"}}]})
        st.feed({"choices": [{"delta": {}, "finish_reason": "stop"}]})
        # usage 전용 청크 (choices 빈 배열) — finish() 전에 도착
        usage_events = st.feed({"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 4}})
        self.assertEqual(usage_events, [])  # 이벤트 없음, 상태만 갱신
        fin = st.finish()
        delta = [d for n, d in fin if n == "message_delta"][0]
        self.assertEqual(delta["usage"], {"output_tokens": 4})

    def test_input_tokens_in_message_start_when_present(self):
        st = OpenAIToAnthropicStream("m")
        ev = st.feed({"id": "c1", "usage": {"prompt_tokens": 20},
                      "choices": [{"delta": {"content": "hi"}}]})
        start = ev[0][1]
        self.assertEqual(start["message"]["usage"]["input_tokens"], 20)


class StreamToolTests(unittest.TestCase):
    def test_text_then_tool_call_stream(self):
        st = OpenAIToAnthropicStream("m")
        ev = []
        ev += st.feed({"id": "c1", "choices": [{"delta": {"content": "생각중"}}]})
        # tool call 시작 (id/name은 첫 조각에만)
        ev += st.feed({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_1", "type": "function",
             "function": {"name": "get_weather", "arguments": ""}}]}}]})
        # arguments 조각들
        ev += st.feed({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": '{"city":'}}]}}]})
        ev += st.feed({"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": '"서울"}'}}]}}]})
        ev += st.feed({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]})
        ev += st.finish()

        self.assertEqual(_names(ev), [
            "message_start",
            "content_block_start",   # text block 0
            "content_block_delta",   # "생각중"
            "content_block_stop",    # close text before tool block
            "content_block_start",   # tool_use block 1
            "content_block_delta",   # partial_json '{"city":'
            "content_block_delta",   # partial_json '"서울"}'
            "content_block_stop",    # close tool block at finish
            "message_delta",
            "message_stop",
        ])
        # 텍스트 블록 index 0, tool 블록 index 1
        text_start = ev[1][1]
        tool_start = ev[4][1]
        self.assertEqual(text_start["index"], 0)
        self.assertEqual(tool_start["index"], 1)
        self.assertEqual(tool_start["content_block"], {
            "type": "tool_use", "id": "call_1", "name": "get_weather", "input": {}})
        # input_json_delta
        self.assertEqual(ev[5][1]["delta"],
                         {"type": "input_json_delta", "partial_json": '{"city":'})
        self.assertEqual(ev[5][1]["index"], 1)
        self.assertEqual(ev[-2][1]["delta"]["stop_reason"], "tool_use")

    def test_two_tool_calls_get_separate_blocks(self):
        st = OpenAIToAnthropicStream("m")
        ev = []
        ev += st.feed({"id": "c1", "choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "c0", "function": {"name": "a", "arguments": "{}"}}]}}]})
        ev += st.feed({"choices": [{"delta": {"tool_calls": [
            {"index": 1, "id": "c1", "function": {"name": "b", "arguments": "{}"}}]}}]})
        ev += st.feed({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]})
        ev += st.finish()

        starts = [d for n, d in ev if n == "content_block_start"]
        self.assertEqual([s["index"] for s in starts], [0, 1])
        self.assertEqual(starts[0]["content_block"]["id"], "c0")
        self.assertEqual(starts[1]["content_block"]["id"], "c1")
        # 두 블록 모두 닫혀야 함
        stops = [d for n, d in ev if n == "content_block_stop"]
        self.assertEqual([s["index"] for s in stops], [0, 1])

    def test_finish_without_finish_reason_chunk_still_closes(self):
        # finish_reason 청크가 오지 않고 스트림이 끊긴 경우에도 종결 보장
        st = OpenAIToAnthropicStream("m")
        st.feed({"id": "c1", "choices": [{"delta": {"content": "hi"}}]})
        fin = st.finish()
        self.assertEqual(_names(fin),
                         ["content_block_stop", "message_delta", "message_stop"])
        # stop_reason 미도착 -> None
        self.assertIsNone(fin[-2][1]["delta"]["stop_reason"])


if __name__ == "__main__":
    unittest.main()
