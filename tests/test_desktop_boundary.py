from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from local_video.desktop import bind_bridge,allowed_navigation,successful_completion


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
