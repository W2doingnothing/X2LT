# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 W2doingnothing
"""Read Telegram messages and stream their attachments through Telethon 1.x."""
import asyncio
from concurrent.futures import CancelledError
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from math import ceil, isfinite
import re
from threading import Lock, Thread
from time import time
from types import SimpleNamespace
from urllib.parse import parse_qsl, unquote, urlsplit

from ..local_import_files import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS


class TelegramTransportError(RuntimeError):
    def __init__(self, message, *, code='telegram_error', seconds=0):
        super().__init__(message)
        self.code = code
        self.seconds = seconds


def _load_telethon():
    try:
        from telethon import TelegramClient, errors, functions, types, utils
        from telethon.sessions import StringSession
    except ImportError:
        raise TelegramTransportError('当前运行环境缺少 Telegram 组件') from None
    return SimpleNamespace(client=TelegramClient, session=StringSession, errors=errors, functions=functions,
                           types=types, utils=utils)


_LOGGER = logging.getLogger(__name__ + '.client')
_LOGGER.addHandler(logging.NullHandler())
_LOGGER.propagate = False

_USERNAME = r'[A-Za-z][A-Za-z0-9_]{2,31}'
_ID = r'[1-9][0-9]{0,15}'
_MESSAGE_ID = r'[1-9][0-9]{0,9}'
_REF_ERRORS = ('FileReferenceExpiredError', 'FileReferenceInvalidError', 'FilerefUpgradeNeededError')
_AUTH_ERRORS = ('AuthKeyUnregisteredError', 'SessionRevokedError', 'AuthKeyDuplicatedError')
_OPERATION_TIMEOUT = 120
_CLOSE_TIMEOUT = 3
_PREVIEW_BYTES = 256 * 1024
_PREVIEW_DIMENSION = 512
_DELIVERY_TYPES = {
    'App': 'app', 'Sms': 'sms', 'SmsWord': 'sms_text', 'SmsPhrase': 'sms_text',
    'Call': 'call', 'FlashCall': 'missed_call', 'MissedCall': 'missed_call',
    'EmailCode': 'email', 'SetUpEmailRequired': 'email_setup',
    'FragmentSms': 'fragment', 'FirebaseSms': 'official_sms',
}


@dataclass
class _QRLoginAttempt:
    client: object
    proxy: str
    ready: asyncio.Future
    task: asyncio.Task | None = None


def _message_link(value):
    if isinstance(value, str):
        value = value.strip()
        if value.startswith('t.me/'):
            value = 'https://' + value
        if len(value) <= 2000 and not any(ord(char) < 32 or char == '\\' for char in value):
            try:
                parts = urlsplit(value)
                query = dict(parse_qsl(parts.query, keep_blank_values=True))
                if (parts.scheme == 'https' and parts.netloc.lower() in ('t.me', 't.me:443')
                        and 'comment' not in query):
                    private = re.fullmatch(rf'/c/({_ID})/(?:{_MESSAGE_ID}/)?({_MESSAGE_ID})/?', parts.path)
                    public = re.fullmatch(rf'/(?:s/)?({_USERNAME})/(?:{_MESSAGE_ID}/)?({_MESSAGE_ID})/?', parts.path)
                    if private and int(private[2]) < 2**31:
                        return -(10**12 + int(private[1])), int(private[2])
                    if (public and int(public[2]) < 2**31
                            and public[1].lower() not in {'joinchat', 'share', 'addlist', 'proxy', 'socks'}):
                        return public[1], int(public[2])
            except ValueError:
                pass
    raise TelegramTransportError('请输入 Telegram 消息链接，例如 https://t.me/channel/123')


def _proxy_options(value):
    if value == '':
        return None
    try:
        parts = urlsplit(value)
        protocols = {'http': 'http', 'socks5': 'socks5', 'socks5h': 'socks5'}
        proxy_type = protocols.get(parts.scheme)
        if proxy_type and parts.hostname and parts.port and parts.path in ('', '/') and not parts.query and not parts.fragment:
            return {'proxy_type': proxy_type, 'addr': parts.hostname, 'port': parts.port, 'rdns': True,
                    'username': unquote(parts.username) if parts.username is not None else None,
                    'password': unquote(parts.password) if parts.password is not None else None}
    except (TypeError, ValueError, AttributeError):
        pass
    raise TelegramTransportError('Telegram 代理须为 HTTP 或 SOCKS5 地址', code='proxy')


def _protected(message):
    return (getattr(message, 'noforwards', False) or getattr(message, 'ttl_period', None)
            or getattr(getattr(message, 'media', None), 'ttl_seconds', None))


def _photo_size(size, types):
    if isinstance(size, types.PhotoSizeProgressive):
        return max(size.sizes, default=0)
    if isinstance(size, types.PhotoCachedSize):
        return len(size.bytes)
    if isinstance(size, types.PhotoSize):
        return size.size
    return 0


def _media(message, types):
    """Return metadata and the precise file variant it describes."""
    media = getattr(message, 'media', None)
    if isinstance(media, types.MessageMediaPhoto) and isinstance(media.photo, types.Photo):
        photo = media.photo
        size = max(photo.sizes, key=lambda value: _photo_size(value, types), default=None)
        byte_count = _photo_size(size, types)
        if not byte_count:
            return None
        if isinstance(size, types.PhotoCachedSize):
            file = bytes(size.bytes)
        else:
            file = types.InputPhotoFileLocation(id=photo.id, access_hash=photo.access_hash,
                                                file_reference=photo.file_reference, thumb_size=size.type)
        return ({'media_id': str(photo.id), 'kind': 'image', 'extension': '.jpg', 'size': byte_count,
                 'width': size.w, 'height': size.h, 'name': ''}, file, photo.dc_id)
    if not isinstance(media, types.MessageMediaDocument) or not isinstance(media.document, types.Document):
        return None
    document = media.document
    file = message.file
    if not file or not document.size:
        return None
    name = re.split(r'[/\\]', file.name or '')[-1]
    extension = (file.ext or '').removeprefix('.').lower()
    named_extension = name.rpartition('.')[2].lower()
    if document.mime_type.startswith('video/'):
        kind, allowed = 'video', VIDEO_EXTENSIONS
    elif document.mime_type.startswith('image/'):
        kind, allowed = 'image', IMAGE_EXTENSIONS
    elif document.mime_type == 'application/octet-stream' and named_extension in VIDEO_EXTENSIONS | IMAGE_EXTENSIONS:
        kind = 'video' if named_extension in VIDEO_EXTENSIONS else 'image'
        allowed = VIDEO_EXTENSIONS | IMAGE_EXTENSIONS
    else:
        return None
    if extension not in allowed:
        extension = named_extension
    if extension not in allowed:
        return None
    return ({'media_id': str(document.id), 'kind': kind, 'extension': '.' + extension, 'size': document.size,
             'width': file.width or 0, 'height': file.height or 0, 'name': name}, document, document.dc_id)


def _sent_at(message):
    date = getattr(message, 'date', None)
    if not isinstance(date, datetime):
        return None
    if date.tzinfo is None:
        date = date.replace(tzinfo=timezone.utc)
    return date.timestamp()


def _media_thumbnail(media, types):
    is_photo = isinstance(media, types.MessageMediaPhoto)
    sizes = media.photo.sizes if is_photo else (getattr(media.document, 'thumbs', None) or [])
    full = max(sizes, key=lambda size: _photo_size(size, types), default=None) if is_photo else None
    stripped_type = getattr(types, 'PhotoStrippedSize', ())
    candidates = []
    for size in sizes:
        if full is not None and getattr(size, 'type', None) == full.type:
            continue
        if not is_photo and isinstance(size, types.PhotoSizeProgressive):
            # Telethon's document downloader expects a scalar size.
            continue
        if isinstance(size, stripped_type):
            byte_count = len(size.bytes)
        else:
            byte_count = _photo_size(size, types)
            if max(getattr(size, 'w', 0), getattr(size, 'h', 0)) > _PREVIEW_DIMENSION:
                continue
        if getattr(size, 'type', '') and 0 < byte_count <= _PREVIEW_BYTES:
            candidates.append((byte_count, size.type))
    return max(candidates, default=(0, None))[1]


class TelegramTransport:
    def __init__(self, vault, proxy_callable):
        self._vault = vault
        self._proxy = proxy_callable
        self._state_lock = Lock()
        self._thread_lock = Lock()
        self._credentials = None
        self._pending = None
        self._code_delivery = None
        self._code_resend_at = None
        self._password_required = False
        self._qr_state = {'state': 'idle'}
        self._qr_attempt = None
        self._qr_tasks = set()
        self._loop = self._thread = self._lock = None
        self._closed = False
        self._sdk = self._client = self._connected_proxy = None
        self._requests = set()
        self._streams = {}

    def _load_state(self):
        if self._credentials is None:
            try:
                credentials = self._vault.load()
                if not isinstance(credentials, dict):
                    raise ValueError
                self._credentials = credentials
            except Exception:
                raise TelegramTransportError('无法解密 Telegram 凭证') from None
        return self._credentials

    def _save_state(self, credentials):
        with self._state_lock:
            try:
                self._vault.save(credentials)
            except Exception:
                raise TelegramTransportError('无法加密保存 Telegram 凭证') from None
            self._credentials = credentials

    def status(self):
        """Report local session state without connecting to Telegram."""
        with self._state_lock:
            state = self._load_state()
            return {'configured': bool(state.get('api_id') and state.get('api_hash')),
                    'authorized': bool(state.get('session')), 'code_sent': self._pending is not None,
                    'password_required': not state.get('session') and
                    (self._password_required or self._qr_state['state'] == 'password'),
                    'code_delivery': self._code_delivery,
                    'code_can_resend': self._code_resend_at is not None,
                    'code_resend_at': self._code_resend_at}

    def _login_state(self, pending=None, password_required=False, delivery=None, resend_at=None):
        with self._state_lock:
            self._pending = pending
            self._password_required = password_required
            self._code_delivery = delivery
            self._code_resend_at = resend_at

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
            self._loop.run_until_complete(self._loop.shutdown_asyncgens())
        finally:
            self._loop.close()

    def _call(self, operation, *args, serialize=True):
        with self._thread_lock:
            if self._closed:
                raise TelegramTransportError('Telegram 连接已关闭')
            if self._loop is None:
                self._loop = asyncio.new_event_loop()
                self._thread = Thread(target=self._run_loop, name='Telegram transport', daemon=True)
                self._thread.start()
            future = asyncio.run_coroutine_threadsafe(self._request(operation, args, serialize), self._loop)
        try:
            return future.result()
        except CancelledError:
            raise TelegramTransportError('Telegram 连接已关闭', code='closed') from None

    async def _request(self, operation, args, serialize):
        if self._lock is None:
            self._lock = asyncio.Lock()
        task = asyncio.current_task()
        self._requests.add(task)
        try:
            if serialize:
                return await self._serial(operation, *args)
            return await operation(*args)
        finally:
            self._requests.discard(task)

    async def _serial(self, operation, *args):
        async with self._lock:
            try:
                async with asyncio.timeout(_OPERATION_TIMEOUT):
                    return await operation(*args)
            except (TelegramTransportError, StopAsyncIteration):
                raise
            except TimeoutError:
                raise TelegramTransportError('Telegram 请求超时，请检查网络或代理', code='timeout') from None
            except Exception as error:
                if self._is_error(error, 'FloodWaitError', 'FloodPremiumWaitError', 'FloodTestPhoneWaitError'):
                    seconds = max(0, int(getattr(error, 'seconds', 0)))
                    raise TelegramTransportError(f'Telegram 请求过于频繁，请等待 {seconds} 秒',
                                                 code='flood_wait', seconds=seconds) from None
                for names, code, message in (
                    (('ApiIdInvalidError',), 'api_credentials', 'Telegram API ID 或 API Hash 无效'),
                    (('ApiIdPublishedFloodError',), 'api_credentials', 'Telegram 已限制此 API ID，请检查应用凭证'),
                    (('PhoneNumberInvalidError', 'PhoneNumberBannedError'), 'phone', 'Telegram 无法向此号码发送验证码'),
                    (('PhoneNumberFloodError', 'PhonePasswordFloodError'), 'phone_flood', '验证码请求次数过多，请稍后再试'),
                    (('SendCodeUnavailableError', 'SmsCodeCreateFailedError'), 'code_unavailable',
                     'Telegram 暂未提供可用的验证码投递方式，请检查已登录设备里的 Telegram 服务通知，稍后再试'),
                    (('PhoneCodeInvalidError', 'PhoneCodeEmptyError'), 'code', 'Telegram 验证码不正确'),
                    (('PhoneCodeExpiredError', 'PhoneCodeHashEmptyError'), 'code_expired', 'Telegram 验证码已过期，请重新发送'),
                    (('PasswordHashInvalidError',), 'password', 'Telegram 两步验证密码不正确'),
                    (('PhoneNumberUnoccupiedError',), 'no_account', '此号码没有 Telegram 账号'),
                    (('ChannelPrivateError', 'ChannelInvalidError', 'UsernameInvalidError', 'UsernameNotOccupiedError'),
                     'peer_unavailable', '此 Telegram 频道不存在或当前账号无权访问'),
                    (_REF_ERRORS, 'file_reference', 'Telegram 媒体引用已过期，请重试'),
                ):
                    if self._is_error(error, *names):
                        raise TelegramTransportError(message, code=code) from None
                if self._is_error(error, *_AUTH_ERRORS):
                    await self._forget_session()
                    raise TelegramTransportError('Telegram 会话已失效，请重新登录', code='unauthorized') from None
                raise TelegramTransportError('Telegram 请求失败，请检查网络、代理或账号权限') from None

    def _is_error(self, error, *names):
        return self._sdk is not None and isinstance(error, tuple(
            cls for name in names if (cls := getattr(self._sdk.errors, name, None)) is not None))

    def configure(self, api_id, api_hash):
        if not re.fullmatch(r'[0-9]{1,10}', str(api_id)) or not 0 < int(api_id) < 2**31:
            raise TelegramTransportError('Telegram API ID 必须为正整数')
        api_hash = api_hash.strip() if isinstance(api_hash, str) else ''
        if not re.fullmatch(r'[0-9a-fA-F]{32}', api_hash):
            raise TelegramTransportError('Telegram API Hash 必须为 32 位十六进制字符')
        return self._call(self._configure, int(api_id), api_hash.lower())

    async def _configure(self, api_id, api_hash):
        with self._state_lock:
            old = self._load_state()
        if (old.get('api_id'), old.get('api_hash')) != (api_id, api_hash):
            self._save_state({'api_id': api_id, 'api_hash': api_hash, 'session': ''})
            await self._cancel_qr()
            self._login_state()
            await self._disconnect()
        return self.status()

    def _new_client(self, session, options, *, receive_updates=False):
        self._sdk = self._sdk or _load_telethon()
        state = self._credentials
        return self._sdk.client(self._sdk.session(session), state['api_id'], state['api_hash'], proxy=options,
                                receive_updates=receive_updates, flood_sleep_threshold=0,
                                timeout=10, connection_retries=2, request_retries=2,
                                raise_last_call_error=True, base_logger=_LOGGER)

    async def _client_for_request(self):
        if not self.status()['configured']:
            raise TelegramTransportError('请先填写 Telegram API ID 和 API Hash')
        proxy = self._proxy()
        options = _proxy_options(proxy)
        if self._client is not None and proxy != self._connected_proxy:
            self._login_state()
            await self._disconnect()
        if self._client is None:
            self._client = self._new_client(self._credentials.get('session') or '', options)
            self._connected_proxy = proxy
        if not self._client.is_connected():
            try:
                await self._client.connect()
            except Exception:
                await self._disconnect()
                raise
        return self._client

    async def _disconnect(self):
        for stream in list(self._streams.values()):
            await self._close_stream(stream)
        self._streams.clear()
        client, self._client = self._client, None
        self._connected_proxy = None
        if client is not None:
            with suppress(Exception):
                await client.disconnect()

    async def _forget_session(self):
        try:
            if self._credentials.get('session'):
                self._save_state({**self._credentials, 'session': ''})
        finally:
            await self._cancel_qr()
            self._login_state()
            await self._disconnect()

    async def _get_me(self, client):
        try:
            return await client.get_me()
        except Exception as error:
            if not self._is_error(error, *_AUTH_ERRORS):
                raise
            return None

    async def _authorized_client(self, *, with_account=False):
        if not self.status()['authorized']:
            raise TelegramTransportError('请先登录 Telegram', code='unauthorized')
        client = await self._client_for_request()
        account = await self._get_me(client)
        if account is None:
            await self._forget_session()
            raise TelegramTransportError('Telegram 会话已失效，请重新登录', code='unauthorized')
        return (client, account) if with_account else client

    def send_code(self, phone):
        phone = re.sub(r'[ ()-]', '', phone.strip()) if isinstance(phone, str) else ''
        if not re.fullmatch(r'\+[1-9][0-9]{6,14}', phone):
            raise TelegramTransportError('请输入带国家区号的 Telegram 手机号')
        return self._call(self._send_code, phone)

    async def _send_code(self, phone):
        await self._cancel_qr()
        client = await self._client_for_request()
        if self.status()['authorized']:
            if await self._get_me(client) is not None:
                return self.status()
            await self._forget_session()
            client = await self._client_for_request()
        if self._pending:
            if self._pending[0] == phone:
                return self.status()
            await self._reset_code()
            client = await self._client_for_request()
        result = await client.send_code_request(phone)
        return self._record_code(phone, result)

    def _record_code(self, phone, result):
        code_hash = getattr(result, 'phone_code_hash', None)
        code_type = getattr(result, 'type', None)
        if type(result).__name__ == 'SentCodePaymentRequired':
            self._login_state(self._pending, delivery=self._code_delivery)
            raise TelegramTransportError('Telegram 要求通过官方客户端完成验证', code='official_client_required')
        if not code_hash or code_type is None:
            raise TelegramTransportError('Telegram 未返回可用的验证码，请稍后再试', code='code_unavailable')
        type_name = type(code_type).__name__.removeprefix('SentCodeType')
        delivery = _DELIVERY_TYPES.get(type_name, 'unknown')
        resend_at = None
        if getattr(result, 'next_type', None) is not None:
            timeout = max(0, int(getattr(result, 'timeout', 0) or 0))
            resend_at = int((time() + timeout) * 1000)
        self._login_state((phone, code_hash), delivery=delivery, resend_at=resend_at)
        return self.status()

    def resend_code(self):
        return self._call(self._resend_code)

    async def _resend_code(self):
        if not self._pending or self._password_required or self._code_resend_at is None:
            raise TelegramTransportError('Telegram 暂未提供重新发送选项')
        if time() * 1000 < self._code_resend_at:
            raise TelegramTransportError('请等待 Telegram 的验证码倒计时结束')
        client = await self._client_for_request()
        if not self._pending:
            raise TelegramTransportError('代理已变更，请重新发送 Telegram 验证码')
        phone, code_hash = self._pending
        try:
            result = await client(self._sdk.functions.auth.ResendCodeRequest(phone[1:], code_hash))
        except Exception as error:
            if self._is_error(error, 'PhoneCodeExpiredError', 'PhoneCodeHashEmptyError'):
                await self._reset_code()
            elif self._is_error(error, 'SendCodeUnavailableError'):
                self._login_state(self._pending, delivery=self._code_delivery)
            elif self._is_error(error, 'FloodWaitError', 'FloodTestPhoneWaitError'):
                deadline = int((time() + max(0, error.seconds)) * 1000)
                self._login_state(self._pending, delivery=self._code_delivery, resend_at=deadline)
            raise
        return self._record_code(phone, result)

    def reset_code(self):
        return self._call(self._reset_code)

    async def _reset_code(self):
        if self._pending:
            self._login_state()
            await self._disconnect()
        return self.status()

    def qr_start(self):
        return self._call(self._qr_start)

    def qr_status(self):
        return self._call(self._qr_status, serialize=False)

    def qr_cancel(self):
        return self._call(self._cancel_qr, serialize=False)

    def _qr_snapshot(self):
        if self.status()['authorized']:
            return {'state': 'authorized'}
        return self._qr_state.copy()

    def _set_qr_state(self, state, **details):
        with self._state_lock:
            self._qr_state = {'state': state, **details}

    def _qr_is_current(self, attempt):
        if self._qr_attempt is not attempt or self._closed:
            return False
        with suppress(Exception):
            return attempt.proxy == self._proxy()
        return False

    async def _qr_status(self):
        attempt = self._qr_attempt
        if attempt is not None and not self._qr_is_current(attempt):
            await self._cancel_qr()
        return self._qr_snapshot()

    async def _qr_start(self):
        status = self.status()
        if not status['configured']:
            raise TelegramTransportError('请先填写 Telegram API ID 和 API Hash')
        await self._cancel_qr()
        if status['authorized']:
            client = await self._client_for_request()
            if await self._get_me(client) is not None:
                return self._qr_snapshot()
            await self._forget_session()
        self._login_state()
        await self._disconnect()
        proxy = self._proxy()
        client = self._new_client('', _proxy_options(proxy), receive_updates=True)
        attempt = _QRLoginAttempt(client, proxy, self._loop.create_future())
        self._qr_attempt = attempt
        attempt.task = asyncio.create_task(self._listen_for_qr(attempt))
        self._qr_tasks.add(attempt.task)
        attempt.task.add_done_callback(self._qr_tasks.discard)
        try:
            result = await attempt.ready
        except asyncio.CancelledError:
            if self._qr_attempt is attempt:
                await self._cancel_qr()
            raise
        if not self._qr_is_current(attempt):
            return await self._qr_status()
        return result

    async def _listen_for_qr(self, attempt):
        client = attempt.client
        try:
            await client.connect()
            login = await client.qr_login()
            if not self._qr_is_current(attempt):
                return
            self._set_qr_state('waiting', expires_at=int(login.expires.timestamp() * 1000))
            attempt.ready.set_result({**self._qr_state, 'url': login.url})
            # Setting the future schedules the caller. Telethon installs its
            # UpdateLoginToken handler before wait() first yields to that caller.
            await login.wait()
            if self._qr_is_current(attempt):
                self._persist_session(client)
                self._set_qr_state('authorized')
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if self._qr_is_current(attempt):
                if not attempt.ready.done():
                    self._set_qr_state('failed', error='无法生成 Telegram 登录二维码')
                    attempt.ready.set_exception(error)
                elif isinstance(error, TimeoutError) or self._is_error(error, 'AuthTokenExpiredError'):
                    self._set_qr_state('expired')
                elif self._is_error(error, 'SessionPasswordNeededError'):
                    self._set_qr_state('password')
                else:
                    self._set_qr_state('failed', error='Telegram 扫码登录失败，请重新获取二维码')
        finally:
            if not self._qr_is_current(attempt) or self._qr_state['state'] != 'password':
                if self._qr_attempt is attempt:
                    if not self._qr_is_current(attempt):
                        self._set_qr_state('idle')
                    self._qr_attempt = None
                if not attempt.ready.done():
                    attempt.ready.set_result(self._qr_snapshot())
                await self._disconnect_qr(attempt)

    async def _disconnect_qr(self, attempt):
        client, attempt.client = attempt.client, None
        if client is not None:
            with suppress(Exception):
                async with asyncio.timeout(_CLOSE_TIMEOUT):
                    await client.disconnect()

    async def _cancel_qr(self):
        attempt, self._qr_attempt = self._qr_attempt, None
        self._set_qr_state('idle')
        if attempt is not None:
            if not attempt.ready.done():
                attempt.ready.set_result(self._qr_snapshot())
            if not attempt.task.done():
                attempt.task.cancel()
                await asyncio.gather(attempt.task, return_exceptions=True)
            await self._disconnect_qr(attempt)
        return self._qr_snapshot()

    async def _sign_in_qr(self, password):
        attempt = self._qr_attempt
        if not self._qr_is_current(attempt):
            await self._cancel_qr()
            raise TelegramTransportError('Telegram 二维码登录已取消，请重新获取二维码', code='qr_inactive')
        if not password:
            return self.status()
        client = attempt.client
        try:
            await client.sign_in(password=password)
        finally:
            if not self._qr_is_current(attempt):
                if self._qr_attempt is attempt:
                    await self._cancel_qr()
                raise TelegramTransportError('Telegram 二维码登录已取消，请重新获取二维码',
                                             code='qr_inactive') from None
        try:
            self._persist_session(client)
            self._set_qr_state('authorized')
        except TelegramTransportError:
            self._set_qr_state('failed', error='无法加密保存 Telegram 会话，请重新扫码登录')
            raise
        finally:
            self._qr_attempt = None
            await self._disconnect_qr(attempt)
        return self.status()

    def _persist_session(self, client):
        session = client.session.save()
        if not session:
            raise TelegramTransportError('Telegram 登录成功，但未获得可保存的会话')
        self._save_state({**self._credentials, 'session': session})

    def sign_in(self, code='', password=''):
        if not isinstance(code, str) or not isinstance(password, str):
            raise TelegramTransportError('请输入有效的 Telegram 验证码或两步验证密码')
        return self._call(self._sign_in, code.strip(), password)

    async def _sign_in(self, code, password):
        if self.status()['authorized']:
            return self.status()
        if self._qr_state['state'] == 'password':
            return await self._sign_in_qr(password)
        if not self._pending:
            raise TelegramTransportError('请先发送 Telegram 验证码')
        client = await self._client_for_request()
        if not self._pending:
            raise TelegramTransportError('代理已变更，请重新发送 Telegram 验证码')
        phone, phone_code_hash = self._pending
        try:
            if self._password_required:
                if not password:
                    return self.status()
                await client.sign_in(password=password)
            elif self._code_delivery in ('email_setup', 'official_sms'):
                raise TelegramTransportError('请先在 Telegram 官方客户端完成所需验证')
            elif self._code_delivery == 'email' and re.fullmatch(r'[0-9]{3,12}', code):
                result = await client(self._sdk.functions.auth.SignInRequest(
                    phone[1:], phone_code_hash, email_verification=self._sdk.types.EmailVerificationCode(code)))
                if not isinstance(result, self._sdk.types.auth.Authorization):
                    raise TelegramTransportError('请先在 Telegram 官方客户端完成账号注册')
            elif re.fullmatch(r'[0-9]{3,20}', code) or (self._code_delivery == 'sms_text' and code.isprintable() and 0 < len(code) <= 80):
                await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
            else:
                raise TelegramTransportError('请输入收到的 Telegram 验证码')
        except Exception as error:
            if self._is_error(error, 'SessionPasswordNeededError'):
                self._login_state(self._pending, password_required=True)
                return self.status()
            if self._is_error(error, 'PhoneCodeExpiredError', 'PhoneCodeHashEmptyError'):
                await self._reset_code()
            raise
        self._persist_session(client)
        self._login_state()
        return self.status()

    def logout(self):
        return self._call(self._logout)

    async def _logout(self):
        await self._cancel_qr()
        if self.status()['authorized']:
            client = await self._client_for_request()
            if await self._get_me(client) is not None and not await client.log_out():
                raise TelegramTransportError('Telegram 退出登录失败，请稍后重试')
        await self._forget_session()
        return self.status()

    def channels(self):
        return self._call(self._channels)

    def _peer_id(self, peer):
        return self._sdk.utils.get_peer_id(peer)

    async def _channels(self):
        client = await self._authorized_client()
        channels = []
        async for dialog in client.iter_dialogs():
            peer = dialog.entity
            if isinstance(peer, self._sdk.types.Channel) and not getattr(peer, 'noforwards', False):
                channels.append({'id': str(self._peer_id(peer)), 'title': peer.title})
        return channels

    async def _resolve_peer(self, client, source, url=''):
        try:
            peer = await client.get_entity(source)
        except Exception as error:
            if not (isinstance(error, (ValueError, TypeError)) or (isinstance(source, int) and
                    self._is_error(error, 'ChannelInvalidError', 'ChannelPrivateError'))):
                raise
            peer = None
        if peer is None and isinstance(source, int):
            # StringSession does not retain private channel access hashes across restarts.
            async for dialog in client.iter_dialogs():
                if isinstance(dialog.entity, self._sdk.types.Channel) and self._peer_id(dialog.entity) == source:
                    peer = dialog.entity
                    break
            if peer is None and url:
                username, _ = _message_link(url)
                if isinstance(username, str):
                    with suppress(ValueError):
                        peer = await client.get_entity(username)
        if not isinstance(peer, self._sdk.types.Channel) or (isinstance(source, int) and self._peer_id(peer) != source):
            raise TelegramTransportError('此链接对应的 Telegram 频道当前不可访问', code='peer_unavailable')
        return peer

    @staticmethod
    def _url(peer, message_id):
        username = getattr(peer, 'username', None)
        path = username if username and re.fullmatch(_USERNAME, username) else f'c/{peer.id}'
        return f'https://t.me/{path}/{message_id}'

    def scan(self, mode, source, limit, media_mode, cancelled=None, *, start_at=None, end_at=None, on_item=None,
             incremental=None):
        """Read newest first, optionally publishing each result on the transport loop."""
        if (media_mode not in ('all', 'image', 'video') or type(limit) is not int
                or not (0 if incremental is not None else 1) <= limit <= 1000):
            raise TelegramTransportError('无效的 Telegram 扫描类型或数量')
        if mode == 'link' and incremental is None:
            peer, message_id = _message_link(source)
        elif mode == 'channel' and re.fullmatch(r'-[0-9]{1,17}', str(source)):
            peer, message_id = int(source), None
        elif mode == 'saved' and source == '':
            peer, message_id = None, None
        else:
            raise TelegramTransportError('请选择可访问的 Telegram 频道或填写消息链接')
        if any(value is not None and (type(value) not in (int, float)
                                      or (type(value) is float and not isfinite(value)))
               for value in (start_at, end_at)):
            raise TelegramTransportError('无效的 Telegram 扫描日期')
        if start_at is not None and end_at is not None and start_at >= end_at:
            raise TelegramTransportError('无效的 Telegram 扫描日期范围')
        return self._call(self._scan, peer, message_id, limit, media_mode, cancelled,
                          start_at, end_at, on_item, incremental, serialize=False)

    async def _scan_peer(self, source):
        client, account = await self._authorized_client(with_account=True)
        peer = account if source is None else await self._resolve_peer(client, source)
        if getattr(peer, 'noforwards', False):
            raise TelegramTransportError('Telegram 频道已开启内容保护', code='protected')
        return client, peer, account

    @staticmethod
    def _account_id(account):
        identifier = getattr(account, 'id', None)
        if type(identifier) is not int or not 0 < identifier < 2**63:
            raise TelegramTransportError('无法确认当前 Telegram 账号')
        return identifier

    def _ensure_scan_client(self, client):
        if self._client is not client or self._connected_proxy != self._proxy():
            raise TelegramTransportError('Telegram 连接已更换，请重新扫描', code='connection_changed')

    async def _scan_next(self, client, messages):
        self._ensure_scan_client(client)
        return await anext(messages, None)

    async def _scan_link(self, client, peer, message_id):
        self._ensure_scan_client(client)
        return await self._message_for_media(client, peer, message_id)

    def _forward_channel(self, message):
        header = getattr(message, 'fwd_from', None)
        origin = getattr(header, 'from_id', None)
        if origin is None or not isinstance(origin, self._sdk.types.PeerChannel):
            return None, None
        entity = getattr(getattr(message, 'forward', None), 'chat', None)
        if not isinstance(entity, self._sdk.types.Channel) or entity.id != origin.channel_id:
            entity = None
        return origin, entity

    def _forward_metadata(self, message):
        origin, entity = self._forward_channel(message)
        if origin is None:
            return {'forward_id': '', 'forward_title': '', 'forward_url': ''}
        post = getattr(message.fwd_from, 'channel_post', None)
        url = ''
        if type(post) is int and 0 < post < 2**31 and 0 < origin.channel_id < 10**16:
            url = self._url(entity, post) if entity else f'https://t.me/c/{origin.channel_id}/{post}'
        return {'forward_id': str(self._peer_id(origin)), 'forward_title': getattr(entity, 'title', '') or '',
                'forward_url': url}

    @staticmethod
    def _offset_date(end_at):
        # Telethon serializes dates as signed whole seconds. Zero means there is no bound.
        return (datetime.fromtimestamp(ceil(end_at), timezone.utc)
                if end_at is not None and 0 < end_at <= 2**31 - 1 else None)

    async def _scan(self, source, message_id, limit, media_mode, cancelled, start_at, end_at, on_item, incremental):
        if cancelled and cancelled():
            return []
        client, peer, account = await self._serial(self._scan_peer, source)
        saved = source is None
        peer_id = self._account_id(account) if saved else self._peer_id(peer)
        scope = f'saved:{peer_id}' if saved else str(peer_id)

        def read_item(message, sent_at):
            if message is None or getattr(message, 'chat_id', None) != peer_id or _protected(message):
                return None
            media = _media(message, self._sdk.types)
            if not media or (incremental is None and media_mode != 'all' and media[0]['kind'] != media_mode):
                return None
            body = getattr(message, 'message', '') or ''
            title = ((body.splitlines() or [''])[0].strip() or media[0]['name']
                     or ('图片' if media[0]['kind'] == 'image' else '视频'))
            return {**media[0], **self._forward_metadata(message),
                    'peer_id': scope, 'peer_title': '收藏夹' if saved else peer.title, 'message_id': str(message.id),
                    'group_id': str(getattr(message, 'grouped_id', None) or ''), 'body': body,
                    'title': title[:180], 'sent_at': sent_at, 'url': '' if saved else self._url(peer, message.id)}

        rows = []
        def publish(message, sent_at):
            if row := read_item(message, sent_at):
                rows.append(row)
                if on_item:
                    on_item(row)

        if incremental is not None:
            return await self._scan_incremental(client, peer, incremental, (self._account_id(account), scope),
                                                limit, start_at, end_at, cancelled, read_item, on_item)

        if message_id is not None:
            message = await self._serial(self._scan_link, client, peer, message_id)
            if not cancelled or not cancelled():
                publish(message, _sent_at(message))
            return rows

        ranged = start_at is not None or end_at is not None
        offset_date = self._offset_date(end_at)
        messages = aiter(client.iter_messages(peer, limit=None if ranged else limit, offset_date=offset_date))
        try:
            while not cancelled or not cancelled():
                message = await self._serial(self._scan_next, client, messages)
                if cancelled and cancelled():
                    break
                if message is None:
                    return rows
                sent_at = _sent_at(message)
                if not ranged or sent_at is not None:
                    if start_at is not None and sent_at < start_at:
                        return rows
                    if end_at is None or sent_at < end_at:
                        publish(message, sent_at)
                await asyncio.sleep(0)
            return []
        finally:
            await self._close_stream(messages)

    async def _scan_incremental(self, client, peer, journal, key, limit, start_at, end_at, cancelled, read_item,
                                on_item):
        def stopped():
            return cancelled is not None and cancelled()

        if on_item:
            for row in journal.items(key):
                if stopped():
                    return []
                on_item(row)
        cursor = journal.cursor(key)
        if cursor is None or cursor['upper_id'] is None:
            # Snapshot the upper message ID once. New arrivals wait for the next scan.
            latest = None
            if cursor is not None or end_at is None or end_at > 0:
                offset_date = self._offset_date(end_at) if cursor is None else None
                history = aiter(client.iter_messages(peer, limit=1, offset_date=offset_date))
                try:
                    latest = await self._serial(self._scan_next, client, history)
                finally:
                    await self._close_stream(history)
            if stopped():
                return []
            completed = cursor['completed'] if cursor else 0
            upper = max(completed, latest.id if latest is not None else 0)
            baseline_count = (limit or None) if cursor is None and start_at is None and end_at is None else None
            cursor = journal.begin(key, upper, baseline_count, start_at if cursor is None else None)

        upper, completed = cursor['upper_id'], cursor['completed']
        remaining, lower_date = cursor['remaining'], cursor['start_at']
        if upper > completed and remaining != 0:
            # Telethon takes the larger of max_id and offset_id in newest-first order.
            # Pass one exclusive bound so a resumed scan starts below its checkpoint.
            before = cursor['next_before'] or (upper + 1 if upper < 2**31 - 1 else 0)
            history = aiter(client.iter_messages(peer, limit=None, min_id=completed, offset_id=before))
            try:
                while not stopped() and remaining != 0:
                    message = await self._serial(self._scan_next, client, history)
                    if stopped() or message is None or message.id <= completed:
                        break
                    if message.id > upper:
                        continue
                    sent_at = _sent_at(message)
                    if lower_date is not None and sent_at is not None and sent_at < lower_date:
                        break
                    if remaining is not None:
                        remaining -= 1
                    item = read_item(message, sent_at)
                    journal.record(key, message.id, remaining, item)
                    if item is not None and on_item:
                        on_item(item)
                    await asyncio.sleep(0)
            finally:
                await self._close_stream(history)
        if stopped():
            return []
        journal.finish(key)
        return journal.items(key)

    def chunks(self, item, offset):
        if type(offset) is not int or offset < 0:
            raise TelegramTransportError('Telegram 下载偏移无效')
        stream = self._download(item, offset)
        try:
            while True:
                try:
                    yield self._call(anext, stream)
                except StopAsyncIteration:
                    return
        finally:
            with suppress(TelegramTransportError):
                self._call(stream.aclose)

    async def _message_for_media(self, client, peer, message_id, *, chat_id=None):
        if getattr(peer, 'noforwards', False):
            raise TelegramTransportError('Telegram 频道已开启内容保护', code='protected')
        message = await client.get_messages(peer, ids=message_id)
        expected_chat = self._peer_id(peer) if chat_id is None else chat_id
        if message is None or message.id != message_id or getattr(message, 'chat_id', None) != expected_chat:
            raise TelegramTransportError('Telegram 原消息已删除或不可访问', code='message_unavailable')
        if _protected(message):
            raise TelegramTransportError('Telegram 消息已开启内容保护', code='protected')
        return message

    async def _fresh_media(self, client, account, item):
        scope = str(item['peer_id'])
        if scope.startswith('saved:'):
            chat_id = self._account_id(account)
            if scope != f'saved:{chat_id}':
                raise TelegramTransportError('请使用收藏夹所属的 Telegram 账号', code='wrong_account')
            peer = account
        else:
            peer = await self._resolve_peer(client, int(scope), item.get('url', ''))
            chat_id = None
        message = await self._message_for_media(client, peer, int(item['message_id']), chat_id=chat_id)
        media = _media(message, self._sdk.types)
        identity_fields = ('media_id', 'size', 'kind', 'extension', 'width', 'height')
        if not media or any(media[0][field] != item[field] for field in identity_fields):
            raise TelegramTransportError('Telegram 原消息的媒体已变化，请重新扫描', code='media_changed')
        return message, media

    def preview(self, item, kind='media'):
        if kind not in ('media', 'avatar'):
            raise TelegramTransportError('无效的 Telegram 预览类型')
        return self._call(self._preview, item, kind)

    async def _preview(self, item, kind):
        client, account = await self._authorized_client(with_account=True)
        message, _ = await self._fresh_media(client, account, item)
        if kind == 'media':
            thumb = _media_thumbnail(message.media, self._sdk.types)
            data = await client.download_media(message, file=bytes, thumb=thumb) if thumb else None
        else:
            channel, entity = self._forward_channel(message)
            if channel is None or str(self._peer_id(channel)) != item.get('forward_id'):
                return None
            if entity is None:
                try:
                    entity = await self._resolve_peer(client, self._peer_id(channel))
                except TelegramTransportError as error:
                    if error.code != 'peer_unavailable':
                        raise
                    return None
            if getattr(entity, 'noforwards', False):
                return None
            data = await client.download_profile_photo(entity, file=bytes, download_big=False)
        return data if isinstance(data, bytes) and 0 < len(data) <= _PREVIEW_BYTES else None

    @staticmethod
    async def _close_stream(stream):
        closer = getattr(stream, 'close', None) or getattr(stream, 'aclose', None)
        if closer:
            with suppress(Exception):
                await closer()

    async def _download(self, item, offset):
        client, account = await self._authorized_client(with_account=True)
        expected = item['size']
        for retry in range(2):
            _, media = await self._fresh_media(client, account, item)
            _, file, dc_id = media
            if offset > expected:
                raise TelegramTransportError('Telegram 下载偏移超过文件长度')
            if offset == expected:
                return
            if isinstance(file, bytes):
                yield file[offset:]
                return
            stream = aiter(client.iter_download(file, offset=offset, file_size=expected, dc_id=dc_id))
            key = id(stream)
            self._streams[key] = stream
            try:
                while offset < expected:
                    if self._client is not client or self._connected_proxy != self._proxy():
                        raise TelegramTransportError('Telegram 连接已更换，请重试下载', code='connection_changed')
                    chunk = bytes(await anext(stream, b''))
                    if not chunk or offset + len(chunk) > expected:
                        raise TelegramTransportError('Telegram 媒体字节数与原消息不符', code='media_size')
                    offset += len(chunk)
                    yield chunk
                return
            except Exception as error:
                if retry or not self._is_error(error, *_REF_ERRORS):
                    raise
            finally:
                if self._streams.pop(key, None) is not None:
                    await self._close_stream(stream)

    def close(self):
        with self._thread_lock:
            if self._closed:
                return
            self._closed = True
            loop, thread = self._loop, self._thread
            if loop is None:
                return
            cleanup = asyncio.run_coroutine_threadsafe(self._shutdown(), loop)
        try:
            cleanup.result(timeout=_CLOSE_TIMEOUT)
        except (TimeoutError, CancelledError):
            return
        thread.join(timeout=_CLOSE_TIMEOUT)

    async def _shutdown(self):
        try:
            requests = list(self._requests)
            for request in requests:
                request.cancel()
            if requests:
                await asyncio.gather(*requests, return_exceptions=True)
            await self._cancel_qr()
            if self._qr_tasks:
                await asyncio.gather(*self._qr_tasks, return_exceptions=True)
            self._login_state()
            await self._disconnect()
        finally:
            self._loop.call_soon(self._loop.stop)
