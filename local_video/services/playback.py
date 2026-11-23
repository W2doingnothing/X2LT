# SPDX-License-Identifier: GPL-2.0-only
"""Ticketed loopback streaming for registered local files."""
from collections import OrderedDict
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import stat
import threading
import time
from urllib.parse import urlsplit

from ..archive import original_path
from ..local_import_files import selected_path


CHUNK_SIZE = 256 * 1024
IDLE_LIFETIME = 30 * 60
MAX_SESSIONS = 8
VIDEO_TYPES = {
    '.mp4':'video/mp4', '.m4v':'video/mp4', '.mov':'video/quicktime',
    '.webm':'video/webm', '.mkv':'video/x-matroska', '.avi':'video/x-msvideo',
    '.wmv':'video/x-ms-wmv', '.ts':'video/mp2t', '.mpeg':'video/mpeg', '.mpg':'video/mpeg',
}
TICKET_PATH = re.compile(r'/video/([a-f0-9]{64})')
BYTE_RANGE = re.compile(r'bytes=([0-9]{0,19})-([0-9]{0,19})', re.IGNORECASE)


def signature(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def parse_range(header, size):
    match = BYTE_RANGE.fullmatch(header.strip())
    if not match or not any(match.groups()):
        raise ValueError('Invalid range')
    first, last = match.groups()
    if first:
        start = int(first)
        end = min(int(last),size-1) if last else size-1
    else:
        start, end = max(0,size-int(last)), size-1
    if start > end:
        raise ValueError('Unsatisfiable range')
    return start,end


@dataclass
class PlaybackSession:
    path: Path
    identity: tuple[int,int,int,int]
    mime: str
    origin: str
    expires: float
    cancelled: threading.Event = field(default_factory=threading.Event)


class PlaybackServer(ThreadingHTTPServer):
    def __init__(self, service):
        self.service = service
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(('127.0.0.1',0), PlaybackHandler)
        self.host = f'127.0.0.1:{self.server_port}'

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            request.settimeout(5)
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request,client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        # Never include a bearer ticket or personal file path in server diagnostics.
        pass


class PlaybackHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        self.respond(send_file=True)

    def do_HEAD(self):
        self.respond()

    def do_OPTIONS(self):
        self.respond(preflight=True)

    def reply(self, status, length=0, origin=None, **headers):
        self.close_connection = True
        self.send_response_only(status)
        self.send_header('Date',self.date_time_string())
        if status != 204:
            self.send_header('Content-Length',str(length))
        self.send_header('Connection','close')
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Referrer-Policy','no-referrer')
        if origin:
            self.send_header('Access-Control-Allow-Origin',origin)
            self.send_header('Access-Control-Expose-Headers','Accept-Ranges, Content-Range')
            self.send_header('Vary','Origin')
        for key,value in headers.items():
            self.send_header(key.replace('_','-'),value)
        self.end_headers()

    def valid_origin(self, session):
        origin = self.headers.get_all('Origin')
        if origin is not None:
            return origin == [session.origin]
        referers = self.headers.get_all('Referer') or []
        if len(referers) != 1:
            return False
        try:
            referer = urlsplit(referers[0])
        except ValueError:
            return False
        # WebView2 normally strips the page path from a cross-origin Referer.
        return (f'{referer.scheme}://{referer.netloc}' == session.origin
                and referer.path in ('','/','/index.html'))

    def respond(self, send_file=False, preflight=False):
        if self.headers.get_all('Host') != [self.server.host]:
            self.reply(403)
            return
        match = TICKET_PATH.fullmatch(self.path)
        session = self.server.service.session(match[1]) if match else None
        if session is None:
            self.reply(404)
            return
        if not self.valid_origin(session):
            self.reply(403)
            return
        if preflight:
            method = self.headers.get('Access-Control-Request-Method')
            headers = self.headers.get('Access-Control-Request-Headers','')
            if method not in ('GET','HEAD') or headers.lower().strip() not in ('','range'):
                self.reply(403)
                return
            self.reply(204,origin=session.origin,Access_Control_Allow_Methods='GET, HEAD',
                       Access_Control_Allow_Headers='Range')
            return
        handle = self.open_file(session)
        if handle is None:
            self.reply(404,origin=session.origin)
            return
        with handle:
            self.serve_file(handle,session,send_file)

    @staticmethod
    def open_file(session):
        try:
            if session.cancelled.is_set() or selected_path(session.path) != session.path:
                return None
            current = session.path.stat()
            if not stat.S_ISREG(current.st_mode) or signature(current) != session.identity:
                return None
            handle = session.path.open('rb')
        except (OSError,ValueError):
            return None
        try:
            actual = os.fstat(handle.fileno())
            if stat.S_ISREG(actual.st_mode) and signature(actual) == session.identity:
                return handle
        except OSError:
            pass
        handle.close()
        return None

    def serve_file(self, handle, session, send_file):
        size = session.identity[2]
        ranges = self.headers.get_all('Range') or []
        headers = {'Accept_Ranges':'bytes','Content_Type':session.mime}
        if ranges:
            try:
                if len(ranges) != 1:
                    raise ValueError('Range must occur once')
                start,end = parse_range(ranges[0],size)
            except ValueError:
                self.reply(416,origin=session.origin,Accept_Ranges='bytes',Content_Range=f'bytes */{size}')
                return
            code = 206
            headers['Content_Range'] = f'bytes {start}-{end}/{size}'
        else:
            code,start,end = 200,0,size-1
        remaining = end-start+1
        self.reply(code,remaining,origin=session.origin,**headers)
        if not send_file:
            return
        handle.seek(start)
        while remaining and not session.cancelled.is_set():
            block = handle.read(min(CHUNK_SIZE,remaining))
            if not block:
                return
            self.wfile.write(block)
            remaining -= len(block)


class PlaybackService:
    def __init__(self, store, libraries, local_collection):
        self._store,self._libraries,self._local = store,libraries,local_collection
        self._lock = threading.Lock()
        self._server = None
        self._closed = False
        self._sessions = OrderedDict()

    def _local_path(self, p):
        entry = self._local.entry(p['id'])
        if entry['kind'] != 'video':
            raise ValueError('请选择视频')
        row = self._local.get_location(entry['id'],p.get('location_id'))
        if not row or row['directory']:
            raise ValueError('本地视频不可用，请重新定位')
        return Path(row['path']),entry['bytes'],tuple(json.loads(row['sig']))

    def _saved_path(self, p):
        rows = self._store.get_many([p['id']])
        if not rows or rows[0]['state'] != 'completed' or rows[0].get('media_type','video') != 'video':
            raise ValueError('只能播放已完成的视频')
        row = rows[0]
        root = Path(row['output'])
        with self._libraries.lock:
            configured = any(Path(item['path']) == root for item in self._libraries.items)
        if not configured:
            raise ValueError('视频所属媒体库不可用')
        if row.get('local_path'):
            # A previous organizer operation can have moved the file elsewhere.
            path = Path(row['local_path'])
        else:
            if selected_path(root) != root:
                raise ValueError('视频所属媒体库不可用')
            path = original_path(row)
        if path.suffix.lower() != '.mp4':
            raise ValueError('下载视频的文件类型已改变')
        return path,row['bytes'],None

    def issue(self, p, shell_url):
        shell = urlsplit(shell_url)
        if (shell.scheme != 'http' or shell.hostname not in ('127.0.0.1','localhost') or not shell.port
                or shell.path != '/index.html' or shell.username is not None):
            raise ValueError('仅支持从桌面媒体库播放')
        source = p['source']
        if source not in ('local','x') or (source == 'x' and 'location_id' in p):
            raise ValueError('视频来源无效')
        try:
            path,size,known = self._local_path(p) if source == 'local' else self._saved_path(p)
            if not path.is_absolute() or selected_path(path) != path:
                raise ValueError('视频路径已改变')
            mime = VIDEO_TYPES.get(path.suffix.lower())
            info = path.stat() if mime else None
        except OSError:
            raise ValueError('视频文件不可用') from None
        if (info is None or not stat.S_ISREG(info.st_mode) or info.st_size <= 0 or
                (size and info.st_size != size) or (known and signature(info) != known)):
            raise ValueError('视频文件已改变或不可用')
        ticket = secrets.token_hex(32)
        with self._lock:
            if self._closed:
                raise ValueError('窗口正在关闭')
            if self._server is None:
                self._server = PlaybackServer(self)
                worker = threading.Thread(target=self._server.serve_forever,kwargs={'poll_interval':.05},
                                          name='x2lt-playback',daemon=True)
                worker.start()
            while len(self._sessions) >= MAX_SESSIONS:
                self._sessions.popitem(last=False)[1].cancelled.set()
            self._sessions[ticket] = PlaybackSession(path,signature(info),mime,
                f'{shell.scheme}://{shell.netloc}',time.monotonic()+IDLE_LIFETIME)
            return {'url':f'http://{self._server.host}/video/{ticket}','token':ticket}

    def session(self, ticket):
        with self._lock:
            value = self._sessions.get(ticket)
            if value and value.expires <= time.monotonic():
                self._sessions.pop(ticket).cancelled.set()
                return None
            if value:
                value.expires = time.monotonic()+IDLE_LIFETIME
                self._sessions.move_to_end(ticket)
            return value

    def revoke(self, p):
        with self._lock:
            session = self._sessions.pop(p['token'],None)
            if session:
                session.cancelled.set()
        return True

    def close(self):
        with self._lock:
            self._closed = True
            for session in self._sessions.values():
                session.cancelled.set()
            self._sessions.clear()
            server,self._server = self._server,None
        if server is not None:
            server.shutdown()
            server.server_close()
