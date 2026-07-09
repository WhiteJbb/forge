"""시작 배너 회귀 테스트 — 유니코드 배너가 인코딩 실패 시 ASCII로 폴백하는지 (isatty=False 시나리오)"""

import io
import unittest

from forge_gateway.server import _BANNER_ART_ASCII, _BANNER_ART_UNICODE, _print_art


class _EncodeLimitedWriter(io.TextIOBase):
    """cp949 같은 레거시 코드페이지를 흉내낸다 — ASCII 밖 문자는 encode 실패."""

    def __init__(self):
        self.chunks: list[str] = []

    def write(self, s: str) -> int:
        s.encode("ascii")  # cp949도 박스문자를 못 담지만, 재현 목적은 "非ASCII 실패"로 충분
        self.chunks.append(s)
        return len(s)


class PrintArtFallbackTests(unittest.TestCase):
    def test_uses_unicode_banner_when_stdout_supports_it(self):
        buf = io.StringIO()
        import sys
        original = sys.stdout
        sys.stdout = buf
        try:
            _print_art()
        finally:
            sys.stdout = original
        self.assertIn(_BANNER_ART_UNICODE.strip(), buf.getvalue())

    def test_falls_back_to_ascii_when_encoding_fails(self):
        writer = _EncodeLimitedWriter()
        import sys
        original = sys.stdout
        sys.stdout = writer
        try:
            _print_art()
        finally:
            sys.stdout = original
        output = "".join(writer.chunks)
        self.assertIn(_BANNER_ART_ASCII.strip(), output)
        self.assertNotIn("█", output)  # 박스문자(█)가 새지 않았는지
