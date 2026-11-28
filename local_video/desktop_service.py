# SPDX-License-Identifier: GPL-2.0-only
"""Composition root and guarded desktop bridge; business logic lives in services."""
from pathlib import Path
import threading
from time import monotonic, sleep
from urllib.parse import urlsplit

from .assets import LibraryAssets
from .bridge_contracts import ACTIONS, BridgeResponse, validate_request
from .libraries import LibraryManager
from .local_groups import LocalGroups
from .previews import PreviewCache
from .runner import Runner
from .security import Vault
from .store import Store
from .services.settings import SettingsService, random_video_libraries
from .services.libraries import LibraryService
from .services.library_migration import LibraryMigrationService
from .services.queries import QueryService
from .services.downloads import DownloadService
from .services.tools import MediaToolsService
from .services.media import MediaService
from .services.runtime import WorkerRuntime
from .services.native import NativeHost
from .services.cloud import CloudService
from .services.local_library import LocalLibraryService
from .services.playback import PlaybackService
from .services.random_feed import RandomFeed
from .services.scheduler import DesktopScheduler
from .services.telegram import TelegramService


_STORAGE_WRITES = frozenset({
    'start', 'organize', 'removal_start', 'removal_retry',
    'library_add', 'library_update', 'library_remove',
    'local_import_start', 'local_import_retry', 'local_file_manage',
    'local_media_link', 'local_media_relocate', 'local_media_remove', 'local_comic_update',
    'cloud_start_upload', 'cloud_retry', 'tg_enqueue', 'tg_control', 'tg_bulk_control',
})


class DesktopService:
    def __init__(self, data_dir, home):
        self._data, self._home = Path(data_dir), Path(home)
        self._data.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._closed = False
        self._random_player = None
        self._create_random_player = None
        self._refresh_app_icons = None
        self._trusted_origin = None
        self._configure_windows_startup = None
        self._restore_backfill = True
        self._native = NativeHost()
        self._runtime = WorkerRuntime()
        self._settings_service = SettingsService(self._data/'settings.json', self._home, Vault(self._data/'credentials.dpapi'))
        self._store = Store(self._data/'queue.sqlite3', defer_catalog=True)
        try:
            self._store.recover()
            self._libraries = LibraryManager(self._data/'libraries.json')
            self._library_service = LibraryService(self._libraries, self._store, self._settings_service,
                                                   self._native, self._runtime, self._data, self._home)
            self._local = LocalLibraryService(self._data, self._store, self._libraries, self._native, self._runtime)
            self._playback = PlaybackService(self._store,self._libraries,self._local.collection)
            self._groups = LocalGroups(self._local.collection)
            self._queries = QueryService(self._store, self._settings_service, self._library_service, self._runtime,
                                         self._groups)
            self._downloads = DownloadService(self._store, self._settings_service, self._library_service, self._runtime)
            self._tools = MediaToolsService(self._store, self._settings_service, self._library_service, self._runtime, self._native)
            cache = PreviewCache(self._data/'previews')
            self._media = MediaService(self._store, self._settings_service, self._library_service, self._runtime,
                                       self._native, cache, LibraryAssets(self._data/'covers', cache))
            self._cloud = CloudService(self._data, self._store, self._libraries, self._native, self._runtime,
                                       upload_only=True)
            self._telegram = TelegramService(
                self._data, self._store, self._libraries, self._local.collection, self._groups,
                self._native, self._settings_service, self._local.notify, cache, self._local.preview,
            )
            self._migration = LibraryMigrationService(
                self._libraries, self._store, self._native, self._runtime, self._data,
                self._settings_service, idle_check=self._storage_idle,
            )
            self._scheduler = DesktopScheduler(lambda: self._settings, self._download_window,
                                               self._scheduled_downloads, self._background_scans)
            self._handlers = self._build_handlers()
            if set(self._handlers) != set(ACTIONS):
                raise RuntimeError('桌面接口注册不完整')
            if not self._migration.pending():
                self._tools.recover_removals()
                self._tools.recover_auto_flatten()
        except Exception:
            self._store.close()
            raise

    def _build_handlers(self):
        return {
            'bootstrap': self._queries.bootstrap,
            'snapshot': self._queries.snapshot,
            'library_cards': self._queries.library_cards,
            'filters': self._tools.filters, 'filters_clear': self._tools.clear_filters,
            'removal_plan': self._tools.plan_removal, 'removal_start': self._tools.start_removal,
            'removal_status': lambda p: self._store.removal.status(p['id']),
            'removal_retry': self._tools.retry_removal,
            'resources': self._queries.resources,
            'resource_overview': lambda p: self._queries.resources(p, overview=True),
            'author_preferences': self._queries.author_preferences,
            'poll': lambda p: self._runtime.poll(),
            'tags_set': self._queries.set_tags, 'tag_rename': self._queries.rename_tag,
            'tag_delete': self._queries.delete_tag,
            'library_cover': self._media.set_cover,
            'settings': self._save_settings,
            'credentials': self._settings_service.update_credentials,
            'import_legacy': lambda p: self._settings_service.import_existing(),
            'choose_folder': lambda p: self._native.choose_folder(),
            'library_add': self._library_service.add, 'library_update': self._library_service.update,
            'library_default': self._library_service.set_default,
            'library_remove': self._library_service.remove, 'library_refresh': self._library_service.refresh,
            'ignore': self._tools.ignore,
            'pause': self._pause_download, 'start': self._downloads.start,
            'organize': self._tools.organize,
            'library_migration_plan': self._migration.plan,
            'library_migration_status': self._migration.status,
            'library_migration_start': lambda p: self._change_storage(p['id'], self._migration.start, p),
            'library_migration_retry': lambda p: self._change_storage(p['id'], self._migration.retry, p),
            'library_migration_abandon': lambda p: self._migration.abandon(p),
            'library_auto_flatten': self._set_auto_flatten,
            **{name: (lambda p, action=name: self._media.open(action, p))
               for name in ('open_profile', 'open_tweet', 'open_folder', 'open_media')},
            'video_open': self._open_video, 'video_close': self._playback.revoke,
            'random_feed_launch': self._launch_random_feed,
            'clear_previews': self._clear_previews, 'preview': self._media.preview,
            'avatar': lambda p: self._media.asset('avatar', p),
            'cover': lambda p: self._media.asset('cover', p),
            'local_pick': self._local.pick, 'local_scan': self._local.scan,
            'local_library_scan': lambda p: self._local.scan_library(p),
            'local_scan_exclusions': lambda p: self._local.scan_exclusions(p),
            'local_scan_restore': lambda p: self._local.scan_restore(p),
            'local_file_manage': lambda p: self._local.manage_file(p),
            'local_group_change': self._change_local_group,
            'local_import_status': self._local.status, 'local_import_start': self._local.start,
            'local_import_cancel': self._local.cancel, 'local_import_retry': self._local.retry,
            'local_library': self._local_page, 'local_media_tags': self._local.tags,
            'local_media_edit': self._edit_local_media,
            'local_media_open': self._local.open, 'local_media_preview': self._local.preview,
            'local_media_link': self._local.link, 'local_media_relocate': self._local.relocate,
            'local_media_remove': self._local.remove, 'local_comic_get': self._local.comic_get,
            'local_comic_page': self._local.comic_page, 'local_comic_update': self._local.comic_update,
            'cloud_status': self._cloud.status, 'cloud_login': self._cloud.login,
            'cloud_pick_upload': self._cloud.pick_upload, 'cloud_start_upload': self._cloud.start_upload,
            'cloud_jobs': self._cloud.jobs,
            'cloud_retry': self._cloud.retry,
            'cloud_cancel': self._cloud.cancel_jobs,
            'tg_status': self._telegram.status, 'tg_import': self._telegram.import_credentials,
            'tg_credentials': self._telegram.manual_credentials,
            'tg_send_code': self._telegram.send_code, 'tg_sign_in': self._telegram.sign_in,
            'tg_resend_code': self._telegram.resend_code, 'tg_reset_code': self._telegram.reset_code,
            'tg_qr_start': self._telegram.qr_start, 'tg_qr_status': self._telegram.qr_status,
            'tg_qr_cancel': self._telegram.qr_cancel,
            'tg_logout': self._telegram.logout, 'tg_channels': self._telegram.channels,
            'tg_proxy': self._telegram.proxy, 'tg_scan': self._telegram.scan,
            'tg_scan_status': self._telegram.scan_status, 'tg_scan_cancel': self._telegram.cancel_scan,
            'tg_backfill_status': self._telegram.backfill_status,
            'tg_backfill_resume': lambda p: self._telegram.resume_backfill(),
            'tg_scan_preview': self._telegram.scan_preview, 'tg_forward_avatar': self._telegram.forward_avatar,
            'tg_enqueue': self._telegram.enqueue, 'tg_jobs': self._telegram.jobs,
            'tg_job_preview': self._telegram.job_preview,
            'tg_control': self._telegram.control, 'tg_bulk_control': self._telegram.bulk_control,
            'tg_open_source': self._telegram.open_source,
        }

    def call(self, action, payload=None) -> BridgeResponse:
        try:
            if self._window is not None:
                current = urlsplit(self._window.get_current_url() or '')
                expected = urlsplit(self._window.real_url or '')
                if (current.scheme != 'http' or current.hostname not in ('127.0.0.1', 'localhost')
                        or current.netloc != expected.netloc or current.path != '/index.html'
                        or current.query != expected.query):
                    raise ValueError('仅本地应用界面可调用桌面接口')
            if self._closed: raise ValueError('窗口正在关闭')
            if action in ('bootstrap', 'snapshot', 'library_cards', 'resources', 'resource_overview', 'library_refresh',
                          'preview', 'avatar', 'cover', 'cloud_status', 'cloud_login',
                          'local_media_preview', 'local_comic_page', 'library_migration_plan',
                          'library_migration_status') or (
                              isinstance(action, str) and action.startswith('tg_') and action not in _STORAGE_WRITES):
                data = self._dispatch(action, payload)
            else:
                with self._lock: data = self._dispatch(action, payload)
            return {'ok': True, 'data': data}
        except Exception as exc:
            return {'ok': False, 'error': self._settings_service.redact(str(exc))}

    def _dispatch(self, action, payload):
        request = validate_request(action, payload)
        if action in _STORAGE_WRITES or action == 'settings' and 'output' in request:
            library = None if action == 'start' and request.get('mode') in ('force', 'enrich') else request.get('library_id')
            if self._migration.pending(library) or self._libraries.auto_flatten_pending(library):
                raise ValueError('请先完成该媒体库的整库迁移或脱壳整理')
        return self._handlers[action](request)

    def _storage_idle(self):
        with self._local.lock:
            importing = any(job['stage'] in ('scanning', 'importing') for job in self._local.jobs.values())
        cloud_worker = self._cloud.worker
        return not importing and not (cloud_worker and cloud_worker.is_alive())

    def _save_settings(self, request):
        previous_icon = self._settings['app_icon']
        previous_startup = self._settings['start_with_windows']
        settings = self._settings_service.save(request)
        if self._configure_windows_startup and settings['start_with_windows'] != previous_startup:
            try:
                self._configure_windows_startup(settings['start_with_windows'])
            except OSError as exc:
                self._settings_service.save({'start_with_windows': previous_startup})
                raise ValueError('无法修改 Windows 登录时启动设置') from exc
        self._scheduler.changed.set()
        if self._refresh_app_icons and settings['app_icon'] != previous_icon:
            self._refresh_app_icons()
        return settings

    def _pause_download(self, request):
        if self._downloads.downloading():
            self._downloads.set_manual_hold(True)
        return self._runtime.pause()

    def _download_window(self, enabled, opened):
        with self._lock:
            self._telegram._queue.set_download_window(enabled, opened)
            if enabled and not opened and self._downloads.downloading():
                self._runtime.pause()

    def _scheduled_downloads(self, attempted):
        with self._lock:
            if self._migration.pending() or self._runtime.removing():
                return
            if self._telegram.status({}).get('authorized'):
                self._telegram._queue.resume_scheduled()
            if self._runtime.busy() or self._downloads.manual_hold:
                return
            for library in tuple(self._libraries.items):
                identifier, path = library['id'], library['path']
                if (identifier in attempted or self._libraries.auto_flatten_pending(identifier)
                        or identifier in self._library_service.errors or not Path(path).is_dir()):
                    continue
                if self._store.runnable_ids(path, automatic=True):
                    self._downloads.start({'library_id': identifier}, automatic=True)
                    attempted.add(identifier)
                    break

    def _background_scans(self, opened):
        if not self._restore_backfill:
            return
        with self._lock:
            if self._migration.pending() or not self._telegram.status({}).get('authorized'):
                return
            self._telegram.resume_backfill()
            self._restore_backfill = False

    def pause_all_downloads(self):
        with self._lock:
            self._downloads.set_manual_hold(True)
            if self._downloads.downloading():
                self._runtime.pause()
            self._telegram._queue.control('pause')

    def continue_downloads(self):
        with self._lock:
            if self._migration.pending():
                raise ValueError('请先完成媒体库迁移')
            self._downloads.set_manual_hold(False)
            if self._telegram.status({}).get('authorized'):
                self._telegram._queue.control('resume')
            if not self._runtime.busy():
                self._scheduled_downloads(set())

    def _change_storage(self, library_id, operation, request):
        if not self._storage_idle() or self._runtime.removing():
            raise ValueError('请等待本地入库、文件移除或云盘任务结束')
        path = self._libraries.get(library_id)['path']
        runner = self._runtime.runner
        if self._runtime.busy():
            if not isinstance(runner, Runner) or runner.settings['output'] != path:
                raise ValueError('请先完成或暂停当前文件操作')
            runner.cancel.set()
        queue = self._telegram._queue
        with queue.condition:
            jobs = [job for job in queue.list() if job['library_id'] == library_id]
            targets = {job['id'] for job in jobs}
            if active := [job['id'] for job in jobs if job['state'] in ('queued', 'waiting', 'downloading')]:
                queue.control('pause', active)
        deadline = monotonic() + 3
        while self._runtime.busy() or queue.active in targets:
            if monotonic() >= deadline:
                raise ValueError('正在保存下载断点，请稍后继续')
            sleep(.05)
        return operation(request)

    def _set_auto_flatten(self, request):
        library_id = request['library_id']
        if self._migration.pending(library_id):
            raise ValueError('请先完成该媒体库的整库迁移')
        return self._change_storage(library_id, self._tools.set_auto_flatten, request)

    def _local_page(self, p):
        library = self._libraries.get(p['library_id'])
        return self._groups.page(library['id'],p.get('query',''),p.get('tags',[]),
                                 p.get('untagged',False),p.get('offset',0),p.get('limit',40))

    def _change_local_group(self, p):
        library = self._libraries.get(p['library_id'])
        result = self._groups.change(library['id'],p)
        self._runtime.emit({'type':'changed'})
        return result

    def _edit_local_media(self, p):
        library = self._libraries.get(p['library_id'])
        self._local.collection.edit_text(library['id'], p['id'], p['title'], p['body'])
        self._runtime.emit({'type':'changed'})
        return True

    def _clear_previews(self, p):
        self._media.clear(p)
        self._local.clear_previews()
        return True

    def _open_video(self, p):
        if self._window is None:
            raise ValueError('仅支持从桌面媒体库播放')
        return self._playback.issue(p,self._window.real_url or '')

    def _launch_random_feed(self, p):
        if self._create_random_player is None:
            raise ValueError('仅支持从桌面媒体库打开随机播放器')
        if self._random_player and not self._random_player.closed:
            self._random_player.show()
            return {'opened': False}
        selected = random_video_libraries(p['library_ids'])
        feed = RandomFeed(self._store, self._libraries, selected)
        player = self._create_random_player(feed)
        self._random_player = player
        if self._closed:
            player.close()
            raise ValueError('窗口正在关闭')
        try:
            self._settings_service.save({'random_video_libraries': selected})
        except Exception:
            player.close()
            raise
        return {'opened': True}

    def _close(self):
        if self._closed: return True
        if not self._scheduler.close(): return False
        if not self._local.close(): return False
        if not self._runtime.close(): return False
        if not self._cloud.close(): return False
        if not self._telegram.close(): return False
        self._closed = True
        if self._random_player and not self._random_player.closed:
            self._random_player.close()
        self._playback.close()
        self._libraries.close()
        self._store.close()
        return True

    # Compatibility accessors for the native bootstrap and existing diagnostics.
    # None of this graph is exposed to JavaScript (desktop.bind_bridge exposes call only).
    @property
    def _window(self): return self._native.window
    @_window.setter
    def _window(self, value): self._native.window = value
    @property
    def _settings(self): return self._settings_service.values
    @property
    def _settings_path(self): return self._settings_service.path
    @property
    def _credentials(self): return self._settings_service.credentials
    @property
    def _credential_error(self): return self._settings_service.credential_error
    @property
    def _vault(self): return self._settings_service.vault
    @property
    def _approved_paths(self): return self._native.approved_paths
    @property
    def _operation(self): return self._runtime.operation
    @property
    def _relocation_errors(self): return self._library_service.errors
    @property
    def _cache(self): return self._media.cache
    def _busy(self): return self._runtime.busy()
    def _emit(self, event): return self._runtime.emit(event)
    def _run_settings(self, library_id): return self._downloads.run_settings(library_id)
    def _repair_library_paths(self): return self._library_service.repair_paths()
