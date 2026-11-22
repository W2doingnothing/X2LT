# SPDX-License-Identifier: GPL-2.0-only
"""Composition root and guarded desktop bridge; business logic lives in services."""
from pathlib import Path
import threading
from urllib.parse import urlsplit

from .assets import LibraryAssets
from .bridge_contracts import ACTIONS, BridgeResponse, validate_request
from .libraries import LibraryManager
from .previews import PreviewCache
from .security import Vault
from .store import Store
from .services.settings import SettingsService
from .services.libraries import LibraryService
from .services.queries import QueryService
from .services.downloads import DownloadService
from .services.tools import MediaToolsService
from .services.media import MediaService
from .services.runtime import WorkerRuntime
from .services.native import NativeHost
from .services.cloud import CloudService


class DesktopService:
    def __init__(self, data_dir, home):
        self._data, self._home = Path(data_dir), Path(home)
        self._data.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._closed = False
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
            self._queries = QueryService(self._store, self._settings_service, self._library_service, self._runtime)
            self._downloads = DownloadService(self._store, self._settings_service, self._library_service, self._runtime)
            self._tools = MediaToolsService(self._store, self._settings_service, self._library_service, self._runtime, self._native)
            cache = PreviewCache(self._data/'previews')
            self._media = MediaService(self._store, self._settings_service, self._library_service, self._runtime,
                                       self._native, cache, LibraryAssets(self._data/'covers', cache))
            self._cloud = CloudService(self._data, self._store, self._libraries, self._native, self._runtime)
            self._cloud.directories.set_enabled(self._settings_service.values['cloud_prefetch'] is True)
            self._handlers = self._build_handlers()
            if set(self._handlers) != set(ACTIONS):
                raise RuntimeError('桌面接口注册不完整')
            self._tools.recover_removals()
        except Exception:
            self._store.close()
            raise

    def _build_handlers(self):
        return {
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
            'settings': self._save_settings,
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
            'clear_previews': self._clear_previews, 'preview': self._media.preview,
            'avatar': lambda p: self._media.asset('avatar', p),
            'cover': lambda p: self._media.asset('cover', p),
            'cloud_status': self._cloud.status, 'cloud_login': self._cloud.login,
            'cloud_list': self._cloud.list, 'cloud_import': self._cloud.import_folder,
            'cloud_catalog': self._cloud.catalog_page, 'cloud_tags': self._cloud.tags,
            'cloud_link': self._cloud.link, 'cloud_positions': self._cloud.positions,
            'cloud_upload': self._cloud.upload, 'cloud_download': self._cloud.download,
            'cloud_locate': self._cloud.locate, 'cloud_open': self._cloud.open,
            'cloud_preview': self._cloud.preview, 'cloud_source': self._cloud.open_source,
            'cloud_thumbnail': self._cloud.thumbnail,
            'cloud_preview_credentials': self._cloud.preview_credentials,
            'cloud_media_view': self._cloud.media_view, 'cloud_playback_status': self._cloud.playback_status,
            'cloud_playback_connect': self._cloud.playback_connect,
            'cloud_check': self._cloud.check,
            'cloud_release': self._cloud.release, 'cloud_jobs': self._cloud.jobs,
            'cloud_retry': self._cloud.retry,
            'cloud_backup': self._cloud.backup, 'cloud_restore': self._cloud.restore,
        }

    def call(self, action, payload=None) -> BridgeResponse:
        try:
            if self._window is not None:
                current = urlsplit(self._window.get_current_url() or '')
                expected = urlsplit(self._window.real_url or '')
                if current.hostname not in ('127.0.0.1', 'localhost') or current.netloc != expected.netloc or current.path != '/index.html':
                    raise ValueError('仅本地应用界面可调用桌面接口')
            if self._closed: raise ValueError('窗口正在关闭')
            if action in ('preview', 'avatar', 'cover', 'cloud_preview', 'cloud_thumbnail',
                          'cloud_preview_credentials', 'cloud_list', 'cloud_status', 'cloud_login',
                          'cloud_media_view', 'cloud_playback_status', 'cloud_playback_connect'):
                data = self._dispatch(action, payload)
            else:
                with self._lock: data = self._dispatch(action, payload)
            return {'ok': True, 'data': data}
        except Exception as exc:
            return {'ok': False, 'error': self._settings_service.redact(str(exc))}

    def _dispatch(self, action, payload):
        request = validate_request(action, payload)
        if action in {'start','organize','removal_start','removal_retry','library_update','library_remove'}:
            if self._cloud.catalog.one(
                    "SELECT 1 FROM cloud_jobs WHERE state IN ('queued','running') AND kind='release'"):
                raise ValueError('请等待光鸭本地空间释放任务完成')
        return self._handlers[action](request)

    def _clear_previews(self, request):
        self._media.clear(request)
        self._cloud.thumbnails.cache.trim(clear=True)
        return True

    def _save_settings(self, request):
        saved = self._settings_service.save(request)
        if 'cloud_prefetch' in request:
            self._cloud.directories.set_enabled(saved['cloud_prefetch'])
        return saved

    def _close(self):
        if self._closed: return True
        if not self._runtime.close(): return False
        if not self._cloud.close(): return False
        self._closed = True
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
