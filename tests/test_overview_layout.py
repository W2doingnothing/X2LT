import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from local_video.desktop_service import DesktopService


class OverviewTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name).resolve()
        self.service = self.reopen()
        self.addCleanup(lambda: self.service._close())

    def reopen(self):
        return DesktopService(self.path/'data',self.path/'home')

    def test_layout_is_validated_and_persists_in_display_order(self):
        layout = [
            {'id':'libraries','visible':True,'width':'full'},
            {'id':'stats','visible':False,'width':'half'},
            {'id':'recent','visible':True,'width':'half'},
        ]
        encoded = json.dumps(layout)
        self.assertTrue(self.service.call('settings',{'overview_layout':encoded})['ok'])
        duplicate = json.dumps([layout[0],layout[0],layout[2]])
        self.assertFalse(self.service.call('settings',{'overview_layout':duplicate})['ok'])
        self.assertTrue(self.service._close())
        self.service = self.reopen()
        self.assertEqual(json.loads(self.service._settings_service.public()['overview_layout']),layout)
        self.assertTrue(self.service.call('settings',{'overview_layout':''})['ok'])

    def test_hidden_panels_skip_the_corresponding_database_queries(self):
        resources = self.service._store.resources
        with patch.object(resources,'page',wraps=resources.page) as page, patch.object(
                resources,'summary',wraps=resources.summary) as summary:
            self.assertTrue(self.service.call('resource_overview',{'stats':True,'recent':False})['ok'])
            page.assert_not_called()
            summary.assert_called_once()
            summary.reset_mock()
            self.assertTrue(self.service.call('resource_overview',{'stats':False,'recent':True})['ok'])
            page.assert_called_once()
            summary.assert_not_called()
            page.reset_mock()
            self.assertTrue(self.service.call('resource_overview',{'stats':False,'recent':False})['ok'])
            page.assert_not_called()
            summary.assert_not_called()
