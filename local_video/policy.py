# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 W2doingnothing
"""Narrow URL boundaries for platform discovery and native video media."""
import re
from urllib.parse import urlsplit


def _split(url):
    if not isinstance(url, str) or any(ord(c) < 32 for c in url) or '\\' in url:
        raise ValueError('无效链接')
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.username or parts.password or parts.port not in (None, 443):
        raise ValueError('只允许无账号密码的 HTTPS 标准端口链接')
    return parts


def validate_media_url(url, media_type="video"):
    parts = _split(url)
    expected = 'pbs.twimg.com' if media_type == 'image' else 'video.twimg.com'
    if parts.hostname != expected:
        raise ValueError('媒体链接不是允许的官方 CDN')
    return url


def validate_source_url(url):
    parts = _split(url.strip())
    if parts.hostname not in {'x.com', 'www.x.com', 'twitter.com', 'www.twitter.com'}:
        raise ValueError('请输入 x.com 或 twitter.com 推文链接')
    match = re.fullmatch(r'/([A-Za-z0-9_]{1,30}|i/web)/status/(\d+)/?', parts.path)
    if not match:
        raise ValueError('请输入完整的推文链接，例如 https://x.com/name/status/123')
    return f'https://x.com/{match[1]}/status/{match[2]}'


def likes_url(username):
    username = username.strip().lstrip('@')
    if not re.fullmatch(r'[A-Za-z0-9_]{1,30}', username):
        raise ValueError('X 用户名格式错误')
    return f'https://x.com/{username}/likes'


def normalize_proxy(value):
    value = value.strip()
    if not value:
        return ''
    parts = urlsplit(value)
    if parts.scheme not in {'http', 'https', 'socks5', 'socks5h'} or not parts.hostname or not parts.port:
        raise ValueError('代理须为 http://主机:端口 或 socks5://主机:端口')
    # Resolve target DNS at the SOCKS proxy, avoiding a separate local DNS path.
    return 'socks5h://' + value[len('socks5://'):] if value.startswith('socks5://') else value


REQUEST_STRATEGIES = {'保守 · 1 MiB': 'conservative', '均衡 · 4 MiB': 'balanced', '吞吐 · 8 MiB': 'throughput'}


def request_bytes_for(strategy='conservative'):
    sizes = {'conservative': 1, 'balanced': 4, 'throughput': 8}
    if strategy not in sizes:
        raise ValueError('无效下载请求策略')
    return sizes[strategy] * 1024**2


def validate_image_url(url):
    return validate_media_url(url, 'image')
