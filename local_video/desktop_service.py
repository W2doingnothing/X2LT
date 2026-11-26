# SPDX-License-Identifier: GPL-2.0-only
"""Composition root and guarded desktop bridge; business logic lives in services."""
from pathlib import Path
import threading
from urllib.parse import urlsplit

from .assets import LibraryAssets
from .bridge_contracts import ACTIONS, BridgeResponse, validate_request
from .libraries import LibraryManager
from .local_groups import LocalGroups
from .previews import PreviewCache
from .security import Vault
from .store import Store
from .services.settings import SettingsService, random_video_libraries
from .services.libraries import LibraryService
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
from .services.telegram import TelegramService


class DesktopService:
    def __init__(self, data_dir, home):
        self._data, self._home = Path(data_dir), Path(home)
        self._data.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._closed = False
        self._random_player = None
        self._create_random_player = None
        self._trusted_origin = None
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
                                         self._local.collection)
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
            self._handlers = self._build_handlers()
            if set(self._handlers) != set(ACTIONS):
                raise RuntimeError('桌面接口注册不完整')
            self._tools.recover_removals()
        except Exception:
            self._store.close()
            raise

    def _build_handlers(self):
        return {
            'bootstrap': self._queries.bootstrap,
            'snapshot': self._queries.snapshot,
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
            'settings': self._settings_service.save,
            'credentials': self._settings_service.update_credentials,
            'import_legacy': lambda p: self._settings_service.import_existing(),
            'choose_folder': lambda p: self._native.choose_folder(),
            'library_add': self._library_service.add, 'library_update': self._library_service.update,
            'library_default': self._library_service.set_default,
            'library_remove': self._library_service.remove, 'library_refresh': self._library_service.refresh,
            'ignore': self._tools.ignore,
            'pause': lambda p: self._runtime.pause(), 'start': self._downloads.start,
            'organize': self._tools.organize,
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
            'tg_scan_preview': self._telegram.scan_preview, 'tg_forward_avatar': self._telegram.forward_avatar,
            'tg_enqueue': self._telegram.enqueue, 'tg_jobs': self._telegram.jobs,
            'tg_job_preview': self._telegram.job_preview,
            'tg_control': self._telegram.control, 'tg_open_source': self._telegram.open_source,
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
            if action in ('bootstrap', 'snapshot', 'resources', 'resource_overview', 'library_refresh',
                          'preview', 'avatar', 'cover', 'cloud_status', 'cloud_login',
                          'local_media_preview', 'local_comic_page') or (
                              isinstance(action, str) and action.startswith('tg_')):
                data = self._dispatch(action, payload)
            else:
                with self._lock: data = self._dispatch(action, payload)
            return {'ok': True, 'data': data}
        except Exception as exc:
            return {'ok': False, 'error': self._settings_service.redact(str(exc))}

    def _dispatch(self, action, payload):
        request = validate_request(action, payload)
        return self._handlers[action](request)

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
