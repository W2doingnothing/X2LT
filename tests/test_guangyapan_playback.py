from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen
from urllib.error import HTTPError

from local_video.guangyapan_playback import GuangyapanPlayback, playback_url


class GuangyapanPlaybackTests(unittest.TestCase):
    def test_loopback_requires_matching_state_before_exchanging_code(self):
        with TemporaryDirectory() as data:
            with socket.socket() as reserve:
                reserve.bind(('127.0.0.1', 0))
                port = reserve.getsockname()[1]
            callback = f'http://127.0.0.1:{port}/x2lt/guangyapan'
            browser = Mock()
            playback = GuangyapanPlayback(Path(data), browser)
            self.addCleanup(playback.close)
            playback.vault = Mock()
            playback.tokens = Mock(return_value={'client': 'app-fixture', 'refresh_token': 'test-token'})
            playback.start('app-fixture', callback)
            params = parse_qs(urlsplit(browser.call_args.args[0]).query)
            self.assertEqual(params['code_challenge_method'], ['S256'])
            with self.assertRaises(HTTPError) as rejected:
                urlopen(callback + '?code=fake&state=wrong', timeout=3)
            self.assertEqual(rejected.exception.code, 400)
            rejected.exception.close()
            playback.tokens.assert_not_called()
            with urlopen(callback + '?code=valid&state=' + params['state'][0], timeout=3) as response:
                self.assertEqual(response.status, 200)
            self.assertTrue(playback.status()['connected'])
            self.assertEqual(playback.tokens.call_args.args[1]['code'], 'valid')

    def test_selects_default_version_and_reuses_vod_request_on_refresh(self):
        with TemporaryDirectory() as data:
            playback = GuangyapanPlayback(Path(data), Mock())
            playback.business = Mock(side_effect=[
                {'userId': 'account'},
                {'videoResource': [{'bizId': 'first'}, {'bizId': 'preferred', 'info': {'defaultResolution': True}}]},
                {'signedURL': 'https://play.guangyacdn.com/clip', 'requestId': 'request-1'},
                {'signedURL': 'https://play.guangyacdn.com/refreshed', 'requestId': 'request-1'},
            ])
            provider = Mock()
            first, previous = playback.video('123', 'account', provider)
            second, _ = playback.video('123', 'account', provider, previous)
            self.assertNotEqual(first, second)
            history = playback.business.call_args_list
            self.assertEqual(history[2].args[1], {'fileId': '123', 'bizId': 'preferred'})
            self.assertEqual(history[3].args[1], {'fileId': '123', 'bizId': 'preferred', 'requestId': 'request-1'})
            provider.list.assert_not_called()

    def test_developer_id_and_external_playback_urls_are_rejected(self):
        with TemporaryDirectory() as data:
            launch = Mock()
            playback = GuangyapanPlayback(Path(data), launch)
            with self.assertRaisesRegex(ValueError, '开发者'):
                playback.start('dvlp_fixture', 'http://127.0.0.1:17863/x2lt/guangyapan')
            launch.assert_not_called()
        for url in ('http://play.guangyacdn.com/file', 'https://api.guangyapan.com.evil.test/file',
                    'https://user@play.guangyacdn.com/file', 'http://127.0.0.1/'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                playback_url(url)
