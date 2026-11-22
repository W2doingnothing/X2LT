# SPDX-License-Identifier: GPL-2.0-only
"""X2LT client for the installed Guangyapan public command-line protocol."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
from uuid import uuid4


_AGENT = "X2LT"
_SUPPORTED = {"1.0.3+28"}
_TERMINAL = {"SUCCEEDED", "PARTIAL", "FAILED", "STOPPED"}
_MAX_OUTPUT = 4 * 1024 * 1024
_KNOWN_CODES = {
    "LOGIN_DENIED": "光鸭登录已取消",
    "LOGIN_REQUIRED": "请先连接光鸭账号",
    "LOGIN_PENDING": "请先在浏览器完成光鸭登录",
    "LOGIN_STATUS_UNAVAILABLE": "暂时无法验证光鸭登录",
    "LOGIN_WORKER_START_FAILED": "光鸭登录后台未能启动",
    "REMOTE_NAME_CONFLICT": "光鸭目录或文件名冲突，请检查远端位置",
    "REMOTE_RESULT_UNKNOWN": "光鸭结果待核对，请先检查云端位置",
    "REMOTE_DIRECTORY_NOT_FOUND": "光鸭目录已不可用",
    "SKILL_RELOAD_REQUIRED": "光鸭插件版本已变更，请稍后重试",
    "PERMISSION_DENIED": "光鸭插件无法访问它的本地状态目录",
}


class GuangyapanError(ValueError):
    def __init__(self, code, fallback):
        self.code = code if isinstance(code, str) and re.fullmatch(r'[A-Z0-9_]{1,100}', code) else None
        message = _KNOWN_CODES.get(self.code, fallback)
        if self.code and self.code not in _KNOWN_CODES and self.code != 'OK':
            message = f'{message}（{self.code}）'
        super().__init__(message)


def _error(code, fallback):
    return GuangyapanError(code, fallback)


def _find_node():
    exe = "node.exe" if os.name == "nt" else "node"
    for directory in os.get_exec_path():
        folder = Path(directory.strip('"'))
        candidate = folder / exe
        if folder.is_absolute() and candidate.is_file():
            return str(candidate.resolve())
    return None


def _invoke(args, *, cwd, env, timeout, stdin=None):
    output = bytearray()
    read_failure = []

    def drain(stream):
        try:
            with stream:
                while block := stream.read(64 * 1024):
                    output.extend(block)
                    if len(output) > _MAX_OUTPUT:
                        output.clear()
                        read_failure.append("光鸭响应超过大小限制")
                        process.kill()
                        return
        except OSError:
            if not read_failure:
                read_failure.append("无法读取光鸭插件响应")

    with tempfile.TemporaryFile() as request:
        if stdin is not None:
            request.write(stdin)
            request.seek(0)
        try:
            process = subprocess.Popen(
                [str(value) for value in args],
                cwd=cwd, env=env, stdin=request, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError:
            raise ValueError("无法启动光鸭插件，请检查 Node.js 和插件安装") from None
        reader = threading.Thread(target=drain, args=(process.stdout,), daemon=True)
        reader.start()
        deadline = time.monotonic() + timeout
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise ValueError("光鸭请求响应超时，请查询原任务状态") from None
        reader.join(timeout=max(0, deadline - time.monotonic()))
        if reader.is_alive():
            raise ValueError("光鸭插件响应未结束，请查询原任务状态")
        if read_failure:
            raise ValueError(read_failure[0])
    try:
        payload = json.loads(output.decode("utf-8-sig"))
        if type(payload) is not dict or payload.get("version") != 1 or not isinstance(payload.get("code"), str):
            raise ValueError
    except (UnicodeError, ValueError):
        raise ValueError("光鸭插件返回了无法识别的响应") from None
    return payload


class Guangyapan:
    """Hold the app's own fixed identity. The CLI owns and protects credentials."""

    def __init__(self, data_dir, *, installation=None, node=None, transport=_invoke):
        self.directory = Path(data_dir).resolve() / "guangyapan"
        self.profile = self.directory / "agent-profile"
        self.state = self.directory / "state"
        self.installation = Path(installation or Path.home() / ".codex" / "skills" / "guangyapan-drive").resolve()
        self.cli = self.installation / "bin" / "guangyapan.mjs"
        self.instructions = self.installation / "references" / "skill.md"
        self.node = str(node) if node else None
        self.transport = transport
        self._version = None
        self._lock = threading.RLock()

    def _environment(self):
        env = {
            k: v for k, v in os.environ.items()
            if not k.upper().startswith("GUANGYAPAN_DRIVE_")
            and k.upper() not in {"NODE_OPTIONS", "NODE_PATH", "NODE_ENV", "NODE_TEST_CONTEXT"}
        }
        env.update(
            GUANGYAPAN_DRIVE_STATE_ROOT=str(self.state),
            GUANGYAPAN_DRIVE_LOGIN_MODE="browser",
            GUANGYAPAN_DRIVE_TELEMETRY="0",
        )
        return env

    def _call(self, args, *, stdin=None, timeout=40):
        node = self.node or _find_node()
        if not node:
            raise ValueError("使用光鸭需要安装 Node.js 20 或以上版本")
        if not self.cli.is_file() or self.cli.resolve() != self.cli:
            raise ValueError("未找到受支持的光鸭插件入口")
        return self.transport(
            [node, str(self.cli), *args], cwd=self.installation,
            env=self._environment(), stdin=stdin, timeout=timeout,
        )

    def _prepare(self, *, force=False):
        with self._lock:
            if self._version and not force:
                return self._version
            self._version = None
            try:
                self.profile.mkdir(parents=True, exist_ok=True)
                self.state.mkdir(parents=True, exist_ok=True)
            except OSError:
                raise ValueError("无法创建 X2LT 的光鸭状态目录") from None
            result = self._call(["prepare", "--agent-name", _AGENT])
            if result.get("code") != "OK":
                raise _error(result.get("code"), "光鸭插件未能准备就绪")
            data = result.get("data") or {}
            try:
                entry = Path(data["cli_path"])
                instructions = Path(data["instructions_path"])
            except (KeyError, TypeError):
                raise ValueError("光鸭插件未返回受支持的入口") from None
            if entry != self.cli or instructions != self.instructions or instructions.resolve() != instructions:
                raise ValueError("光鸭插件返回的入口与安装目录不一致")
            version = data.get("instructions_version")
            if not isinstance(version, str) or version not in _SUPPORTED:
                raise ValueError("当前光鸭插件版本尚未由 X2LT 验证")
            try:
                with instructions.open(encoding="utf-8") as handle:
                    handle.read(128 * 1024)
            except (OSError, UnicodeError):
                raise ValueError("无法读取光鸭插件的操作规范") from None
            self._version = version
            return version

    def _business(self, command, *args, stdin=None):
        def execute(version):
            return self._call([
                command, "--skill-version", version, "--agent-name", _AGENT,
                "--agent-profile-dir", str(self.profile), *args,
            ], stdin=stdin)

        response = execute(self._prepare())
        if response.get("code") == "SKILL_RELOAD_REQUIRED":
            response = execute(self._prepare(force=True))
        return response

    def _login_status(self, command):
        response = self._business(command)
        code = response.get("code")
        if code not in {"OK", "LOGIN_REQUIRED", "LOGIN_PENDING", "LOGIN_DENIED", "LOGIN_STATUS_UNAVAILABLE"}:
            raise _error(code, "光鸭登录服务异常")
        data = response.get("data") or {}
        result = {"code": code, "logged_in": data.get("logged_in"), "state": data.get("state")}
        if result["logged_in"] is True and isinstance(data.get("account_id"), str):
            result["account_id"] = data["account_id"]
        for key in ("expires_at", "user_agreement_url", "privacy_policy_url"):
            if isinstance(data.get(key), str):
                result[key] = data[key]
        return result

    def login(self):
        return self._login_status("login")

    def relogin(self):
        return self._login_status("relogin")

    def status(self):
        return self._login_status("login-status")

    def list(self, parent, page=0):
        result = self.submit({
            "id": "list", "type": "list", "remote_parent_id": parent,
            "page": page, "page_size": 100,
        }, str(uuid4()), str(uuid4()))
        item = self._item(result, "list", "无法读取光鸭目录")
        if not isinstance(item.get("items"), list):
            raise ValueError("光鸭目录返回不完整")
        return item

    def mkdir(self, parent, name, *, request_id=None, task_id=None):
        result = self.submit({
            "id": "folder", "type": "create_folder", "remote_parent_id": parent, "name": name,
        }, request_id or str(uuid4()), task_id or str(uuid4()))
        data = result.get("data") or {}
        batch_id = result.get("batch_id") or data.get("batch_id")
        if result.get('code') == 'DUPLICATE_SUSPECTED':
            batch_id = data.get('existing_batch_id') or batch_id
            if batch_id:
                result = self.batch(batch_id)
        if batch_id:
            deadline = time.monotonic() + 45
            while (result.get("batch_state") or (result.get("data") or {}).get("batch_state")) not in _TERMINAL:
                if time.monotonic() >= deadline:
                    raise ValueError("光鸭目录创建结果待核对")
                result = self.batch(batch_id)
                if result.get("code") not in {"OK", "SUBMIT_PENDING"}:
                    break
                if (result.get("batch_state") or (result.get("data") or {}).get("batch_state")) not in _TERMINAL:
                    time.sleep(1)
        item = self._item(result, "folder", "无法确认光鸭目录已创建")
        if not isinstance(item.get("fileId"), str) or not item["fileId"]:
            raise ValueError("光鸭未返回目录编号")
        return item["fileId"]

    def submit(self, operation, request_id, task_id):
        scene = {"list": "browse", "create_folder": "organize",
                 "upload": "upload", "download": "download"}.get(operation.get("type"))
        if scene is None:
            raise ValueError("不支持的光鸭操作")
        payload = {
            "version": 1, "request_id": request_id, "user_task_id": task_id, "scene": scene,
            "operations": [operation],
        }
        stdin = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        if len(stdin) > 1024 * 1024:
            raise ValueError("光鸭请求超过大小限制")
        return self._business("run", stdin=stdin)

    def _item(self, result, operation, fallback):
        for item in (result.get("data") or {}).get("items", []):
            if item.get("operation_id") != operation:
                continue
            if item.get("state") == "SUCCEEDED" and isinstance(item.get("result"), dict):
                return item["result"]
            raise _error(item.get("code"), fallback)
        data = result.get("data") or {}
        error = data.get("error") or {}
        raise _error(error.get("cause_code") or error.get("code") or result.get("code"), fallback)

    def _batch(self, command, batch_id):
        if not isinstance(batch_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,200}", batch_id) or batch_id.startswith("-"):
            raise ValueError("光鸭任务编号无效")
        return self._business(command, "--batch-id", batch_id)

    def batch(self, batch_id):
        return self._batch("status", batch_id)

    def stop(self, batch_id):
        return self._batch("stop", batch_id)
