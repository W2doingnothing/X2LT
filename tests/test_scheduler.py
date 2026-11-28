from datetime import date, datetime
from unittest import TestCase
from unittest.mock import Mock

from local_video.services.scheduler import DesktopScheduler, download_window
from local_video.services.telegram_automation import TelegramAutomation, telegram_scan_rules


class DownloadScheduleTests(TestCase):
    def test_normal_and_overnight_boundaries(self):
        def at(hour, minute=0):
            return datetime(2026, 11, 28, hour, minute)

        self.assertEqual(download_window(at(0), '00:00', '05:00'), date(2026, 11, 28))
        self.assertIsNone(download_window(at(5), '00:00', '05:00'))
        self.assertEqual(download_window(at(2, 59), '23:00', '03:00'), date(2026, 11, 27))
        self.assertIsNone(download_window(at(3), '23:00', '03:00'))
        self.assertEqual(download_window(at(23), '23:00', '03:00'), date(2026, 11, 28))

    def test_scheduler_detects_start_stop_and_next_day_after_sleep(self):
        settings = dict(schedule_enabled=True, schedule_start='23:00', schedule_end='03:00')
        clock = Mock(return_value=datetime(2026, 11, 28, 2, 0))
        transition, run = Mock(), Mock()
        scheduler = DesktopScheduler(lambda: settings, transition, run, clock=clock)
        scheduler.tick()
        transition.assert_called_once_with(True, True)
        run.assert_called_once()
        scheduler.attempted.add('library')
        clock.return_value = datetime(2026, 11, 29, 2, 0)
        scheduler.tick()
        self.assertFalse(scheduler.attempted)
        self.assertEqual(transition.call_count, 2)
        clock.return_value = datetime(2026, 11, 29, 5, 25)
        scheduler.tick()
        transition.assert_called_with(True, False)
        self.assertEqual(run.call_count, 2)


class TelegramScheduleTests(TestCase):
    def setUp(self):
        self.rule = dict(mode='saved', source='', title='', library_id='default',
                         on_start=True, in_window=True, interval=60, media_mode='all')
        self.clock = Mock(return_value=1000)
        self.start = Mock(return_value='scan')
        self.result = Mock(return_value={'state': 'scanning'})
        self.journal = Mock()
        self.last = {}
        self.journal.last_automatic_run.side_effect = self.last.get
        self.journal.record_automatic_run.side_effect = self.last.__setitem__
        self.schedule = TelegramAutomation(lambda: [self.rule], self.start, self.result, self.journal, clock=self.clock)

    def test_startup_and_interval_share_one_scan_and_wait_for_login(self):
        self.schedule.tick(True, False)
        self.start.assert_not_called()
        self.schedule.tick(True, True)
        self.schedule.tick(True, True)
        self.start.assert_called_once_with(self.rule)
        self.result.return_value = {'state': 'ready', 'added': 4}
        self.schedule.tick(True, True)
        self.clock.return_value = 4600
        self.schedule.tick(False, True)
        self.start.assert_called_once()
        self.schedule.tick(True, True)
        self.assertEqual(self.start.call_count, 2)

    def test_startup_outside_window_respects_flood_wait_before_retry(self):
        self.schedule.tick(False, True)
        self.result.return_value = {'state': 'failed', 'retry_after': 120}
        self.schedule.tick(False, True)
        self.clock.return_value = 1119
        self.schedule.tick(False, True)
        self.start.assert_called_once()
        self.clock.return_value = 1120
        self.schedule.tick(False, True)
        self.assertEqual(self.start.call_count, 2)

    def test_rule_validation_rejects_bad_source_and_duplicates(self):
        self.assertEqual(telegram_scan_rules([self.rule]), [self.rule])
        for rules in ([self.rule, self.rule], [dict(self.rule, mode='channel', source='https://example.com')]):
            with self.assertRaises(ValueError):
                telegram_scan_rules(rules)
