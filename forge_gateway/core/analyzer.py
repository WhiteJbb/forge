"""Request Analyzer — 힌트 생산자, 결정은 Policy/Scheduler가 한다 (DESIGN.md §5.3)

task 판정은 3계층 신호를 위에서부터 순서대로 적용한다:
  1) task_hint (X-Forge-Task 헤더 / auto:task 별칭 — API 계층이 추출해 전달, confidence=1.0)
  2) 구조적 신호 (diff/patch 블록 존재 → refactor/debug 계열 가중)
  3) 키워드 매칭 (마지막 user 메시지 3배 가중, 시스템 프롬프트는 분석 제외)

src/analyzer.py + src/config.py.TASK_KEYWORDS의 포팅본 (원본은 통합 단계에서 삭제 예정).
"""

import hashlib
import json
from typing import Optional

from .types import AnalysisResult

VALID_TASKS = {"coding", "debug", "refactor", "documentation", "testing"}

# 구조적 신호(diff/patch 블록)이 있을 때 refactor/debug 스코어에 더하는 가중치.
# 키워드 하나의 점수(len(keyword)/10, 대략 0.4~1.4)와 같은 스케일 — 근거 없는 완전 override는 피하고
# 키워드 매칭 몇 개 분량의 신뢰도만 얹는다. (DESIGN.md에 정확한 수치 명시는 없음 — 구현 판단)
STRUCTURAL_SIGNAL_WEIGHT = 1.5

DIFF_MARKERS = ("```diff", "--- a/", "+++ b/", "@@ ")

# src/config.py TASK_KEYWORDS 포팅 (모듈 상수로 복사 — import 아님, 원본 삭제 예정이므로)
TASK_KEYWORDS = {
    "refactor": [
        "리팩토링", "refactor", "개선", "improve", "재구성", "restructure",
        "clean up", "정리", "optimize", "최적화",
    ],
    "debug": [
        "버그", "bug", "에러", "error", "수정", "fix", "debug",
        "디버그", "문제", "issue", "오류", "해결", "resolve",
    ],
    "documentation": [
        "README", "readme", "문서", "documentation", "docs", "작성",
        "write", "설명", "explain", "주석", "comment",
    ],
    "testing": [
        "테스트", "test", "테스트 코드", "test code", "unit test",
        "유닛 테스트", "spec", "스펙",
    ],
    "coding": [
        "구현", "implement", "코드", "code", "개발", "develop",
        "작성", "write", "생성", "generate", "만들", "create",
        "추가", "add", "기능", "feature",
    ],
}

# src/analyzer.py._detect_language 포팅
LANGUAGE_KEYWORDS = {
    "python": ["python", "py", "django", "flask", "fastapi"],
    "javascript": ["javascript", "js", "node", "npm", "react", "vue"],
    "typescript": ["typescript", "ts", "tsx"],
    "java": ["java", "spring", "maven"],
    "go": ["golang", "go "],
    "rust": ["rust", "cargo"],
    "cpp": ["c++", "cpp", "cmake"],
    "csharp": ["c#", "csharp", ".net", "dotnet"],
}


class RequestAnalyzer:
    """OpenAI chat.completions 요청 body를 분석해 AnalysisResult 힌트를 생산한다."""

    def __init__(self):
        self.task_keywords = TASK_KEYWORDS

    def analyze(self, payload: dict, task_hint: Optional[str] = None) -> AnalysisResult:
        messages = payload.get("messages") or []

        best_task, confidence, keywords_matched = self._detect_task(messages)

        # 1순위: task_hint — 유효하면 확정 채택, confidence=1.0
        normalized_hint = task_hint.strip().lower() if task_hint else None
        if normalized_hint in VALID_TASKS:
            task = normalized_hint
            confidence = 1.0
        else:
            task = best_task

        required_features = self._extract_features(payload)
        est_prompt_tokens = self._estimate_prompt_tokens(payload)
        session_key = self._session_key(payload, messages)
        language = self._detect_language(self._all_user_text(messages))

        return AnalysisResult(
            task=task,
            confidence=confidence,
            est_prompt_tokens=est_prompt_tokens,
            required_features=required_features,
            session_key=session_key,
            language=language,
            keywords_matched=keywords_matched,
        )

    # --- task 판정: 구조적 신호 + 키워드 매칭 ---

    def _detect_task(self, messages: list[dict]) -> tuple[str, float, list[str]]:
        user_messages = [m for m in messages if m.get("role") == "user"]
        if not user_messages:
            return "coding", 0.5, []

        # 마지막 user 메시지에 3배 가중치
        weighted_texts: list[tuple[float, str]] = []
        last_idx = len(user_messages) - 1
        for i, msg in enumerate(user_messages):
            text = self._text_of(msg.get("content", ""))
            weight = 3.0 if i == last_idx else 1.0
            weighted_texts.append((weight, text))

        scores: dict[str, float] = {}
        matched: dict[str, list[str]] = {}
        for weight, text in weighted_texts:
            text_lower = text.lower()
            for task_type, keywords in self.task_keywords.items():
                for keyword in keywords:
                    keyword_lower = keyword.lower()
                    if keyword_lower in text_lower:
                        kw_weight = (len(keyword_lower) / 10) * weight
                        scores[task_type] = scores.get(task_type, 0.0) + kw_weight
                        matched.setdefault(task_type, [])
                        if keyword not in matched[task_type]:
                            matched[task_type].append(keyword)

        # 구조적 신호: diff/patch 블록 존재 → refactor/debug 가중
        # 시스템 프롬프트를 제외한 전체 대화(멀티턴 tool 결과 포함)에서 탐지
        non_system_text = " ".join(
            self._text_of(m.get("content", "")) for m in messages if m.get("role") != "system"
        )
        if self._has_diff_signal(non_system_text):
            for task_type in ("refactor", "debug"):
                scores[task_type] = scores.get(task_type, 0.0) + STRUCTURAL_SIGNAL_WEIGHT

        if not scores:
            return "coding", 0.5, []

        best_task = max(scores, key=scores.get)
        total_score = sum(scores.values())
        confidence = scores[best_task] / total_score if total_score > 0 else 0.5

        return best_task, min(confidence, 1.0), matched.get(best_task, [])

    @staticmethod
    def _has_diff_signal(text: str) -> bool:
        return any(marker in text for marker in DIFF_MARKERS)

    # --- required_features (§5.5 하드 필터 입력) ---

    def _extract_features(self, payload: dict) -> set[str]:
        features: set[str] = set()

        if payload.get("tools"):
            features.add("tools")
            if payload.get("parallel_tool_calls") is True:
                features.add("parallel_tools")

        response_format = payload.get("response_format")
        if isinstance(response_format, dict) and response_format.get("type") in (
            "json_object",
            "json_schema",
        ):
            features.add("json_mode")

        if self._has_vision(payload.get("messages") or []):
            features.add("vision")

        if payload.get("stream") is True:
            features.add("streaming")

        return features

    @staticmethod
    def _has_vision(messages: list[dict]) -> bool:
        for msg in messages:
            if msg.get("role") != "user":
                continue
            content = msg.get("content")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        return True
        return False

    # --- est_prompt_tokens ---

    def _estimate_prompt_tokens(self, payload: dict) -> int:
        messages = payload.get("messages") or []
        content_chars = sum(self._content_char_len(m.get("content", "")) for m in messages)

        tools = payload.get("tools")
        tools_json_len = len(json.dumps(tools)) if tools else 0

        return int((content_chars + tools_json_len) / 3.5)

    # --- session_key (§5.5 세션 고정 입력) ---

    def _session_key(self, payload: dict, messages: list[dict]) -> str:
        user_field = payload.get("user")
        if user_field:
            return str(user_field)

        system_text = " ".join(
            self._text_of(m.get("content", "")) for m in messages if m.get("role") == "system"
        )
        first_user_msg = next((m for m in messages if m.get("role") == "user"), None)
        first_user_text = self._text_of(first_user_msg.get("content", "")) if first_user_msg else ""

        if not system_text and not first_user_text:
            return ""

        combined = system_text + first_user_text
        return hashlib.sha256(combined.encode("utf-8")).hexdigest()[:16]

    # --- 콘텐츠 추출 헬퍼 (src/analyzer.py._extract_user_content 포팅) ---

    @staticmethod
    def _text_of(content) -> str:
        """멀티모달 content(list)면 text 파트만 이어붙인다. 문자열이면 그대로."""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    parts.append(part.get("text", ""))
            return " ".join(parts)
        return ""

    @classmethod
    def _content_char_len(cls, content) -> int:
        return len(cls._text_of(content))

    def _all_user_text(self, messages: list[dict]) -> str:
        return " ".join(
            self._text_of(m.get("content", "")) for m in messages if m.get("role") == "user"
        )

    # --- language (src/analyzer.py._detect_language 포팅) ---

    @staticmethod
    def _detect_language(content: str) -> Optional[str]:
        content_lower = content.lower()
        for lang, keywords in LANGUAGE_KEYWORDS.items():
            for kw in keywords:
                if kw in content_lower:
                    return lang
        return None
