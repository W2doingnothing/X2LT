# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 W2doingnothing
"""In-memory official-site extraction using gallery-dl's structured messages."""
import copy
from dataclasses import dataclass
import re
import threading
from urllib.parse import urlsplit, urljoin

from .policy import likes_url, normalize_proxy, validate_media_url, validate_source_url
from .transfer import Paused

_LOCK = threading.RLock()


class DiscoveryError(RuntimeError):
    pass


def _validate_extractor_target(target):
    parts = urlsplit(target)
    try:
        allowed = parts.scheme == 'https' and not parts.username and not parts.password and parts.port in (None, 443) and parts.hostname in {'x.com', 'api.x.com', 'twitter.com', 'api.twitter.com', 'abs.twimg.com', 'pbs.twimg.com', 'video.twimg.com'}
    except ValueError:
        allowed = False
    if not allowed:
        raise DiscoveryError('提取器请求了未授权的站点')


def guard_session(session, cancel):
    def response_guard(response, *args, **kwargs):
        if cancel.is_set():
            response.close()
            raise Paused('已暂停读取点赞')
        try:
            _validate_extractor_target(response.url)
            if response.is_redirect:
                _validate_extractor_target(urljoin(response.url, response.headers['Location']))
        except Exception:
            response.close()
            raise
        return response
    session.hooks.setdefault('response', []).append(response_guard)


@dataclass(frozen=True)
class Video:
    tweet_id: str
    media_id: str
    author: str
    url: str
    source: str
    width: int = 0
    height: int = 0

    @property
    def relative_path(self):
        return f'{self.author}/{self.tweet_id}/{self.tweet_id}_{self.media_id}.mp4'


def _safe_name(value, fallback='unknown'):
    result = re.sub(r'[^A-Za-z0-9_-]', '_', str(value))[:80].strip('_')
    if result.upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(10)), *(f'LPT{i}' for i in range(10))}:
        result = '_' + result
    return result or fallback


def video_from_message(message):
    if len(message) != 3 or message[0] != 3:
        return None
    _, url, meta = message
    if meta.get('type') != 'video':
        return None
    validate_media_url(url)
    tweet_id = str(meta.get('tweet_id', ''))
    if not tweet_id.isdigit():
        raise DiscoveryError('提取器未返回有效推文 ID')
    user = meta.get('author') or meta.get('user') or {}
    author = _safe_name(user.get('name') if isinstance(user, dict) else user)
    media_id = _safe_name(meta.get('num', 1))
    return Video(tweet_id, media_id, author, url, f'https://x.com/{author}/status/{tweet_id}', int(meta.get('width') or 0), int(meta.get('height') or 0))


def select_variant(variants, quality):
    candidates = [v for v in variants if v.get('content_type') == 'video/mp4' or '.mp4' in v.get('url', '')]
    if not candidates:
        return None
    if quality in {'720p', '480p'}:
        limit = int(quality[:-1])
        suitable = []
        for variant in candidates:
            match = re.search(r'/(\d+)x(\d+)/', variant.get('url', ''))
            # Use short edge so portrait video isn't needlessly reduced twice.
            if match and min(int(match[1]), int(match[2])) <= limit:
                suitable.append(variant)
        if suitable:
            candidates = suitable
        else:
            return min(candidates, key=lambda v: v.get('bitrate', 0))
    return max(candidates, key=lambda v: v.get('bitrate', 0))


def discover(url, credentials, proxy, cancel, limit=0, quality='best'):
    from gallery_dl import config, extractor
    if url.endswith('/likes'):
        parts = urlsplit(url)
        if parts.netloc != 'x.com' or parts.scheme != 'https':
            raise ValueError('无效的点赞时间线地址')
        url = likes_url(parts.path.strip('/').split('/')[0])
    else:
        url = validate_source_url(url)
    with _LOCK:
        config.clear()
        config.set(('cache',), 'file', ':memory:')
        config.set(('extractor',), 'proxy', normalize_proxy(proxy))
        config.set(('extractor',), 'proxy-env', False)
        config.set(('extractor',), 'timeout', 15)
        config.set(('extractor',), 'retries', 2)
        config.set(('extractor',), 'cookies-update', False)
        options = {'cookies': {k: credentials[k] for k in ('auth_token', 'ct0') if credentials.get(k)},
                   'conversations': False, 'cards': False, 'quoted': False, 'retweets': False,
                   'articles': False, 'text-tweets': False, 'videos': True, 'sleep-request': 1.0,
                   'write-pages': False}
        for key, value in options.items():
            config.set(('extractor', 'twitter'), key, value)
        instance = None
        disabled_before = None
        try:
            instance = extractor.find(url)
            if instance is None:
                raise DiscoveryError('提取器不支持该地址')
            if hasattr(instance, 'log'):
                disabled_before = instance.log.disabled
                instance.log.disabled = True  # Do not leak raw responses or cookie errors.
            original_request = getattr(instance, 'request', None)
            if original_request:
                def request(target, *args, **kwargs):
                    if cancel.is_set():
                        raise Paused('已暂停读取点赞')
                    _validate_extractor_target(target)
                    return original_request(target, *args, **kwargs)
                instance.request = request
                original_session_init = instance._init_session
                def session_init():
                    original_session_init()
                    guard_session(instance.session, cancel)
                instance._init_session = session_init
            if hasattr(instance, 'sleep'):
                def sleep(seconds, reason):
                    if cancel.wait(max(0, seconds)):
                        raise Paused('已暂停读取点赞')
                instance.sleep = sleep
            original_media = getattr(instance, '_extract_media', None)
            if original_media:
                def extract_media(tweet, entities, files):
                    entities = copy.deepcopy(entities)
                    for entity in entities:
                        if 'video_info' in entity:
                            chosen = select_variant(entity['video_info'].get('variants', []), quality)
                            if chosen:
                                entity['video_info']['variants'] = [chosen]
                                match = re.search(r'/(\d+)x(\d+)/', chosen['url'])
                                if match:
                                    entity['original_info'] = {'width': int(match[1]), 'height': int(match[2])}
                    return original_media(tweet, entities, files)
                instance._extract_media = extract_media
            seen = set()
            tweets = set()
            for message in instance:
                if cancel.is_set():
                    raise Paused('已暂停读取点赞')
                if len(message) == 3 and message[0] == 2:
                    tid = str(message[2].get('tweet_id', ''))
                    if tid and tid not in tweets:
                        if limit and len(tweets) >= limit:
                            break
                        tweets.add(tid)
                video = video_from_message(message)
                if video and (video.tweet_id, video.media_id) not in seen:
                    seen.add((video.tweet_id, video.media_id))
                    yield video
        except (Paused, DiscoveryError, ValueError):
            raise
        except Exception as exc:
            name = type(exc).__name__
            explanations = {'AuthRequired': 'X 登录状态已过期或无权访问，请重新导入 Cookie', 'AuthorizationError': 'X 登录校验失败，请更新 Cookie', 'HttpError': 'X 请求失败，请检查代理或稍后重试', 'NotFoundError': '推文不存在或无法访问'}
            raise DiscoveryError(explanations.get(name, f'读取失败（{name}），请检查代理、Cookie 或稍后重试')) from None
        finally:
            if instance is not None and getattr(instance, 'session', None) is not None:
                instance.session.cookies.clear()
                instance.session.close()
            if disabled_before is not None:
                instance.log.disabled = disabled_before
            config.clear()
