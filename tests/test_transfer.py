from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import hashlib
import email.utils
import json
import re
import tempfile
import threading
import time
import unittest

from local_video.transfer import Downloader, Paused, TransferError

DATA = bytes(range(256)) * 4096


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def handle(self):
        try:
            super().handle()
        except (ConnectionError, OSError):
            pass  # The downloader deliberately closes unneeded probe bodies.

    def do_HEAD(self):
        self.send_response(200)
        self.send_header('Content-Length', str(len(DATA)))
        self.send_header('ETag', self.server.etag)
        self.send_header('Accept-Ranges', 'bytes')
        self.end_headers()

    def do_GET(self):
        requested = self.headers.get('Range', '')
        self.server.ranges.append(requested)
        if self.path == '/redirect':
            self.send_response(302)
            self.send_header('Location', 'http://127.0.0.2/private')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if self.path == '/416':
            self.send_response(416)
            self.send_header('Content-Range', f'bytes */{len(DATA)}')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        match = re.fullmatch(r'bytes=(\d+)-(\d*)', requested)
        start, end = (int(match[1]), int(match[2]) if match[2] else len(DATA) - 1) if match else (0, len(DATA) - 1)
        if self.path == '/expire' and requested != 'bytes=0-0':
            self.send_response(403)
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        if self.path in {'/ignore', '/ignore-after-probe'} and (self.path == '/ignore' or requested != 'bytes=0-0'):
            start, end = 0, len(DATA) - 1
            match = None
        body = (b'B' * len(DATA) if self.path == '/weak-date' else DATA)[start:end + 1]
        self.send_response(206 if match else 200)
        if match:
            reported = start + (1 if self.path == '/bad-range' else 0)
            self.send_header('Content-Range', f'bytes {reported}-{end}/{len(DATA)}')
        self.send_header('Content-Length', str(len(body)))
        if self.path not in {'/no-validator', '/weak-date'}:
            self.send_header('ETag', self.server.etag)
        if self.path == '/weak-date':
            self.send_header('Last-Modified', email.utils.formatdate(usegmt=True))
        self.send_header('Content-Type', 'video/mp4')
        self.end_headers()
        try:
            if self.path == '/short':
                self.wfile.write(body[:100])
                self.wfile.flush()
                self.close_connection = True
                return
            for pos in range(0, len(body), 16384):
                self.wfile.write(body[pos:pos + 16384])
                self.wfile.flush()
                if self.path == '/slow':
                    time.sleep(.004)
        except (ConnectionError, OSError):
            pass


class TransferTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.server.daemon_threads = True
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dest = Path(self.temp.name) / 'test.mp4'
        self.server.etag = '"v1"'
        self.server.ranges = []

    def downloader(self, **kw):
        def local_policy(url):
            if not url.startswith(self.base + '/'):
                raise ValueError('forbidden test redirect')
            return url
        return Downloader(url_policy=local_policy, retries=1, chunk_size=16384, **kw)

    def test_request_strategies_reduce_requests_without_changing_contents(self):
        from local_video.policy import request_bytes_for
        global DATA
        original = DATA
        try:
            DATA = original * 8
            counts=[]
            for strategy in ('conservative','balanced','throughput'):
                dest=self.dest.with_name(strategy+'.mp4')
                self.server.ranges=[]
                result=self.downloader(connections=1,request_bytes=request_bytes_for(strategy)).download(self.base+'/file',dest)
                counts.append(len(self.server.ranges))
                self.assertEqual(result['sha256'],hashlib.sha256(DATA).hexdigest())
                self.assertEqual(dest.read_bytes(),DATA)
            self.assertEqual(counts,[9,3,2])
            with self.assertRaises(ValueError):request_bytes_for('invalid')
        finally: DATA=original

    def test_large_request_pause_resume_keeps_verified_prefix(self):
        from local_video.policy import request_bytes_for
        global DATA
        original=DATA
        try:
            DATA=original*8
            cancel=threading.Event()
            def stop(done,total):
                if done>=1048576:cancel.set()
            with self.assertRaises(Paused):
                self.downloader(connections=1,request_bytes=request_bytes_for('balanced')).download(self.base+'/slow',self.dest,cancel,stop)
            self.server.ranges=[]
            self.downloader(connections=1,request_bytes=request_bytes_for('balanced')).download(self.base+'/slow',self.dest)
            self.assertEqual(self.dest.read_bytes(),DATA)
            self.assertTrue(any(int(re.match(r'bytes=(\d+)',r)[1])>=1048576 for r in self.server.ranges))
        finally:DATA=original

    def test_parallel_transfer_has_correct_hash_and_manifest(self):
        result = self.downloader(connections=4).download(self.base + '/file', self.dest)
        self.assertEqual(self.dest.read_bytes(), DATA)
        self.assertEqual(result['sha256'], hashlib.sha256(DATA).hexdigest())
        self.assertEqual(result['verification'], 'length+sha256')
        self.assertGreaterEqual(len(self.server.ranges), 4)

    def test_explicit_force_fetches_again_despite_valid_sidecar(self):
        self.downloader().download(self.base + '/file', self.dest)
        self.server.ranges.clear()
        result = self.downloader().download(self.base + '/file', self.dest, force=True)
        self.assertTrue(self.server.ranges)
        self.assertFalse(result['reused'])
        self.assertEqual(self.dest.read_bytes(), DATA)

    def test_pause_resume_preserves_progress_across_instances(self):
        cancel = threading.Event()
        def progress(done, total):
            if done >= 131072:
                cancel.set()
        with self.assertRaises(Paused):
            self.downloader(connections=1).download(self.base + '/slow', self.dest, cancel, progress)
        self.assertFalse(self.dest.exists())
        partial = self.dest.with_suffix('.mp4.parts') / '0.part'
        saved = partial.stat().st_size
        self.assertGreater(saved, 0)
        cancel.clear()
        self.server.ranges.clear()
        self.downloader(connections=1).download(self.base + '/slow', self.dest, cancel)
        self.assertIn(f'bytes={saved}-{len(DATA)-1}', self.server.ranges)
        self.assertEqual(self.dest.read_bytes(), DATA)

    def test_changed_etag_restarts_without_mixing(self):
        cancel = threading.Event()
        with self.assertRaises(Paused):
            self.downloader(connections=1).download(self.base + '/slow', self.dest, cancel, lambda n, t: cancel.set() if n > 50000 else None)
        self.server.etag = '"v2"'
        self.server.ranges.clear()
        self.downloader(connections=1).download(self.base + '/slow', self.dest)
        self.assertIn(f'bytes=0-{len(DATA)-1}', self.server.ranges)
        self.assertEqual(self.dest.read_bytes(), DATA)

    def test_ignored_ranges_fall_back_without_corruption(self):
        self.downloader(connections=4).download(self.base + '/ignore', self.dest)
        self.assertEqual(self.dest.read_bytes(), DATA)

    def test_ignored_ranges_after_probe_fall_back(self):
        self.downloader(connections=4).download(self.base + '/ignore-after-probe', self.dest)
        self.assertEqual(self.dest.read_bytes(), DATA)

    def test_expired_link_after_probe_keeps_http_status(self):
        with self.assertRaisesRegex(TransferError, 'HTTP 403'):
            self.downloader().download(self.base + '/expire', self.dest)

    def test_recent_last_modified_is_not_a_strong_range_validator(self):
        self.downloader(connections=4).download(self.base + '/weak-date', self.dest)
        self.assertEqual(self.dest.read_bytes(), b'B' * len(DATA))
        self.assertEqual(self.server.ranges, ['bytes=0-0', ''])

    def test_bad_range_does_not_create_final_file(self):
        with self.assertRaises(TransferError):
            self.downloader().download(self.base + '/bad-range', self.dest)
        self.assertFalse(self.dest.exists())

    def test_truncated_body_not_promoted(self):
        with self.assertRaises(TransferError):
            self.downloader().download(self.base + '/short', self.dest)
        self.assertFalse(self.dest.exists())

    def test_416_does_not_promote_incomplete_file(self):
        with self.assertRaises(TransferError):
            self.downloader().download(self.base + '/416', self.dest)
        self.assertFalse(self.dest.exists())

    def test_size_limit_rejects_before_creating_final(self):
        with self.assertRaises(TransferError):
            self.downloader(max_bytes=10).download(self.base + '/file', self.dest)
        self.assertFalse(self.dest.exists())

    def test_small_request_blocks_merge_correctly(self):
        self.downloader(connections=2, request_bytes=131072).download(self.base + '/file', self.dest)
        self.assertEqual(self.dest.read_bytes(), DATA)
        self.assertGreaterEqual(len(self.server.ranges), 9)
        for value in self.server.ranges:
            match = re.fullmatch(r'bytes=(\d+)-(\d+)', value)
            self.assertLessEqual(int(match[2]) - int(match[1]) + 1, 131072)

    def test_tampered_partial_is_restarted(self):
        cancel = threading.Event()
        with self.assertRaises(Paused):
            self.downloader(connections=1).download(self.base + '/slow', self.dest, cancel, lambda n, t: cancel.set() if n > 50000 else None)
        part = self.dest.with_suffix('.mp4.parts') / '0.part'
        part.write_bytes(b'x' * part.stat().st_size)
        self.server.ranges.clear()
        self.downloader(connections=1).download(self.base + '/slow', self.dest)
        self.assertIn(f'bytes=0-{len(DATA)-1}', self.server.ranges)
        self.assertEqual(self.dest.read_bytes(), DATA)

    def test_modified_completed_file_is_redownloaded(self):
        dl = self.downloader()
        dl.download(self.base + '/file', self.dest)
        self.dest.write_bytes(b'x' * len(DATA))
        dl.download(self.base + '/file', self.dest)
        self.assertEqual(self.dest.read_bytes(), DATA)

    def test_complete_file_is_reused_only_with_manifest(self):
        self.dest.write_bytes(b'broken')
        dl = self.downloader()
        dl.download(self.base + '/file', self.dest)
        self.server.ranges.clear()
        result = dl.download(self.base + '/file', self.dest)
        self.assertTrue(result['reused'])
        self.assertEqual(self.server.ranges, [])

    def test_redirect_is_checked_before_request(self):
        with self.assertRaises(ValueError):
            self.downloader().download(self.base + '/redirect', self.dest)


if __name__ == '__main__':
    unittest.main()
