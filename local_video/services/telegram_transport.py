# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 W2doingnothing
"""Read Telegram messages and stream their attachments through Telethon 1.x."""
import asyncio
from concurrent.futures import CancelledError
from contextlib import suppress
import logging
import re
from threading import Lock, Thread
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
        from telethon import TelegramClient, errors, types, utils
        from telethon.sessions import StringSession
    except ImportError:
        raise TelegramTransportError('当前运行环境缺少 Telegram 组件') from None
    return SimpleNamespace(client=TelegramClient, session=StringSession, errors=errors, types=types, utils=utils)


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


class TelegramTransport:
    def __init__(self, vault, proxy_callable):
        self._vault = vault
        self._proxy = proxy_callable
        self._state_lock = Lock()
        self._thread_lock = Lock()
        self._credentials = None
        self._pending = None
        self._password_required = False
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
                    'password_required': self._password_required}

    def _login_state(self, pending=None, password_required=False):
        with self._state_lock:
            self._pending = pending
            self._password_required = password_required

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
            self._loop.run_until_complete(self._loop.shutdown_asyncgens())
        finally:
            self._loop.close()

    def _call(self, operation, *args):
        with self._thread_lock:
            if self._closed:
                raise TelegramTransportError('Telegram 连接已关闭')
            if self._loop is None:
                self._loop = asyncio.new_event_loop()
                self._thread = Thread(target=self._run_loop, name='Telegram transport', daemon=True)
                self._thread.start()
            future = asyncio.run_coroutine_threadsafe(self._request(operation, *args), self._loop)
        try:
            return future.result()
        except CancelledError:
            raise TelegramTransportError('Telegram 连接已关闭', code='closed') from None

    async def _request(self, operation, *args):
        if self._lock is None:
            self._lock = asyncio.Lock()
        task = asyncio.current_task()
        self._requests.add(task)
        try:
            return await self._serial(operation, *args)
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
                    (('PhoneNumberInvalidError', 'PhoneNumberBannedError'), 'phone', 'Telegram 无法向此号码发送验证码'),
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
            self._login_state()
            await self._disconnect()
        return self.status()

    async def _client_for_request(self):
        if not self.status()['configured']:
            raise TelegramTransportError('请先填写 Telegram API ID 和 API Hash')
        proxy = self._proxy()
        options = _proxy_options(proxy)
        if self._client is not None and proxy != self._connected_proxy:
            self._login_state()
            await self._disconnect()
        if self._client is None:
            self._sdk = self._sdk or _load_telethon()
            state = self._credentials
            session = self._sdk.session(state.get('session') or '')
            self._client = self._sdk.client(session, state['api_id'], state['api_hash'], proxy=options,
                                            receive_updates=False, flood_sleep_threshold=0,
                                            timeout=10, connection_retries=2, request_retries=2,
                                            raise_last_call_error=True, base_logger=_LOGGER)
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
            self._login_state()
            await self._disconnect()

    async def _get_me(self, client):
        try:
            return await client.get_me()
        except Exception as error:
            if not self._is_error(error, *_AUTH_ERRORS):
                raise
            return None

    async def _authorized_client(self):
        if not self.status()['authorized']:
            raise TelegramTransportError('请先登录 Telegram', code='unauthorized')
        client = await self._client_for_request()
        if await self._get_me(client) is None:
            await self._forget_session()
            raise TelegramTransportError('Telegram 会话已失效，请重新登录', code='unauthorized')
        return client

    def send_code(self, phone):
        phone = re.sub(r'[ ()-]', '', phone.strip()) if isinstance(phone, str) else ''
        if not re.fullmatch(r'\+[1-9][0-9]{6,14}', phone):
            raise TelegramTransportError('请输入带国家区号的 Telegram 手机号')
        return self._call(self._send_code, phone)

    async def _send_code(self, phone):
        client = await self._client_for_request()
        if self.status()['authorized']:
            if await self._get_me(client) is not None:
                return self.status()
            await self._forget_session()
            client = await self._client_for_request()
        result = await client.send_code_request(phone)
        self._login_state((phone, result.phone_code_hash))
        return self.status()

    def sign_in(self, code='', password=''):
        if not isinstance(code, str) or not isinstance(password, str):
            raise TelegramTransportError('请输入有效的 Telegram 验证码或两步验证密码')
        return self._call(self._sign_in, code.strip(), password)

    async def _sign_in(self, code, password):
        if self.status()['authorized']:
            return self.status()
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
            elif re.fullmatch(r'[0-9]{3,12}', code):
                await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
            else:
                raise TelegramTransportError('请输入收到的 Telegram 验证码')
        except Exception as error:
            if self._is_error(error, 'SessionPasswordNeededError'):
                self._login_state(self._pending, password_required=True)
                return self.status()
            if self._is_error(error, 'PhoneCodeExpiredError', 'PhoneCodeHashEmptyError'):
                self._login_state()
            raise
        session = client.session.save()
        if not session:
            raise TelegramTransportError('Telegram 登录成功，但未获得可保存的会话')
        self._save_state({**self._credentials, 'session': session})
        self._login_state()
        return self.status()

    def logout(self):
        return self._call(self._logout)

    async def _logout(self):
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

    def scan(self, mode, source, limit, media_mode, cancelled=None):
        if media_mode not in ('all', 'image', 'video') or type(limit) is not int or not 1 <= limit <= 1000:
            raise TelegramTransportError('无效的 Telegram 扫描类型或数量')
        if mode == 'link':
            peer, message_id = _message_link(source)
        elif mode == 'channel' and re.fullmatch(r'-[0-9]{1,17}', str(source)):
            peer, message_id = int(source), None
        else:
            raise TelegramTransportError('请选择可访问的 Telegram 频道或填写消息链接')
        return self._call(self._scan, peer, message_id, limit, media_mode, cancelled)

    async def _scan(self, source, message_id, limit, media_mode, cancelled):
        client = await self._authorized_client()
        peer = await self._resolve_peer(client, source)
        if getattr(peer, 'noforwards', False):
            raise TelegramTransportError('Telegram 频道已开启内容保护', code='protected')
        peer_id = self._peer_id(peer)

        def record(message):
            if message is None or getattr(message, 'chat_id', None) != peer_id or _protected(message):
                return None
            media = _media(message, self._sdk.types)
            if not media or (media_mode != 'all' and media[0]['kind'] != media_mode):
                return None
            body = getattr(message, 'message', '') or ''
            return {**media[0], 'peer_id': str(peer_id), 'peer_title': peer.title, 'message_id': str(message.id),
                    'group_id': str(getattr(message, 'grouped_id', None) or ''), 'body': body,
                    'title': (body.splitlines() or [''])[0].strip()[:180] or peer.title,
                    'url': self._url(peer, message.id)}

        if message_id is None:
            rows = []
            async for message in client.iter_messages(peer, limit=limit):
                if cancelled and cancelled():
                    return []
                if row := record(message):
                    rows.append(row)
            return rows
        message = await client.get_messages(peer, ids=message_id)
        if message is None or getattr(message, 'chat_id', None) != peer_id or message.id != message_id:
            raise TelegramTransportError('Telegram 原消息已删除或不可访问', code='message_unavailable')
        if _protected(message):
            raise TelegramTransportError('Telegram 消息已开启内容保护', code='protected')
        group = getattr(message, 'grouped_id', None)
        if not group:
            row = record(message)
            return [row] if row else []
        # Telegram albums have up to ten items. Look on both sides of the linked item.
        neighbors = await client.get_messages(peer, ids=list(range(max(1, message_id - 9), min(2**31, message_id + 10))))
        return [row for member in neighbors if getattr(member, 'grouped_id', None) == group and (row := record(member))]

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

    async def _fresh_file(self, client, item):
        peer_id, message_id = int(item['peer_id']), int(item['message_id'])
        peer = await self._resolve_peer(client, peer_id, item.get('url', ''))
        if getattr(peer, 'noforwards', False):
            raise TelegramTransportError('Telegram 频道已开启内容保护', code='protected')
        message = await client.get_messages(peer, ids=message_id)
        if message is None or message.id != message_id or getattr(message, 'chat_id', None) != peer_id:
            raise TelegramTransportError('Telegram 原消息已删除或不可访问', code='message_unavailable')
        if _protected(message):
            raise TelegramTransportError('Telegram 消息已开启内容保护', code='protected')
        media = _media(message, self._sdk.types)
        identity_fields = ('media_id', 'size', 'kind', 'extension', 'width', 'height')
        if not media or any(media[0][field] != item[field] for field in identity_fields):
            raise TelegramTransportError('Telegram 原消息的媒体已变化，请重新扫描', code='media_changed')
        return media

    @staticmethod
    async def _close_stream(stream):
        closer = getattr(stream, 'close', None) or getattr(stream, 'aclose', None)
        if closer:
            with suppress(Exception):
                await closer()

    async def _download(self, item, offset):
        client = await self._authorized_client()
        expected = item['size']
        for retry in range(2):
            _, file, dc_id = await self._fresh_file(client, item)
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
            self._login_state()
            await self._disconnect()
        finally:
            self._loop.call_soon(self._loop.stop)
