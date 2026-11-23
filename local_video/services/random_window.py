# SPDX-License-Identifier: GPL-2.0-only
"""Restrict a random playback window to its own queue and stream."""
from threading import Lock

from ..bridge_contracts import RANDOM_FEED_ACTIONS, validate_request


class RandomFeedBridge:
    def __init__(self, feed, playback, window, url, privacy, allowed_navigation):
        self._feed = feed
        self._playback = playback
        self._window = window
        self._url = url
        self._privacy = privacy
        self._allowed_navigation = allowed_navigation
        self._lock = Lock()
        self._token = None
        self._closed = False

    @property
    def closed(self):
        with self._lock:
            return self._closed

    def _revoke(self):
        if self._token:
            self._playback.revoke({'token': self._token})
            self._token = None

    def release(self):
        with self._lock:
            if self._closed:
                return False
            self._closed = True
            self._revoke()
            return True

    def close(self):
        if self.release():
            self._window.destroy()

    def show(self):
        self._window.restore()
        self._window.show()

    def call(self, action, payload=None):
        try:
            if not self._allowed_navigation(self._window.get_current_url(), self._url):
                raise ValueError('仅随机播放器可调用此接口')
            request = validate_request(action, payload, RANDOM_FEED_ACTIONS)
            if action == 'random_feed_close':
                self.close()
                return {'ok': True, 'data': True}
            with self._lock:
                if self._closed:
                    raise ValueError('播放器已关闭')
                return {'ok': True, 'data': self._dispatch(action, request)}
        except ValueError as exc:
            return {'ok': False, 'error': str(exc)}
        except Exception:
            return {'ok': False, 'error': '随机播放器操作失败'}

    def _dispatch(self, action, request):
        if action == 'random_feed_step':
            item = self._feed.step(request['direction'])
            if request['direction'] != 'current':
                self._revoke()
            return {**item, 'privacy': self._privacy}
        if action == 'random_feed_play':
            ticket = self._feed.play(request['revision'], lambda candidate: self._playback.issue(candidate, self._url))
            self._revoke()
            self._token = ticket['token']
            return ticket
        if request['token'] == self._token:
            self._revoke()
        return True
