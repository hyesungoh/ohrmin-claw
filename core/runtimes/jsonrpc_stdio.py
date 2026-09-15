"""줄 단위 JSON-RPC 2.0 클라이언트 — Codex app-server·Grok ACP 공용 (stdio transport).

- 요청: id 매칭 → result 반환 또는 JsonRpcError raise.
- 알림: 수신 순서대로 on_notification(method, params) 호출(동기 콜백 권장, awaitable이면 읽기 루프에서 await).
- 서버→클라이언트 요청: on_request(method, params)의 반환값을 result로 응답(태스크로 처리해 읽기 루프를 막지 않음).
  JsonRpcError raise → 해당 error 응답, 핸들러 없음 = -32601.
- transport EOF·읽기 오류 = 연결 종료 → 대기 요청 전부 RuntimeUnavailable + on_close(error) 1회. 이후 요청도 RuntimeUnavailable.
- jsonrpc_header=False: 송신 메시지에서 `"jsonrpc": "2.0"`을 생략한다(Codex app-server 규약). 수신은 헤더 유무 무관.
- transport 표면: `async readline() -> bytes`(EOF = b"") · `write(bytes)` · `async drain()` · `async close()`
  (core.runtimes.process.ProcessTransport 또는 테스트 in-memory transport).
"""
import asyncio
import inspect
import json
import traceback

METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603
_CLOSE_WAIT_TIMEOUT = 10.0


class JsonRpcError(Exception):
    """JSON-RPC error 응답."""

    def __init__(self, code, message, data=None):
        super().__init__(f"JSON-RPC error {code}: {message}")
        self.code = code
        self.message = message
        self.data = data


class RuntimeUnavailable(Exception):
    """런타임 연결(프로세스)이 종료됐거나 쓸 수 없음."""


class JsonRpcClient:
    def __init__(
        self,
        transport,
        *,
        on_notification=None,
        on_request=None,
        on_close=None,
        jsonrpc_header: bool = True,
    ):
        self._transport = transport
        self._on_notification = on_notification
        self._on_request = on_request
        self._on_close = on_close
        self._jsonrpc_header = jsonrpc_header
        self._pending: dict = {}
        self._next_id = 0
        self._closed = False
        self._reader: asyncio.Task | None = None
        self._write_lock = asyncio.Lock()
        self._request_tasks: set = set()

    @property
    def closed(self) -> bool:
        return self._closed

    def start(self) -> None:
        """읽기 루프 시작 (이벤트 루프 안에서 1회)."""
        if self._reader is None:
            self._reader = asyncio.get_running_loop().create_task(self._read_loop())

    async def request(self, method: str, params=None, timeout: float | None = None):
        if self._closed:
            raise RuntimeUnavailable(f"connection closed (request {method})")
        self._next_id += 1
        request_id = self._next_id
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._send(self._message(request_id, method, params))
            if timeout is None:
                return await future
            return await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(request_id, None)

    async def notify(self, method: str, params=None) -> None:
        await self._send(self._message(None, method, params))

    async def close(self) -> None:
        """transport 종료 후 읽기 루프 정리 (멱등)."""
        try:
            await self._transport.close()
        except Exception as e:
            print(f"⚠️ JSON-RPC transport 종료 오류: {type(e).__name__}: {e}")
        if self._reader is not None and not self._reader.done():
            try:
                await asyncio.wait_for(asyncio.shield(self._reader), _CLOSE_WAIT_TIMEOUT)
            except asyncio.TimeoutError:
                self._reader.cancel()
        self._mark_closed(None)

    # ── 내부 ──

    @staticmethod
    def _message(request_id, method, params) -> dict:
        message = {}
        if request_id is not None:
            message["id"] = request_id
        message["method"] = method
        if params is not None:
            message["params"] = params
        return message

    async def _send(self, message: dict) -> None:
        if self._jsonrpc_header:
            message = {"jsonrpc": "2.0", **message}
        data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        async with self._write_lock:
            if self._closed:
                raise RuntimeUnavailable("connection closed")
            try:
                self._transport.write(data)
                await self._transport.drain()
            except Exception as e:
                raise RuntimeUnavailable(f"write failed: {type(e).__name__}") from e

    async def _read_loop(self) -> None:
        error = None
        try:
            while True:
                line = await self._transport.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except ValueError:
                    print(f"⚠️ JSON-RPC 해석 불가 줄 무시: {line[:120]!r}")
                    continue
                if isinstance(message, dict):
                    await self._dispatch(message)
        except asyncio.CancelledError:
            self._mark_closed(None)
            raise
        except Exception as e:
            error = e
        self._mark_closed(error)

    async def _dispatch(self, message: dict) -> None:
        method = message.get("method")
        if method is None:
            future = self._pending.get(message.get("id"))
            if future is None or future.done():
                return
            if "error" in message:
                err = message.get("error") or {}
                future.set_exception(JsonRpcError(err.get("code"), err.get("message"), err.get("data")))
            else:
                future.set_result(message.get("result"))
            return
        params = message.get("params")
        if "id" in message:
            task = asyncio.get_running_loop().create_task(self._answer(message["id"], method, params))
            self._request_tasks.add(task)
            task.add_done_callback(self._request_tasks.discard)
            return
        if self._on_notification is None:
            return
        try:
            result = self._on_notification(method, params)
            if inspect.isawaitable(result):
                await result
        except Exception:
            print(f"⚠️ JSON-RPC 알림 처리 실패({method})")
            traceback.print_exc()

    async def _answer(self, request_id, method: str, params) -> None:
        if self._on_request is None:
            response = {"id": request_id, "error": {"code": METHOD_NOT_FOUND, "message": f"Method not found: {method}"}}
        else:
            try:
                result = self._on_request(method, params)
                if inspect.isawaitable(result):
                    result = await result
                response = {"id": request_id, "result": result}
            except JsonRpcError as e:
                response = {"id": request_id, "error": {"code": e.code, "message": e.message}}
            except Exception as e:
                print(f"⚠️ JSON-RPC 역요청 처리 실패({method}): {type(e).__name__}: {e}")
                response = {"id": request_id, "error": {"code": INTERNAL_ERROR, "message": "internal error"}}
        try:
            await self._send(response)
        except RuntimeUnavailable:
            pass

    def _mark_closed(self, error) -> None:
        if self._closed:
            return
        self._closed = True
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(RuntimeUnavailable("connection closed"))
        if self._on_close is not None:
            try:
                self._on_close(error)
            except Exception:
                traceback.print_exc()
