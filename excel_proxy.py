"""Small Excel/Basispoints-only OpenAI Responses proxy."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
import uvicorn

import api_key
import excel_session_capture
import excel_upstream


UPSTREAM = excel_upstream.RESPONSES_URL

DASHBOARD = Path(__file__).with_name("dashboard.html")


def _presented_key(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return request.headers.get("x-api-key", "").strip()


async def require_api_key(request: Request) -> None:
    if not api_key.store.matches(_presented_key(request)):
        raise HTTPException(status_code=401, detail="invalid API key")


async def _completed_payload(response: httpx.Response) -> dict | None:
    if "text/event-stream" not in response.headers.get("content-type", "").lower():
        data = response.json()
        return data if isinstance(data, dict) else None
    payload = None
    async for line in response.aiter_lines():
        if not line.startswith("data: "):
            continue
        raw = line[6:]
        if raw == "[DONE]":
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and isinstance(event.get("response"), dict):
            payload = event["response"]
    return payload


@asynccontextmanager
async def lifespan(_app: FastAPI):
    excel_upstream.excel_session_store.load()
    await _refresh_session()
    yield


app = FastAPI(title="Excel Responses Proxy", lifespan=lifespan)


async def _refresh_session() -> None:
    import asyncio

    await asyncio.to_thread(
        excel_session_capture.refresh_windows_excel_session,
        excel_upstream.excel_session_store,
        force=True,
    )
    await asyncio.to_thread(
        excel_session_capture.refresh_macos_excel_session,
        excel_upstream.excel_session_store,
        force=True,
    )


@app.get("/v1/models", dependencies=[Depends(require_api_key)])
@app.get("/models", dependencies=[Depends(require_api_key)])
async def models():
    return excel_upstream.merge_local_models_payload({})


@app.get("/api/config/excel-session")
async def session_status():
    return {**excel_upstream.excel_session_store.status(), "capture": excel_session_capture.cached_session_reader_status()}


@app.post("/api/config/excel-session")
async def session_refresh():
    await _refresh_session()
    return await session_status()


@app.delete("/api/config/excel-session")
async def session_clear():
    return excel_upstream.excel_session_store.clear()


@app.get("/api/config/api-key")
async def api_key_status():
    return {**api_key.store.load(), "path": api_key.KEY_FILE}


@app.post("/api/config/api-key")
async def api_key_update(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        return JSONResponse({"error": {"message": "JSON object required"}}, status_code=400)
    try:
        if body.get("key") is None:
            return {**api_key.store.regenerate(), "path": api_key.KEY_FILE}
        return {**api_key.store.set_custom(body["key"]), "path": api_key.KEY_FILE}
    except ValueError as exc:
        return JSONResponse({"error": {"message": str(exc)}}, status_code=400)


@app.post("/v1/responses", dependencies=[Depends(require_api_key)])
@app.post("/responses", dependencies=[Depends(require_api_key)])
async def responses(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": {"message": "invalid JSON"}}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": {"message": "JSON object required"}}, status_code=400)
    await _refresh_session()
    try:
        headers = excel_upstream.excel_session_store.request_headers(stream=bool(body.get("stream")))
    except RuntimeError as exc:
        return JSONResponse({"error": {"message": str(exc)}}, status_code=401)
    model_id = excel_upstream.excel_model_id(body.get("model")) or excel_upstream.MODEL_ID
    wire = excel_upstream.prepare_responses_body(
        body, tools_version_id=excel_upstream.excel_session_store.tools_version_id()
    )
    timeout = httpx.Timeout(300.0, connect=30.0)
    client = httpx.AsyncClient(timeout=timeout, http2=False)
    if wire.get("stream"):
        # ``client.stream`` is an async context manager. Entering it through a
        # temporary leaves nothing holding that generator, so it is finalized
        # as soon as this function returns and its ``finally`` closes the
        # upstream mid-stream. Send the request directly instead.
        request = client.build_request("POST", UPSTREAM, headers=headers, json=wire)
        upstream = await client.send(request, stream=True)

        async def stream_body():
            try:
                async for chunk in upstream.aiter_bytes():
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

        return StreamingResponse(stream_body(), status_code=upstream.status_code, media_type="text/event-stream")
    try:
        upstream = await client.post(UPSTREAM, headers=headers, json=wire)
        payload = await _completed_payload(upstream)
    finally:
        await client.aclose()
    if payload is None:
        return JSONResponse({"error": {"message": "Basispoints returned no completed response"}}, status_code=502)
    payload["model"] = model_id
    output_text = ""
    for item in payload.get("output", []):
        if isinstance(item, dict) and item.get("type") == "message":
            for part in item.get("content", []):
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    output_text += part["text"]
    tool_call = excel_upstream.extract_client_tool_call(
        output_text, excel_upstream.client_tool_types(body)
    ) or excel_upstream.extract_native_client_tool_call(payload, body)
    if tool_call is not None:
        payload = excel_upstream.response_payload_with_tool_call(
            payload, tool_call, model_id=model_id
        )
    return JSONResponse(payload, status_code=upstream.status_code)


@app.get("/")
async def root():
    return FileResponse(DASHBOARD)


@app.get("/ui")
async def ui():
    return FileResponse(DASHBOARD)


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000, access_log=False)
