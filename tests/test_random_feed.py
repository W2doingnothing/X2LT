# SPDX-License-Identifier: GPL-2.0-only
import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from local_video.desktop_service import DesktopService
from local_video.discovery import Video
from local_video.services.random_feed import RandomFeed


class RandomFeedTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.service = DesktopService(self.root/'data', self.root/'home')
        self.addCleanup(self.service._close)
        self.store, self.libraries = self.service._store, self.service._libraries
        self.first = self.libraries.get()
        self.second = self.add_library('第二媒体库')
        self.other = self.add_library('未选择')

    def add_library(self, name):
        path = self.root/name
        path.mkdir()
        return self.libraries.add(name, path)

    def add_local(self, name, library, kind='video'):
        path = self.root/(name + ('.mp4' if kind == 'video' else '.jpg'))
        payload = name.encode('utf8')
        path.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        entry = self.service._local.collection.add(
            dict(kind=kind, title=name, extension=path.suffix[1:], bytes=len(payload), pages=[]),
            digest, path, self.root, library['id'], False)
        return (entry, digest)

    def add_download(self, title, tweet, library, digest='', complete=True):
        job = self.store.enqueue(
            Video(tweet, '1', 'demo', 'https://video.twimg.com/sample.mp4',
                  f'https://x.com/demo/status/{tweet}', tweet_text=title),
            library['path'], 'best')
        if complete:
            self.store.set_state(job, 'completed', sha256=digest)
        return job

    def test_rounds_history_and_cross_library_deduplication(self):
        local, digest = self.add_local('同一视频', self.first)
        self.service._local.collection.set_link(local, self.second['id'], True)
        self.add_download('X 原版', '123', self.first, digest)
        self.add_download('X 另一画质', '123', self.second, 'b'*64)
        self.add_download('第二段视频', '456', self.second)
        self.add_local('第三段视频', self.first)
        self.add_local('排除图片', self.first, 'image')
        self.add_download('排除未完成', '789', self.first, complete=False)
        self.add_local('排除其他库', self.other)

        feed = RandomFeed(self.store, self.libraries, [self.first['id'], self.second['id']])
        initial = feed.step('current')
        self.assertFalse(initial['canPrevious'])
        first_round = [initial, feed.step('next'), feed.step('next')]
        self.assertTrue(all(item['count'] == 3 for item in first_round))
        self.assertEqual({item['title'] for item in first_round}, {'X 原版', '第二段视频', '第三段视频'})
        next_round = [feed.step('next'), feed.step('next'), feed.step('next')]
        self.assertEqual(len({item['title'] for item in next_round}), 3)
        self.assertNotEqual(next_round[0]['title'], first_round[-1]['title'])
        history = [feed.step('previous')['title'] for _ in range(3)]
        self.assertEqual(history, [next_round[1]['title'], next_round[0]['title'], first_round[-1]['title']])
        self.assertEqual(feed.step('next')['title'], next_round[0]['title'])
        with self.assertRaisesRegex(ValueError, '已切换'):
            feed.play(initial['revision'], Mock())

    def test_fallback_copy_and_saved_selection(self):
        local, digest = self.add_local('备用副本', self.second)
        self.add_download('文件已移动', '123', self.first, digest)
        opened = SimpleNamespace(closed=False, close=Mock(), show=Mock())
        create = Mock(return_value=opened)
        self.service._create_random_player = create
        selected = [self.first['id'], self.second['id']]
        self.assertEqual(self.service.call('random_feed_launch', {'library_ids': selected}),
                         {'ok': True, 'data': {'opened': True}})
        self.assertEqual(self.service._settings['random_video_libraries'], selected)
        feed = create.call_args.args[0]
        current = feed.step('current')

        def issue(payload):
            if payload['source'] == 'x':
                raise ValueError('已移动')
            self.assertEqual(payload['id'], local)
            return {'token': 'test'}

        self.assertEqual(current['count'], 1)
        self.assertEqual(feed.play(current['revision'], issue),
                         {'token': 'test', 'library': self.second['name']})
        self.assertEqual(self.service.call('random_feed_launch', {'library_ids': [self.other['id']]}),
                         {'ok': True, 'data': {'opened': False}})
        opened.show.assert_called_once()
        self.assertEqual(self.service._settings['random_video_libraries'], selected)

    def test_rejects_unknown_and_empty_libraries(self):
        create = Mock()
        self.service._create_random_player = create
        for ids in ([], ['missing'], [self.first['id']]):
            self.assertFalse(self.service.call('random_feed_launch', {'library_ids': ids})['ok'])
        create.assert_not_called()
        self.assertEqual(self.service._settings['random_video_libraries'], [])
