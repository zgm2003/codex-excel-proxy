"""OpenAI Chat Completions compatibility for the Excel/Basispoints upstream.

Clients that only speak ``/v1/chat/completions`` are translated onto the single
Responses request this proxy already builds, so there is still exactly one
upstream call per request. Tool calls keep using the same marker/transport
protocol as ``/v1/responses``; this module only re-shapes the payloads.
"""

from __future__ import annotations

import json
import time
from typing import AsyncIterator
from uuid import uuid4

import excel_upstream

_MARKER_OPEN = excel_upstream.TOOL_CALL_MARKER_OPEN
_TEXT_PART_TYPES = {"text", "input_text", "output_text"}


class ChatCompatError(ValueError):
    """A chat request that cannot be expressed on the Excel upstream."""


def _content_text(value: object, where: str) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        raise ChatCompatError(f"{where}: content must be a string or a list of parts")
    parts: list[str] = []
    for part in value:
        if isinstance(part, str):
            parts.append(part)
            continue
        if not isinstance(part, dict):
            raise ChatCompatError(f"{where}: unsupported content part {part!r}")
        kind = str(part.get("type") or "text").strip().lower()
        if kind not in _TEXT_PART_TYPES or not isinstance(part.get("text"), str):
            raise ChatCompatError(
                f"{where}: content part {kind!r} is unsupported; the Excel upstream is text-only"
            )
        parts.append(part["text"])
    return "".join(parts)


def _assistant_tool_items(value: object, where: str) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ChatCompatError(f"{where}: tool_calls must be a list")
    items: list[dict] = []
    for call in value:
        if not isinstance(call, dict):
            raise ChatCompatError(f"{where}: each tool_call must be an object")
        function = call.get("function")
        name = function.get("name") if isinstance(function, dict) else None
        call_id = call.get("id")
        if not isinstance(name, str) or not name.strip():
            raise ChatCompatError(f"{where}: tool_call.function.name is required")
        if not isinstance(call_id, str) or not call_id:
            raise ChatCompatError(f"{where}: tool_call.id is required")
        arguments = function.get("arguments")
        items.append(
            {
                "type": "function_call",
                "call_id": call_id,
                "name": name.strip(),
                "arguments": arguments
                if isinstance(arguments, str)
                else json.dumps(arguments or {}, separators=(",", ":"), ensure_ascii=False),
            }
        )
    return items


def _flat_tools(value: object) -> list[dict]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ChatCompatError("tools must be a list")
    tools: list[dict] = []
    for tool in value:
        if not isinstance(tool, dict):
            raise ChatCompatError("each tool must be an object")
        kind = str(tool.get("type") or "function").strip().lower()
        function = tool.get("function")
        name = function.get("name") if isinstance(function, dict) else None
        if kind != "function" or not isinstance(name, str) or not name.strip():
            raise ChatCompatError(
                f"tool {name!r} of type {kind!r} is unsupported; chat completions relays function tools only"
            )
        entry: dict[str, object] = {"type": "function", "name": name.strip()}
        description = function.get("description")
        if isinstance(description, str) and description:
            entry["description"] = description
        parameters = function.get("parameters")
        entry["parameters"] = parameters if isinstance(parameters, dict) else {}
        tools.append(entry)
    return tools


def responses_request_from_chat(body: dict) -> dict:
    """Translate a chat-completions request into a Responses request."""
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ChatCompatError("messages must be a non-empty list")

    instructions: list[str] = []
    items: list = []
    for index, message in enumerate(messages):
        where = f"messages[{index}]"
        if not isinstance(message, dict):
            raise ChatCompatError(f"{where}: must be an object")
        role = str(message.get("role") or "").strip().lower()
        if role in {"system", "developer"}:
            text = _content_text(message.get("content"), where)
            if text:
                instructions.append(text)
            continue
        if role == "tool":
            call_id = message.get("tool_call_id")
            if not isinstance(call_id, str) or not call_id:
                raise ChatCompatError(f"{where}: tool_call_id is required")
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": _content_text(message.get("content"), where),
                }
            )
            continue
        if role == "assistant":
            items.extend(_assistant_tool_items(message.get("tool_calls"), where))
            text = _content_text(message.get("content"), where)
            if text:
                items.append(excel_upstream._message_item("assistant", text))
            continue
        if role != "user":
            raise ChatCompatError(f"{where}: role {role!r} is unsupported")
        items.append(
            excel_upstream._message_item("user", _content_text(message.get("content"), where))
        )

    request: dict[str, object] = {
        "model": body.get("model"),
        "input": items,
        "stream": bool(body.get("stream", False)),
    }
    if instructions:
        request["instructions"] = "\n\n".join(instructions)
    tools = _flat_tools(body.get("tools"))
    if tools:
        request["tools"] = tools
    for key in ("tool_choice", "reasoning", "reasoning_effort"):
        if key in body:
            request[key] = body[key]
    return request


def _payload_text(payload: dict) -> str:
    parts: list[str] = []
    for item in payload.get("output") or []:
        if isinstance(item, dict) and item.get("type") == "message":
            for part in item.get("content") or []:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    parts.append(part["text"])
    return "".join(parts)


def _chat_tool_call(call: dict) -> dict:
    return {
        "id": call.get("call_id") or call.get("id"),
        "type": "function",
        "function": {
            "name": call.get("name") or "",
            "arguments": call.get("arguments") or "",
        },
    }


def _client_tool_call(payload: dict, source: dict) -> dict | None:
    # The upstream answers a tool request with either the run_officejs transport
    # or a native call; both are decoded exactly as /v1/responses decodes them.
    return excel_upstream.extract_native_client_tool_call(payload, source)


def _split_marker(text: str, allowed_tools: dict[str, str]) -> tuple[str, dict | None]:
    if not text or not allowed_tools:
        return text, None
    call = excel_upstream.extract_client_tool_call(text, allowed_tools)
    if call is None:
        return text, None
    index = text.find(_MARKER_OPEN)
    return (text[:index] if index >= 0 else text), call


def _usage(payload: dict) -> dict:
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    return {
        "prompt_tokens": usage.get("input_tokens", 0),
        "completion_tokens": usage.get("output_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
    }


def chat_completion_from_payload(
    payload: dict,
    model_id: str,
    source: dict,
) -> dict:
    text, marker_call = _split_marker(
        _payload_text(payload), excel_upstream.client_tool_types(source)
    )
    call = _client_tool_call(payload, source) or marker_call
    tool_calls = [_chat_tool_call(call)] if call is not None else []
    message: dict[str, object] = {"role": "assistant", "content": text or None}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": payload.get("id") or f"chatcmpl-{uuid4().hex}",
        "object": "chat.completion",
        "created": payload.get("created_at") or int(time.time()),
        "model": model_id,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if tool_calls else "stop",
            }
        ],
        "usage": _usage(payload),
    }


def _chunk_bytes(
    chunk_id: str,
    created: int,
    model_id: str,
    delta: dict,
    finish_reason: str | None = None,
) -> bytes:
    payload = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model_id,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    return f"data: {json.dumps(payload, separators=(',', ':'), ensure_ascii=False)}\n\n".encode()


def _emit_ready(buffer: str, allowed_tools: dict[str, str]) -> tuple[str, str]:
    """Split buffered text into what is safe to stream and a possible marker tail."""
    if not allowed_tools:
        return buffer, ""
    index = buffer.find(_MARKER_OPEN)
    if index >= 0:
        return buffer[:index], buffer[index:]
    for size in range(min(len(_MARKER_OPEN) - 1, len(buffer)), 0, -1):
        if buffer.endswith(_MARKER_OPEN[:size]):
            return buffer[:-size], buffer[-size:]
    return buffer, ""


async def chat_chunks(
    lines: AsyncIterator[str],
    *,
    model_id: str,
    source: dict,
) -> AsyncIterator[bytes]:
    """Translate the upstream Responses event stream into chat-completion chunks."""
    allowed_tools = excel_upstream.client_tool_types(source)
    chunk_id = f"chatcmpl-{uuid4().hex}"
    created = int(time.time())
    started = False
    buffered = ""
    completed: dict | None = None

    def delta(delta_payload: dict) -> bytes:
        nonlocal started
        payload = delta_payload
        if not started:
            payload = {"role": "assistant", **delta_payload}
            started = True
        return _chunk_bytes(chunk_id, created, model_id, payload)

    async for line in lines:
        if not line.startswith("data:"):
            continue
        raw = line[5:].strip()
        if not raw or raw == "[DONE]":
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "response.output_text.delta":
            piece = event.get("delta")
            if isinstance(piece, str):
                buffered += piece
                ready, buffered = _emit_ready(buffered, allowed_tools)
                if ready:
                    yield delta({"content": ready})
        elif kind == "response.completed":
            response = event.get("response")
            completed = response if isinstance(response, dict) else None
            break

    # Tool calls are resolved once, from the completed response, so a call that
    # arrives through the run_officejs transport is never shown to the client.
    call = _client_tool_call(completed, source) if completed is not None else None
    text, marker_call = _split_marker(buffered, allowed_tools)
    call = call or marker_call
    if call is not None:
        yield delta({"tool_calls": [_chat_tool_call(call)]})
    elif text:
        yield delta({"content": text})
    yield _chunk_bytes(
        chunk_id,
        created,
        model_id,
        {},
        "tool_calls" if call is not None else "stop",
    )
    yield b"data: [DONE]\n\n"