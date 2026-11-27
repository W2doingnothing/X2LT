# SPDX-License-Identifier: GPL-2.0-only
"""Settings and user-scoped credentials; public state never contains secrets."""
import json
import re
from pathlib import Path
import threading
from ..policy import normalize_proxy, REQUEST_STRATEGIES
from ..security import atomic_json, import_legacy, redact

DEFAULT_APP_ICON = 'gray-pixel'
APP_ICONS = (DEFAULT_APP_ICON, 'black-white-pixel', 'black-white-illustration', 'gray-illustration')

DEFAULTS = dict(confirm_removal=True, privacy_mode=False, theme='light', accent='#397863',
                app_icon=DEFAULT_APP_ICON,
                username='', quality='best', connections=4, max_mb=4096,
                media_mode='all', request_strategy='conservative', infinite_scroll=False,
                overview_layout='', random_video_libraries=[])

RANDOM_LIBRARY_LIMIT = 1000


def random_video_libraries(value):
    if (type(value) is not list or len(value) > RANDOM_LIBRARY_LIMIT
            or any(type(item) is not str or not 0 < len(item) <= 128 for item in value)):
        raise ValueError('随机播放媒体库列表无效')
    return list(dict.fromkeys(value))


def overview_layout(value):
    if value == '':
        return ''
    try:
        if not isinstance(value,str) or len(value)>1024:
            raise ValueError()
        modules = json.loads(value)
        if not isinstance(modules,list) or len(modules)!=3:
            raise ValueError()
        for item in modules:
            if (not isinstance(item,dict) or item.keys() != {'id','visible','width'}
                    or item['id'] not in ('stats','recent','libraries') or type(item['visible']) is not bool
                    or item['width'] not in ('half','full')):
                raise ValueError()
        if len({item['id'] for item in modules}) != 3:
            raise ValueError()
    except ValueError:
        raise ValueError('总览布局无效') from None
    return json.dumps(modules,separators=(',',':'))


class SettingsService:
    def __init__(self, path, home, vault):
        self.path, self.home, self.vault = Path(path), Path(home), vault
        self.lock = threading.RLock()
        try: saved = json.loads(self.path.read_text(encoding='utf8'))
        except FileNotFoundError: saved = {}
        if not isinstance(saved, dict): raise ValueError('设置文件格式无效')
        self.values = {**DEFAULTS, **saved}
        if self.values['app_icon'] not in APP_ICONS:
            self.values['app_icon'] = DEFAULT_APP_ICON
        try:
            self.values['overview_layout'] = overview_layout(self.values['overview_layout'])
        except ValueError:
            self.values['overview_layout'] = ''
        try:
            self.values['random_video_libraries'] = random_video_libraries(self.values['random_video_libraries'])
        except ValueError:
            self.values['random_video_libraries'] = []
        self.values.pop('proxy', None)
        self.values.pop('quality_label', None)
        self.credential_error = ''
        try: self.credentials = self.vault.load()
        except Exception:
            self.credentials = {}
            self.credential_error = '原有凭证无法解密，请重新导入；未覆盖原凭证'

    def public(self):
        return {key:self.values[key] for key in DEFAULTS}

    def redact(self, value):
        return redact(value, self.credentials.values())

    def save(self, patch):
        with self.lock:
            return self._save(patch)

    def rebind_output(self, source, target):
        with self.lock:
            if self.values.get('output') and Path(self.values['output']) == source:
                values = dict(self.values, output=str(target))
                atomic_json(self.path, values)
                self.values = values

    def _save(self, patch):
        if not isinstance(patch, dict) or set(patch)-DEFAULTS.keys(): raise ValueError('不支持的设置字段')
        values = {**self.values, **patch}
        if type(values['privacy_mode']) is not bool: raise ValueError('隐私模式开关无效')
        if type(values['confirm_removal']) is not bool: raise ValueError('移除确认开关无效')
        if type(values['infinite_scroll']) is not bool: raise ValueError('连续加载开关无效')
        if 'overview_layout' in patch:
            values['overview_layout'] = overview_layout(patch['overview_layout'])
        if 'random_video_libraries' in patch:
            values['random_video_libraries'] = random_video_libraries(patch['random_video_libraries'])
        if values['theme'] not in ('light','dark') or not isinstance(values['accent'],str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',values['accent']): raise ValueError('主题或颜色码无效')
        if values['app_icon'] not in APP_ICONS: raise ValueError('应用图标无效')
        if values['quality'] not in ('best','720p','480p') or values['media_mode'] not in ('all','video','image'): raise ValueError('画质或媒体类型无效')
        if type(values['connections']) is not int or values['connections'] not in (1,2,4) or type(values['max_mb']) is not int or not 1<=values['max_mb']<=65536: raise ValueError('连接数或单文件上限无效')
        if values['request_strategy'] not in REQUEST_STRATEGIES.values(): raise ValueError('请求策略无效')
        if not isinstance(values['username'],str) or values['username'] and not re.fullmatch(r'@?[A-Za-z0-9_]{1,30}',values['username']): raise ValueError('账号格式无效')
        atomic_json(self.path,values)
        self.values = values
        return self.public()

    def update_credentials(self, patch):
        if not isinstance(patch,dict) or set(patch)-{'auth_token','ct0','proxy'}: raise ValueError('凭证格式无效')
        values = dict(self.credentials)
        for key,value in patch.items():
            if not isinstance(value,str) or len(value)>8192: raise ValueError('凭证格式无效')
            values[key] = normalize_proxy(value) if key=='proxy' else value.strip()
        try:
            self.vault.save(values)
        except Exception as exc:
            # New credentials are not yet in self.credentials when persistence fails.
            raise ValueError(self.redact(redact(str(exc), values.values()))) from None
        self.credentials = values
        self.credential_error = ''
        return True

    def import_existing(self):
        base = self.home if (self.home/'cookies.txt').exists() else self.home.parent
        values = import_legacy(base)
        if not values.get('auth_token'): raise ValueError('未在软件目录或上一级找到登录凭证')
        self.update_credentials({k:values.get(k,'') for k in ('auth_token','ct0','proxy')})
        self.save({'username':values.get('username','')})
        return True
