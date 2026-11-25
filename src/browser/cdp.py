"""独立浏览器：用本机 Chrome 起一个**独立配置目录**的实例，经 DevTools 管道驱动。

为什么这样做（而不是 Playwright / 接管用户的 Chrome）：
- 独立 ``--user-data-dir``：看不到用户日常浏览器里的登录态、Cookie 和密码；需要登录的站点
  由用户在这个窗口里自己登录。
- ``--remote-debugging-pipe``：DevTools 走父子进程间的管道（fd 3/4），**不开 TCP 端口**，
  本机其他进程无法连上来驱动它。
- 不打包 Playwright 和 Chromium：复用本机已装的 Chrome / Edge / Chromium（Desktop sidecar
  刻意排除了 playwright，见 desktop/scripts/build-sidecar.mjs）。

本模块只管"起进程 + 收发 CDP"，安全策略（哪些站点能碰、哪些动作要确认）在
``src/agents/tools/browser.py``。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from src.utils.state_dir import home_state_dir

_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
)
_LINUX_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge")

def _default_profile_dir() -> Path:
    # 用时才定位：state_dir 会顺手把旧的 ~/.vortocode 搬过来，导入模块不该有这种副作用
    return home_state_dir() / "browser-profile"
_COMMAND_TIMEOUT = 30.0


class BrowserError(RuntimeError):
    pass


def find_browser() -> Optional[str]:
    """本机可用的 Chromium 系浏览器路径；``VORTOONE_BROWSER_PATH`` 优先。"""
    override = (os.getenv("VORTOONE_BROWSER_PATH") or "").strip()
    if override:
        return override if os.access(override, os.X_OK) else None
    for path in _CANDIDATES:
        if os.access(path, os.X_OK):
            return path
    for name in _LINUX_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return None


class CdpBrowser:
    """一个独立浏览器实例 + 一个受控标签页（flatten 会话）。"""

    def __init__(self, executable: str, profile_dir: Optional[Path] = None, *,
                 device_mode: str = "desktop", private_preview: bool = False, headless: bool = False):
        self._headless = headless          # 没有显示器的服务器上（agent 在服务器上用浏览器）
        if device_mode not in {"desktop", "mobile"}:
            raise ValueError("Unknown browser device mode")
        if private_preview and profile_dir is None:
            raise ValueError("A private preview requires its own browser profile")
        self.device_mode = device_mode
        self._shutdown_timeout = 0.15 if private_preview else 5.0
        self._private_preview = private_preview
        self._lifecycle = asyncio.Lock()
        self._fds = set()
        self._transport = None
        self.executable = executable
        self.profile_dir = profile_dir or _default_profile_dir()
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._writer_fd: Optional[int] = None
        self._reader: Optional[asyncio.StreamReader] = None
        self._pending: Dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._session: Optional[str] = None
        self._page_detached = False
        self._pump: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    @property
    def page_attached(self) -> bool:
        return (self.running and self._session is not None and not self._page_detached
                and self._pump is not None and not self._pump.done())

    async def start(self) -> None:
        async with self._lifecycle:
            try:
                await self._start()
            except BaseException:
                await self._close()
                raise

    async def _start(self) -> None:
        from src.sandbox.policy import child_env
        if self.running:
            return
        self.profile_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        to_child_r, to_child_w = os.pipe()     # 我们写 → 浏览器从 fd 3 读
        self._fds.update((to_child_r, to_child_w))
        from_child_r, from_child_w = os.pipe()  # 浏览器写 fd 4 → 我们读

        self._fds.update((from_child_r, from_child_w))
        self._writer_fd = to_child_w

        def _wire_fds() -> None:  # 子进程里把两端放到 Chrome 约定的 3/4 号描述符
            import fcntl
            # 先挪到高位再 dup2：原描述符本身可能就是 3 或 4，直接 dup2 会互相覆盖。
            read_end = fcntl.fcntl(to_child_r, fcntl.F_DUPFD, 10)
            write_end = fcntl.fcntl(from_child_w, fcntl.F_DUPFD, 10)
            os.dup2(read_end, 3)
            os.dup2(write_end, 4)
            os.close(read_end)
            os.close(write_end)

        args = [
            self.executable,
            f"--user-data-dir={self.profile_dir}",
            "--remote-debugging-pipe",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=Translate,OptimizationHints",
            "--window-size=1280,900",
        ]
        if self._private_preview:
            args.append("--disable-sync")
        if self._headless:
            args.append("--headless=new")      # 浏览器自己的沙箱照开（97 上实测可用），不加 --no-sandbox
        args.append("about:blank")
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                # 不能用 pass_fds：子进程会在 preexec_fn **之后**关掉名单外的描述符，刚 dup 到
                # 3/4 的两端也一起被关（Chrome 报 "pipe file descriptors are not open"）。
                # Python 打开的描述符默认不可继承，close_fds=False 不会把别的句柄漏给浏览器。
                close_fds=False, preexec_fn=_wire_fds, env=child_env())
        finally:
            os.close(to_child_r)
            os.close(from_child_w)
            self._fds.difference_update((to_child_r, from_child_w))
        self._writer_fd = to_child_w
        loop = asyncio.get_running_loop()
        self._reader = asyncio.StreamReader(limit=64 * 1024 * 1024)
        self._transport, _ = await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(self._reader),
                                     os.fdopen(from_child_r, "rb", buffering=0))
        self._fds.discard(from_child_r)
        self._pump = asyncio.create_task(self._read_loop())
        await self._attach_page()
        if self.device_mode == "mobile":
            await self._mobile_mode()

    async def _read_loop(self) -> None:
        assert self._reader is not None
        try:
            while True:
                raw = await self._reader.readuntil(b"\0")
                try:
                    message = json.loads(raw[:-1])
                except ValueError:
                    continue
                if (message.get("method") == "Target.detachedFromTarget"
                        and (message.get("params") or {}).get("sessionId") == self._session):
                    self._page_detached = True
                future = self._pending.pop(message.get("id"), None) if "id" in message else None
                if future is not None and not future.done():
                    if "error" in message:
                        future.set_exception(BrowserError(str(message["error"].get("message") or message["error"])))
                    else:
                        future.set_result(message.get("result") or {})
        except (asyncio.IncompleteReadError, asyncio.CancelledError, ConnectionError):
            pass
        finally:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(BrowserError("浏览器已关闭"))
            self._pending.clear()

    async def send(self, method: str, params: Optional[dict] = None, *, page: bool = True,
                   timeout: float = _COMMAND_TIMEOUT) -> Dict[str, Any]:
        if not self.running or self._writer_fd is None:
            raise BrowserError("浏览器未启动或已关闭")
        self._next_id += 1
        message: Dict[str, Any] = {"id": self._next_id, "method": method, "params": params or {}}
        if page and self._session:
            message["sessionId"] = self._session
        future = asyncio.get_running_loop().create_future()
        self._pending[self._next_id] = future
        payload = json.dumps(message).encode() + b"\0"
        await asyncio.to_thread(os.write, self._writer_fd, payload)
        return await asyncio.wait_for(future, timeout)

    async def _attach_page(self) -> None:
        targets = await self.send("Target.getTargets", page=False)
        pages = [t for t in targets.get("targetInfos", []) if t.get("type") == "page"]
        if self._private_preview:
            pages = [page for page in pages if page.get("url") == "about:blank"]
            if len(pages) != 1:
                raise BrowserError("preview must start in a new blank tab")
        target_id = pages[0]["targetId"] if pages else (
            await self.send("Target.createTarget", {"url": "about:blank"}, page=False))["targetId"]
        attached = await self.send("Target.attachToTarget", {"targetId": target_id, "flatten": True}, page=False)
        self._session = attached["sessionId"]
        self._page_detached = False
        await self.send("Page.enable")
        await self.send("Runtime.enable")

    async def evaluate(self, expression: str) -> Any:
        return await self._evaluate(expression)

    async def _isolated_context(self) -> int:
        tree = await self.send("Page.getFrameTree")
        frame_id = tree["frameTree"]["frame"]["id"]
        world = await self.send("Page.createIsolatedWorld", {"frameId": frame_id, "worldName": "vortoone-handoff"})
        return world["executionContextId"]

    async def evaluate_isolated(self, expression: str) -> Any:
        return await self._evaluate(expression, contextId=await self._isolated_context())

    async def call_isolated(self, function: str, arguments: list[Any], *, current: Callable[[], bool]) -> Any:
        context = await self._isolated_context()
        if not current():
            return None
        result = await self.send("Runtime.callFunctionOn", {
            "functionDeclaration": function, "executionContextId": context,
            "arguments": [{"value": value} for value in arguments], "returnByValue": True,
        })
        if result.get("exceptionDetails"):
            raise BrowserError("页面检查失败")
        return (result.get("result") or {}).get("value")

    async def _evaluate(self, expression: str, **context) -> Any:
        result = await self.send("Runtime.evaluate", {
            "expression": expression, "returnByValue": True, "awaitPromise": True, **context})
        if result.get("exceptionDetails"):
            raise BrowserError(str(result["exceptionDetails"].get("text") or "页面脚本出错"))
        return (result.get("result") or {}).get("value")

    async def navigate(self, url: str) -> None:
        result = await self.send("Page.navigate", {"url": url})
        if result.get("errorText"):
            raise BrowserError(f"打开失败：{result['errorText']}")
        await self.wait_ready()

    async def wait_ready(self, timeout: float = 15.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            try:
                if await self.evaluate("document.readyState") in ("interactive", "complete"):
                    await asyncio.sleep(0.4)  # 给首屏脚本一点时间渲染出可交互元素
                    return
            except BrowserError:
                pass
            await asyncio.sleep(0.3)
        raise BrowserError("等待页面就绪超时；操作结果未知，请先核对，不要重复提交")

    async def click_at(self, x: float, y: float) -> None:
        if self.device_mode == "mobile":
            await self.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
            await self.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
            return
        for kind in ("mouseMoved", "mousePressed", "mouseReleased"):
            await self.send("Input.dispatchMouseEvent", {
                "type": kind, "x": x, "y": y, "button": "left", "clickCount": 1})

    async def insert_text(self, text: str) -> None:
        await self.send("Input.insertText", {"text": text})

    async def backspace(self) -> None:
        for kind in ("rawKeyDown", "keyUp"):
            await self.send("Input.dispatchKeyEvent", {
                "type": kind, "key": "Backspace", "code": "Backspace", "windowsVirtualKeyCode": 8})

    async def press_enter(self) -> None:
        for kind in ("keyDown", "keyUp"):
            await self.send("Input.dispatchKeyEvent", {
                "type": kind, "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13, "text": "\r"})

    async def screenshot(self) -> bytes:
        import base64
        result = await self.send("Page.captureScreenshot", {"format": "png"})
        return base64.b64decode(result.get("data") or "")

    async def _mobile_mode(self):
        version = await self.send("Browser.getVersion", page=False)
        number = str(version.get("product") or "").partition("/")[2]
        if not re.fullmatch(r"[0-9.]+", number):
            raise BrowserError("无法核对浏览器版本，未启用手机网页模式")
        await self.send("Emulation.setDeviceMetricsOverride", {
            "width": 390, "height": 844, "deviceScaleFactor": 3, "mobile": True})
        await self.send("Emulation.setTouchEmulationEnabled", {"enabled": True, "maxTouchPoints": 1})
        await self.send("Emulation.setUserAgentOverride", {"userAgent":
            f"Mozilla/5.0 (Linux; Android 13; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{number} Mobile Safari/537.36",
            "platform": "Android"})

    async def scroll(self, amount: int):
        await self.send("Input.synthesizeScrollGesture", {
            "x": 195 if self.device_mode == "mobile" else 640,
            "y": 500, "yDistance": -amount,
            "gestureSourceType": "touch" if self.device_mode == "mobile" else "mouse"})

    async def close(self) -> None:
        async with self._lifecycle:
            await self._close()

    async def _close(self):
        if self._proc is not None and self._proc.returncode is None:
            try:
                await self.send("Browser.close", page=False, timeout=self._shutdown_timeout)
            except Exception:
                try:
                    self._proc.terminate()
                except ProcessLookupError:
                    pass  # The browser may exit before the close response arrives.
            try:
                await asyncio.wait_for(self._proc.wait(), self._shutdown_timeout)
            except asyncio.TimeoutError:
                try:
                    self._proc.kill()
                except ProcessLookupError:
                    pass
                await asyncio.wait_for(self._proc.wait(), self._shutdown_timeout)
        if self._pump is not None:
            self._pump.cancel()
            await asyncio.gather(self._pump, return_exceptions=True)
        if self._transport is not None:
            self._transport.close()
        for descriptor in tuple(self._fds):
            try:
                os.close(descriptor)
            except OSError:
                pass
            self._fds.discard(descriptor)
        self._proc = self._writer_fd = self._session = self._pump = self._transport = None
