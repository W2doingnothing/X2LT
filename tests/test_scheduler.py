from datetime import date, datetime
from unittest import TestCase
from unittest.mock import Mock

from local_video.services.scheduler import DesktopScheduler, download_window


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
