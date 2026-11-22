from collections import Counter
import unittest

from local_video.cloud_directory_cache import CloudDirectoryCache


class CloudDirectoryCacheTests(unittest.TestCase):
    def setUp(self):
        self.calls = Counter()
        self.data = {
            ('', 0): {'items': [{'type': 'directory', 'id': 'a'}], 'has_more': True},
            ('', 1): {'items': [{'type': 'directory', 'id': 'b'}], 'has_more': False},
            ('a', 0): {'items': [{'type': 'directory', 'id': 'c'}], 'has_more': False},
            ('b', 0): {'items': [], 'has_more': False},
            ('c', 0): {'items': [], 'has_more': False},
        }
        self.cache = CloudDirectoryCache(self.fetch, delay=0)
        self.addCleanup(self.cache.close)

    def fetch(self, parent, page):
        self.calls[parent, page] += 1
        return self.data[parent, page]

    def test_cached_pages_and_refresh_only_on_demand(self):
        self.cache.set_account('account-a')
        self.cache.page('account-a', '', 1)
        self.cache.page('account-a', 'a', 0)
        self.cache.page('account-a', '', 1)
        self.assertEqual(self.calls['', 1], 1)
        self.cache.page('account-a', '', 1, refresh=True)
        self.assertEqual(self.calls['', 1], 2)
        self.cache.set_account('account-b')
        self.cache.page('account-b', '', 1)
        self.assertEqual(self.calls['', 1], 3)

    def test_prefetch_visits_all_pages_subdirectories_and_reuses_foreground_reads(self):
        self.cache.set_account('account-a')
        self.cache.page('account-a', '', 0)
        self.cache.set_enabled(True)
        worker = self.cache.worker
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(self.calls, Counter({key: 1 for key in self.data}))
        self.cache.page('account-a', 'c', 0)
        self.assertEqual(self.calls['c', 0], 1)

    def test_failed_refresh_keeps_existing_page(self):
        self.cache.set_account('account-a')
        page = self.cache.page('account-a', '', 0)
        def unavailable(parent, offset):
            raise ValueError('temporary network failure')
        self.cache.fetch = unavailable
        with self.assertRaises(ValueError):
            self.cache.page('account-a', '', 0, refresh=True)
        self.assertEqual(self.cache.page('account-a', '', 0), page)
