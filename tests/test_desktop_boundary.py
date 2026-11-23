from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from local_video.desktop import bind_bridge,allowed_navigation,successful_completion
from local_video.services.random_window import RandomFeedBridge


class DesktopBoundaryTests(unittest.TestCase):
    def test_cancelled_navigation_keeps_existing_bridge_callbacks(self):
        original=Mock();completed=successful_completion(original)
        completed('sender',SimpleNamespace(IsSuccess=False));original.assert_not_called()
        args=SimpleNamespace(IsSuccess=True);completed('sender',args)
        original.assert_called_once_with('sender',args)

    def test_only_explicit_call_is_exposed_not_private_service_object(self):
        fake=SimpleNamespace(_js_api=None,_functions={})
        def expose(fn):fake._functions[fn.__name__]=fn
        fake.expose=expose
        vault=Mock();service=SimpleNamespace(_vault=vault,call=lambda:None)
        # Real service method has the public name used by the dispatcher.
        service.call.__name__='call'
        bind_bridge(fake,service)
        self.assertIsNone(fake._js_api)
        self.assertEqual(set(fake._functions),{'call'})
        from webview.util import js_bridge_call
        js_bridge_call(fake,'_vault.load',[],'synthetic')
        vault.load.assert_not_called()

    def test_navigation_restricted_to_exact_local_app_origin_and_document(self):
        origin='http://127.0.0.1:23456/index.html?desktop=1'
        self.assertTrue(allowed_navigation(origin+'#section',origin))
        for target in ('https://evil.test/index.html','http://127.0.0.1:23457/index.html','file:///C:/private.txt','http://127.0.0.1:23456/other.html','javascript:alert(1)'):
            self.assertFalse(allowed_navigation(target,origin))

    def test_random_player_only_releases_its_own_ticket_and_blocks_main_bridge(self):
        url = 'http://127.0.0.1:23456/index.html?desktop=1&view=random-feed'
        window = Mock()
        window.get_current_url.return_value = url
        feed, playback = Mock(), Mock()
        feed.step.return_value = {'revision': 'item'}
        feed.play.return_value = {'token': 'a'*64}
        bridge = RandomFeedBridge(feed, playback, window, url, True, allowed_navigation)
        self.assertFalse(bridge.call('snapshot')['ok'])
        self.assertFalse(bridge.call('video_open', {'source': 'local', 'id': 1})['ok'])
        self.assertTrue(bridge.call('random_feed_step', {'direction': 'current'})['data']['privacy'])
        self.assertTrue(bridge.call('random_feed_play', {'revision': 'item'})['ok'])
        self.assertTrue(bridge.call('video_close', {'token': 'b'*64})['ok'])
        playback.revoke.assert_not_called()
        window.get_current_url.return_value = url.split('&')[0]
        self.assertFalse(bridge.call('random_feed_play', {'revision': 'item'})['ok'])
        bridge.close()
        playback.revoke.assert_called_once_with({'token': 'a'*64})
        window.destroy.assert_called_once()
