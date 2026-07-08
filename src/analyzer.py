"""
Request Analyzer - Analyzes incoming requests to determine task type
"""

import re
from typing import Optional
from .config import TASK_KEYWORDS


class RequestAnalyzer:
    """Analyzes requests to determine the task type"""

    def __init__(self):
        self.task_keywords = TASK_KEYWORDS

    def analyze(self, messages: list[dict]) -> dict:
        """
        Analyze messages to determine task type and context.

        Args:
            messages: List of message dicts (OpenAI format)

        Returns:
            Dict with task type and analysis metadata
        """
        # Combine all user messages for analysis
        user_content = self._extract_user_content(messages)

        if not user_content:
            return {
                "task": "coding",
                "confidence": 0.5,
                "keywords_matched": [],
            }

        # Detect task type
        task_type, confidence, matched_keywords = self._detect_task(user_content)

        # Extract context hints
        context_hints = self._extract_context_hints(user_content)

        return {
            "task": task_type,
            "confidence": confidence,
            "keywords_matched": matched_keywords,
            "context_hints": context_hints,
            "content_length": len(user_content),
        }

    def _extract_user_content(self, messages: list[dict]) -> str:
        """Extract and combine user message content"""
        contents = []
        for msg in messages:
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, list):
                    # Handle multimodal content
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            contents.append(part.get("text", ""))
                elif isinstance(content, str):
                    contents.append(content)
        return " ".join(contents)

    def _detect_task(self, content: str) -> tuple[str, float, list[str]]:
        """Detect task type from content"""
        content_lower = content.lower()
        scores = {}
        matched = {}

        for task_type, keywords in self.task_keywords.items():
            score = 0
            matched_keywords = []
            for keyword in keywords:
                keyword_lower = keyword.lower()
                if keyword_lower in content_lower:
                    # Weight longer keywords higher
                    weight = len(keyword_lower) / 10
                    score += weight
                    matched_keywords.append(keyword)

            if score > 0:
                scores[task_type] = score
                matched[task_type] = matched_keywords

        if not scores:
            return "coding", 0.5, []

        # Get the task with highest score
        best_task = max(scores, key=scores.get)
        total_score = sum(scores.values())
        confidence = scores[best_task] / total_score if total_score > 0 else 0.5

        return best_task, min(confidence, 1.0), matched.get(best_task, [])

    def _extract_context_hints(self, content: str) -> dict:
        """Extract context hints from content"""
        hints = {
            "has_code_block": "```" in content,
            "has_file_path": bool(re.search(r'[/\\]\w+\.\w+', content)),
            "mentions_language": self._detect_language(content),
            "request_length": len(content),
        }
        return hints

    def _detect_language(self, content: str) -> Optional[str]:
        """Detect programming language mentioned in content"""
        languages = {
            "python": ["python", "py", "django", "flask", "fastapi"],
            "javascript": ["javascript", "js", "node", "npm", "react", "vue"],
            "typescript": ["typescript", "ts", "tsx"],
            "java": ["java", "spring", "maven"],
            "go": ["golang", "go "],
            "rust": ["rust", "cargo"],
            "cpp": ["c++", "cpp", "cmake"],
            "csharp": ["c#", "csharp", ".net", "dotnet"],
        }

        content_lower = content.lower()
        for lang, keywords in languages.items():
            for kw in keywords:
                if kw in content_lower:
                    return lang

        return None