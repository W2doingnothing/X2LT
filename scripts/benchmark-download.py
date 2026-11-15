"""Synthetic localhost benchmark. Never accesses X accounts or external media."""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from local_video.policy import request_bytes_for
from local_video.transfer import Downloader


def benchmark(size_mb=16, latency_ms=20):
    body = bytes(range(256)) * (size_mb * 4096)
    digest = hashlib.sha256(body).hexdigest()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def handle(self):
            try:
                super().handle()
            except (ConnectionError, OSError):
                pass  # Probe bodies may be deliberately closed by the client.

        def do_GET(self):
            self.server.requests += 1
            time.sleep(latency_ms / 1000)
            match = re.fullmatch(r'bytes=(\d+)-(\d+)', self.headers.get('Range', ''))
            if not match:
                self.send_error(400)
                return
            start, end = map(int, match.groups())
            self.send_response(206)
            self.send_header('Content-Range', f'bytes {start}-{end}/{len(body)}')
            self.send_header('Content-Length', str(end - start + 1))
            self.send_header('ETag', '"synthetic-v1"')
            self.send_header('Content-Type', 'video/mp4')
            self.end_headers()
            try:
                self.wfile.write(body[start:end + 1])
            except (ConnectionError, OSError):
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    server.requests = 0
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}/fixture'

    def local_policy(candidate):
        if candidate != url:
            raise ValueError('Benchmark only permits its own localhost fixture')
        return candidate

    results = []
    try:
        with tempfile.TemporaryDirectory() as tmp:
            for strategy in ('conservative', 'balanced', 'throughput'):
                server.requests = 0
                start = time.perf_counter()
                result = Downloader(connections=1, request_bytes=request_bytes_for(strategy), url_policy=local_policy).download(url, Path(tmp) / (strategy + '.mp4'))
                seconds = time.perf_counter() - start
                assert result['sha256'] == digest
                results.append(dict(strategy=strategy, requests=server.requests, seconds=round(seconds, 3), mib_per_second=round(size_mb / seconds, 2), sha256_verified=True))
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    return {'scope': 'synthetic localhost; one connection; not a real CDN speed prediction', 'size_mib': size_mb, 'latency_ms_per_request': latency_ms, 'results': results}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--size-mb', type=int, choices=range(8, 129), default=16)
    parser.add_argument('--latency-ms', type=int, choices=range(0, 1001), default=20)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.size_mb, args.latency_ms), indent=2))
