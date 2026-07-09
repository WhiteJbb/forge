"""Request Analyzer 단위 테스트 (DESIGN.md §5.3, src/core/analyzer.py)"""

import hashlib
import unittest

from forge_gateway.core.analyzer import RequestAnalyzer


def _user(text):
    return {"role": "user", "content": text}


class TaskHintPriorityTests(unittest.TestCase):
    def setUp(self):
        self.analyzer = RequestAnalyzer()

    def test_task_hint_overrides_keyword_signal(self):
        # 메시지 내용은 명백히 documentation 관련 키워드지만 hint가 최우선
        payload = {"messages": [_user("README 문서 작성해줘 documentation")]}
        result = self.analyzer.analyze(payload, task_hint="refactor")
        self.assertEqual(result.task, "refactor")
        self.assertEqual(result.confidence, 1.0)

    def test_invalid_hint_falls_back_to_keyword_detection(self):
        payload = {"messages": [_user("README 문서 작성해줘")]}
        result = self.analyzer.analyze(payload, task_hint="not-a-real-task")
        self.assertEqual(result.task, "documentation")
        self.assertLess(result.confidence, 1.0)


class RequiredFeaturesTests(unittest.TestCase):
    def setUp(self):
        self.analyzer = RequestAnalyzer()

    def test_tools_field_sets_tools_feature(self):
        payload = {
            "messages": [_user("hi")],
            "tools": [{"type": "function", "function": {"name": "foo"}}],
        }
        result = self.analyzer.analyze(payload)
        self.assertIn("tools", result.required_features)
        self.assertNotIn("parallel_tools", result.required_features)

    def test_parallel_tool_calls_adds_parallel_tools_feature(self):
        payload = {
            "messages": [_user("hi")],
            "tools": [{"type": "function", "function": {"name": "foo"}}],
            "parallel_tool_calls": True,
        }
        result = self.analyzer.analyze(payload)
        self.assertEqual(result.required_features, {"tools", "parallel_tools"})

    def test_json_object_response_format_sets_json_mode(self):
        payload = {
            "messages": [_user("hi")],
            "response_format": {"type": "json_object"},
        }
        result = self.analyzer.analyze(payload)
        self.assertIn("json_mode", result.required_features)

    def test_no_tools_no_features(self):
        payload = {"messages": [_user("hi")]}
        result = self.analyzer.analyze(payload)
        self.assertEqual(result.required_features, set())


class SessionKeyTests(unittest.TestCase):
    def setUp(self):
        self.analyzer = RequestAnalyzer()

    def test_user_field_used_directly(self):
        payload = {"messages": [_user("hi")], "user": "client-session-42"}
        result = self.analyzer.analyze(payload)
        self.assertEqual(result.session_key, "client-session-42")

    def test_missing_user_field_hashes_system_and_first_user(self):
        payload = {
            "messages": [
                {"role": "system", "content": "you are a coding agent"},
                _user("fix this bug please"),
            ]
        }
        result = self.analyzer.analyze(payload)
        expected = hashlib.sha256(
            ("you are a coding agent" + "fix this bug please").encode("utf-8")
        ).hexdigest()[:16]
        self.assertEqual(result.session_key, expected)
        self.assertEqual(len(result.session_key), 16)


class LastMessageWeightingTests(unittest.TestCase):
    """마지막 user 메시지 가중(3배)이 판정을 바꾸는 케이스 (§5.3)"""

    def setUp(self):
        self.analyzer = RequestAnalyzer()

    def test_single_message_documentation_keyword_wins(self):
        payload = {"messages": [_user("문서를 확인해주세요")]}
        result = self.analyzer.analyze(payload)
        self.assertEqual(result.task, "documentation")

    def test_last_message_weight_flips_verdict_to_debug(self):
        # 첫 메시지는 documentation 키워드 하나("문서", weight=1 -> score 0.2)
        # 마지막 메시지는 debug 키워드 하나("버그", weight=3 -> score 0.6)
        # 가중치 없이 키워드 수만 세면 동률이지만, 마지막 메시지 가중 덕분에 debug가 이긴다
        payload = {
            "messages": [
                _user("문서를 확인해주세요"),
                _user("버그"),
            ]
        }
        result = self.analyzer.analyze(payload)
        self.assertEqual(result.task, "debug")


if __name__ == "__main__":
    unittest.main()
