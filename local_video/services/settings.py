# SPDX-License-Identifier: GPL-2.0-only
"""Settings and user-scoped credentials; public state never contains secrets."""
import json
import re
from pathlib import Path
from ..emoji import validate_emoji
from ..policy import normalize_proxy, REQUEST_STRATEGIES
from ..security import atomic_json, import_legacy, redact

DEFAULTS = dict(confirm_removal=True, privacy_mode=False, theme='light', accent='#397863',
                username='', quality='best', connections=4, max_mb=4096,
                media_mode='all', request_strategy='conservative', cloud_prefetch=False,
                cloud_card_name='', cloud_card_emoji='')


class SettingsService:
    def __init__(self, path, home, vault):
        self.path, self.home, self.vault = Path(path), Path(home), vault
        try: saved = json.loads(self.path.read_text(encoding='utf8'))
        except FileNotFoundError: saved = {}
        if not isinstance(saved, dict): raise ValueError('设置文件格式无效')
        self.values = {**DEFAULTS, **saved}
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
        if not isinstance(patch, dict) or set(patch)-DEFAULTS.keys(): raise ValueError('不支持的设置字段')
        values = {**self.values, **patch}
        if type(values['privacy_mode']) is not bool: raise ValueError('隐私模式开关无效')
        if type(values['cloud_prefetch']) is not bool: raise ValueError('光鸭目录预读开关无效')
        if type(values['confirm_removal']) is not bool: raise ValueError('移除确认开关无效')
        if 'cloud_card_name' in patch:
            name = patch['cloud_card_name']
            if not isinstance(name,str) or len(name.strip()) > 80 or any(ord(c) < 32 for c in name):
                raise ValueError('光鸭卡片名称无效')
            values['cloud_card_name'] = name.strip()
        if 'cloud_card_emoji' in patch:
            values['cloud_card_emoji'] = validate_emoji(patch['cloud_card_emoji']) if patch['cloud_card_emoji'] != '' else ''
        if values['theme'] not in ('light','dark') or not isinstance(values['accent'],str) or not re.fullmatch(r'#[0-9a-fA-F]{6}',values['accent']): raise ValueError('主题或颜色码无效')
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
