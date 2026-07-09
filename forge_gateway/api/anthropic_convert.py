"""Anthropic Messages API <-> 내부 표준(OpenAI chat.completions) 순수 변환 모듈.

Claude Code는 Anthropic Messages 포맷(`/v1/messages`)으로 통신하지만, Forge의
내부 표준은 OpenAI chat.completions 포맷이다 (DESIGN.md §5.8). 이 모듈은 입구/출구
양 끝단의 변환만 책임진다 — 엔드포인트 배선/파이프라인 연결은 여기서 다루지 않는다.

- request_to_openai:  Anthropic 요청  -> OpenAI 요청
- response_to_anthropic: OpenAI 응답 -> Anthropic 응답
- OpenAIToAnthropicStream: OpenAI 스트리밍 청크 -> Anthropic SSE 이벤트 시퀀스

표준 라이브러리만 사용, Python 3.10 호환.
"""

import json
import uuid
from typing import Any, Optional

# OpenAI finish_reason -> Anthropic stop_reason 매핑 (요청/응답/스트림 공용)
_STOP_REASON_MAP = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "content_filter": "end_turn",
}


def _new_message_id() -> str:
    return "msg_" + uuid.uuid4().hex


def _map_stop_reason(finish_reason: Optional[str]) -> Optional[str]:
    """finish_reason -> stop_reason. None이면 None(아직 미종결)."""
    if finish_reason is None:
        return None
    return _STOP_REASON_MAP.get(finish_reason, "end_turn")


# ---------------------------------------------------------------------------
# 요청 변환: Anthropic -> OpenAI
# ---------------------------------------------------------------------------

def _join_text_blocks(blocks: list) -> str:
    """content 블록 배열에서 text 블록들의 text를 이어붙인다 (system/tool_result 용)."""
    parts = []
    for b in blocks:
        if isinstance(b, dict) and b.get("type") == "text":
            parts.append(b.get("text", ""))
    return "".join(parts)


def _system_to_message(system: Any) -> Optional[dict]:
    """system(문자열 또는 블록 배열) -> OpenAI system 메시지. 없으면 None."""
    if system is None:
        return None
    if isinstance(system, str):
        content = system
    elif isinstance(system, list):
        content = _join_text_blocks(system)
    else:
        return None
    return {"role": "system", "content": content}


def _image_block_to_part(block: dict) -> dict:
    """Anthropic image 블록 -> OpenAI image_url 파트 (base64 data URI)."""
    source = block.get("source") or {}
    media_type = source.get("media_type", "")
    data = source.get("data", "")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:{media_type};base64,{data}"},
    }


def _convert_plain_blocks(blocks: list) -> Any:
    """text/image 블록 배열을 OpenAI content로 변환.

    단일 text 블록만이면 문자열로 축약, 그 외에는 파트 배열.
    (tool_use/tool_result는 여기서 처리하지 않는다 — 호출측에서 분리.)
    """
    parts: list = []
    for b in blocks:
        if not isinstance(b, dict):
            continue
        t = b.get("type")
        if t == "text":
            parts.append({"type": "text", "text": b.get("text", "")})
        elif t == "image":
            parts.append(_image_block_to_part(b))
    if len(parts) == 1 and parts[0].get("type") == "text":
        return parts[0]["text"]
    return parts


def _tool_result_content(content: Any) -> Any:
    """tool_result의 content -> OpenAI tool 메시지 content.

    문자열이면 그대로, 블록 배열이면 text 블록을 이어붙인다.
    """
    if isinstance(content, list):
        return _join_text_blocks(content)
    return content if content is not None else ""


def _convert_user_message(content: Any) -> list[dict]:
    """user 메시지 -> OpenAI 메시지 목록.

    tool_result 블록은 각각 별도 {"role":"tool"} 메시지로, 그 외 text/image
    블록이 남으면 tool 메시지들 뒤에 하나의 user 메시지로 붙인다.
    """
    if isinstance(content, str):
        return [{"role": "user", "content": content}]

    if not isinstance(content, list):
        return [{"role": "user", "content": content}]

    tool_messages: list[dict] = []
    remaining: list[dict] = []
    for b in content:
        if isinstance(b, dict) and b.get("type") == "tool_result":
            tool_messages.append({
                "role": "tool",
                "tool_call_id": b.get("tool_use_id"),
                "content": _tool_result_content(b.get("content")),
            })
        else:
            remaining.append(b)

    messages = list(tool_messages)
    if remaining:
        messages.append({"role": "user", "content": _convert_plain_blocks(remaining)})
    elif not tool_messages:
        # 블록이 하나도 없는 빈 user 메시지 — 빈 문자열로 방어
        messages.append({"role": "user", "content": ""})
    return messages


def _convert_assistant_message(content: Any) -> dict:
    """assistant 메시지 -> OpenAI assistant 메시지.

    text 블록 -> content, tool_use 블록 -> tool_calls 배열.
    """
    if isinstance(content, str):
        return {"role": "assistant", "content": content}

    if not isinstance(content, list):
        return {"role": "assistant", "content": content}

    text_parts: list[str] = []
    tool_calls: list[dict] = []
    for b in content:
        if not isinstance(b, dict):
            continue
        t = b.get("type")
        if t == "text":
            text_parts.append(b.get("text", ""))
        elif t == "tool_use":
            tool_calls.append({
                "id": b.get("id"),
                "type": "function",
                "function": {
                    "name": b.get("name"),
                    "arguments": json.dumps(b.get("input", {}), ensure_ascii=False),
                },
            })

    msg: dict = {"role": "assistant"}
    if text_parts:
        # assistant content는 OpenAI에서 스칼라 문자열 — 여러 text 블록은 이어붙임
        msg["content"] = "".join(text_parts)
    else:
        # text 없이 tool_use만이면 content는 null (OpenAI 허용)
        msg["content"] = None
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def _convert_tools(tools: list) -> list[dict]:
    out = []
    for t in tools:
        if not isinstance(t, dict):
            continue
        fn: dict = {
            "name": t.get("name"),
            "parameters": t.get("input_schema", {}),
        }
        if t.get("description") is not None:
            fn["description"] = t.get("description")
        out.append({"type": "function", "function": fn})
    return out


def _convert_tool_choice(tc: Any) -> Any:
    """Anthropic tool_choice -> OpenAI tool_choice."""
    if not isinstance(tc, dict):
        return tc
    t = tc.get("type")
    if t == "auto":
        return "auto"
    if t == "any":
        return "required"
    if t == "tool":
        return {"type": "function", "function": {"name": tc.get("name")}}
    if t == "none":
        return "none"
    return tc


def request_to_openai(body: dict) -> dict:
    """Anthropic Messages 요청 -> OpenAI chat.completions 요청."""
    out: dict = {}

    # 필수 필드 유지
    if "model" in body:
        out["model"] = body["model"]
    if "max_tokens" in body:
        out["max_tokens"] = body["max_tokens"]

    # 메시지 조립: system(맨 앞) -> 각 메시지 변환
    messages: list[dict] = []
    sys_msg = _system_to_message(body.get("system"))
    if sys_msg is not None:
        messages.append(sys_msg)

    for m in body.get("messages", []):
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content")
        if role == "user":
            messages.extend(_convert_user_message(content))
        elif role == "assistant":
            messages.append(_convert_assistant_message(content))
        else:
            # 알 수 없는 role은 그대로 통과 (방어)
            messages.append({"role": role, "content": content})
    out["messages"] = messages

    # tools / tool_choice
    if isinstance(body.get("tools"), list):
        out["tools"] = _convert_tools(body["tools"])
    if "tool_choice" in body:
        out["tool_choice"] = _convert_tool_choice(body["tool_choice"])

    # 샘플링/스트리밍 파라미터
    if "stop_sequences" in body:
        out["stop"] = body["stop_sequences"]
    for key in ("temperature", "top_p", "stream"):
        if key in body:
            out[key] = body[key]

    # metadata.user_id -> user
    metadata = body.get("metadata")
    if isinstance(metadata, dict) and metadata.get("user_id") is not None:
        out["user"] = metadata["user_id"]

    return out


# ---------------------------------------------------------------------------
# 응답 변환: OpenAI -> Anthropic
# ---------------------------------------------------------------------------

def _parse_tool_arguments(arguments: Any) -> dict:
    """tool_call arguments(JSON 문자열) -> dict. 파싱 실패 시 원문을 _raw로 보존."""
    if isinstance(arguments, dict):
        return arguments
    if not isinstance(arguments, str):
        return {}
    try:
        parsed = json.loads(arguments)
    except (ValueError, TypeError):
        return {"_raw": arguments}
    # 최상위가 dict가 아니면(예: 리스트/스칼라) Anthropic input 규약상 dict로 감싼다
    if not isinstance(parsed, dict):
        return {"_raw": arguments}
    return parsed


def _message_content_to_blocks(message: dict) -> list[dict]:
    """OpenAI message.content + tool_calls -> Anthropic content 블록 목록."""
    blocks: list[dict] = []

    content = message.get("content")
    if isinstance(content, str):
        if content:
            blocks.append({"type": "text", "text": content})
    elif isinstance(content, list):
        # OpenAI content 파트 배열(드묾) — text 파트만 추출
        text = "".join(
            p.get("text", "") for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        )
        if text:
            blocks.append({"type": "text", "text": text})

    for tc in message.get("tool_calls") or []:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        blocks.append({
            "type": "tool_use",
            "id": tc.get("id"),
            "name": fn.get("name"),
            "input": _parse_tool_arguments(fn.get("arguments")),
        })

    return blocks


def response_to_anthropic(resp: dict, request_model: str) -> dict:
    """OpenAI chat.completions 응답 -> Anthropic Messages 응답."""
    choices = resp.get("choices") or []
    choice = choices[0] if choices else {}
    message = choice.get("message") or {}

    content_blocks = _message_content_to_blocks(message)
    stop_reason = _map_stop_reason(choice.get("finish_reason"))

    usage = resp.get("usage") or {}
    input_tokens = usage.get("prompt_tokens", 0) or 0
    output_tokens = usage.get("completion_tokens", 0) or 0

    return {
        "id": resp.get("id") or _new_message_id(),
        "type": "message",
        "role": "assistant",
        "model": request_model,
        "content": content_blocks,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


# ---------------------------------------------------------------------------
# 스트리밍 변환: OpenAI 청크 -> Anthropic SSE 이벤트 상태 기계
# ---------------------------------------------------------------------------

class OpenAIToAnthropicStream:
    """OpenAI 스트리밍 청크를 Anthropic SSE 이벤트 시퀀스로 변환하는 상태 기계.

    이벤트는 (event_name, data) 튜플로 반환한다. data는 실제 Anthropic SSE와
    동일하게 event_name과 짝을 이루는 "type" 필드를 포함한다 (배선 계층이
    `event: <name>\\ndata: <json>` 로 직렬화하기 쉽도록).

    사용법:
        st = OpenAIToAnthropicStream(request_model)
        for chunk in openai_chunks:
            for name, data in st.feed(chunk):
                emit(name, data)
        for name, data in st.finish():
            emit(name, data)

    설계 결정: message_delta / message_stop 은 feed()가 아니라 finish()에서
    방출한다. OpenAI는 finish_reason 청크 '뒤'에 usage 전용 청크를 보내므로,
    종결 이벤트를 finish()로 미뤄야 output_tokens 를 정확히 반영할 수 있다.
    finish_reason 도착 시에는 열린 블록만 content_block_stop 으로 닫는다.
    이렇게 해도 전체 이벤트 '순서'(...stop -> message_delta -> message_stop)는
    보존된다.
    """

    def __init__(self, request_model: str):
        self.request_model = request_model
        self._started = False          # message_start 방출 여부
        self._message_id: Optional[str] = None
        self._next_index = 0           # 다음 content block index (0부터 순차 증가)
        self._cur_index: Optional[int] = None   # 현재 열린 블록 index
        self._cur_type: Optional[str] = None     # "text" | "tool_use"
        self._tool_blocks: dict[int, int] = {}   # OpenAI tool index -> block index
        self._input_tokens = 0
        self._output_tokens = 0
        self._stop_reason: Optional[str] = None
        self._closed = False           # message_stop 방출 여부

    # --- 내부 헬퍼 ---

    def _open_block(self, block_type: str, content_block: dict) -> tuple[str, dict]:
        idx = self._next_index
        self._next_index += 1
        self._cur_index = idx
        self._cur_type = block_type
        return ("content_block_start", {
            "type": "content_block_start",
            "index": idx,
            "content_block": content_block,
        })

    def _close_current_block(self) -> list[tuple[str, dict]]:
        if self._cur_index is None:
            return []
        evt = ("content_block_stop", {
            "type": "content_block_stop",
            "index": self._cur_index,
        })
        self._cur_index = None
        self._cur_type = None
        return [evt]

    # --- 진입점 ---

    def feed(self, chunk: dict) -> list[tuple[str, dict]]:
        events: list[tuple[str, dict]] = []

        # usage 반영 (어느 청크든 usage가 실려오면 갱신)
        usage = chunk.get("usage")
        if isinstance(usage, dict):
            if usage.get("prompt_tokens") is not None:
                self._input_tokens = usage["prompt_tokens"]
            if usage.get("completion_tokens") is not None:
                self._output_tokens = usage["completion_tokens"]

        # 최초 청크에서 message_start
        if not self._started:
            self._started = True
            self._message_id = chunk.get("id") or _new_message_id()
            events.append(("message_start", {
                "type": "message_start",
                "message": {
                    "id": self._message_id,
                    "type": "message",
                    "role": "assistant",
                    "model": self.request_model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {
                        "input_tokens": self._input_tokens,
                        "output_tokens": 0,
                    },
                },
            }))

        choices = chunk.get("choices") or []
        if not choices:
            # usage 전용 청크(choices 빈 배열) — output_tokens만 반영하고 종료
            return events

        choice = choices[0]
        delta = choice.get("delta") or {}

        # 텍스트 델타
        text = delta.get("content")
        if text:
            if self._cur_type != "text":
                events.extend(self._close_current_block())
                events.append(self._open_block("text", {"type": "text", "text": ""}))
            events.append(("content_block_delta", {
                "type": "content_block_delta",
                "index": self._cur_index,
                "delta": {"type": "text_delta", "text": text},
            }))

        # tool_calls 델타 (index 기반 조각)
        for tc in delta.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            oi = tc.get("index", 0)
            fn = tc.get("function") or {}

            if oi not in self._tool_blocks:
                # 새 tool call 시작 — 이전 열린 블록을 먼저 닫는다
                events.extend(self._close_current_block())
                start_evt = self._open_block("tool_use", {
                    "type": "tool_use",
                    "id": tc.get("id"),
                    "name": fn.get("name"),
                    "input": {},
                })
                self._tool_blocks[oi] = start_evt[1]["index"]
                events.append(start_evt)

            args = fn.get("arguments")
            if args:
                events.append(("content_block_delta", {
                    "type": "content_block_delta",
                    "index": self._tool_blocks[oi],
                    "delta": {"type": "input_json_delta", "partial_json": args},
                }))

        # finish_reason 도착 — 열린 블록을 닫고 stop_reason 기록
        # (message_delta/message_stop 은 finish()로 미룬다: 위 docstring 참조)
        finish_reason = choice.get("finish_reason")
        if finish_reason is not None:
            self._stop_reason = _map_stop_reason(finish_reason)
            events.extend(self._close_current_block())

        return events

    def finish(self) -> list[tuple[str, dict]]:
        """종결 이벤트: 남은 열린 블록 닫기 -> message_delta -> message_stop."""
        if self._closed:
            return []

        events: list[tuple[str, dict]] = []

        # feed()가 한 번도 호출되지 않았어도 최소한의 종결을 보장
        if not self._started:
            self._started = True
            self._message_id = _new_message_id()
            events.append(("message_start", {
                "type": "message_start",
                "message": {
                    "id": self._message_id,
                    "type": "message",
                    "role": "assistant",
                    "model": self.request_model,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": self._input_tokens, "output_tokens": 0},
                },
            }))

        events.extend(self._close_current_block())

        events.append(("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": self._stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": self._output_tokens},
        }))
        events.append(("message_stop", {"type": "message_stop"}))

        self._closed = True
        return events
