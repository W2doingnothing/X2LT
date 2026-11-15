# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 W2doingnothing
"""Windows user-scoped DPAPI vault; never imports Telegram credentials."""
import csv
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


class _Blob(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(data, decrypt=False):
    if os.name != 'nt':
        raise OSError('凭证保护需要 Windows DPAPI，禁止明文降级')
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(data)
    source = _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = _Blob()
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel.LocalFree(output.pbData)


def protect(data):
    return _dpapi(data)


def unprotect(data):
    return _dpapi(data, True)


def private_directory(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    if os.name == 'nt':
        flags = {'creationflags': subprocess.CREATE_NO_WINDOW}
        identity = subprocess.run(['whoami', '/user', '/fo', 'csv', '/nh'], capture_output=True, check=True, text=True, **flags)
        sid = next(csv.reader([identity.stdout.strip()]))[-1]
        if not re.fullmatch(r'S-1-\d+(?:-\d+)+', sid):
            raise OSError('无法确认当前用户 SID')
        result = subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r', f'*{sid}:(OI)(CI)F', '*S-1-5-18:(OI)(CI)F'], capture_output=True, **flags)
        if result.returncode:
            raise OSError('无法限制凭证目录权限；未保存凭证')
    return path


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf8') as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


class Vault:
    def __init__(self, path):
        self.path = Path(path)

    def save(self, data):
        blob = protect(json.dumps(data).encode('utf8'))
        private_directory(self.path.parent)
        fd, name = tempfile.mkstemp(dir=self.path.parent, suffix='.encrypted.tmp')
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(blob)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)

    def load(self):
        if not self.path.exists():
            return {}
        return json.loads(unprotect(self.path.read_bytes()).decode('utf8'))


def import_legacy(root):
    root = Path(root)
    result = {}
    # Read the old file but only parse explicitly allowed X fields.
    if (root / '.env').exists():
        for line in (root / '.env').read_text(encoding='utf-8-sig').splitlines():
            key, sep, value = line.partition('=')
            key = key.strip()
            if sep and key in {'X_USER', 'X_PROXY'}:
                result[{'X_USER': 'username', 'X_PROXY': 'proxy'}[key]] = value.strip().strip('\"\'')
    cookies = root / 'cookies.txt'
    if cookies.exists():
        for line in cookies.read_text(encoding='utf-8-sig').splitlines():
            cols = line.split('\t')
            if len(cols) == 7 and cols[0].removeprefix('#HttpOnly_') in {'.x.com', 'x.com', '.twitter.com', 'twitter.com'} and cols[5] in {'auth_token', 'ct0'}:
                result[cols[5]] = cols[6]
    return result


def redact(message, secrets=()):
    message = str(message)
    for secret in sorted((s for s in secrets if s), key=len, reverse=True):
        message = message.replace(secret, '[已隐藏]')
    message = re.sub(r'(?i)((?:auth_token|ct0|TG_SESSION|TG_API_HASH)\s*[=:]\s*)[^\s,;]+', r'\1[已隐藏]', message)
    message = re.sub(r'((?:https?|socks5h?)://)[^/\s@]+@', r'\1[已隐藏]@', message)
    return re.sub(r'(https?://[^\s?]+)\?[^\s]+', r'\1?[已隐藏]', message)
