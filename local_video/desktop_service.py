# SPDX-License-Identifier: GPL-2.0-only
"""Tk-independent desktop controller; only call() is exposed to JavaScript."""
import base64
from io import BytesIO
import json
import os
from pathlib import Path
import queue
import re
import threading
import time
import unicodedata
import webbrowser
from urllib.parse import urlsplit

from . import __version__
from .flatten import flatten_completed, rollback_completed
from .archive import original_path,manifest,read_json,comparable_path
from .libraries import LibraryManager
from .policy import likes_url,normalize_proxy,validate_source_url,profile_url,REQUEST_STRATEGIES
from .previews import PreviewCache
from .runner import Runner,destination_for
from .security import Vault,atomic_json,redact,import_legacy
from .store import Store
from .assets import LibraryAssets
from .operations import OperationState


class DesktopService:
    def __init__(self,data_dir,home):
        self._data=Path(data_dir);self._data.mkdir(parents=True,exist_ok=True);self._home=Path(home)
        self._settings_path=self._data/'settings.json';self._vault=Vault(self._data/'credentials.dpapi')
        try:self._settings=json.loads(self._settings_path.read_text(encoding='utf8'))
        except FileNotFoundError:self._settings={}
        self._credential_error=''
        try:self._credentials=self._vault.load()
        except Exception:self._credentials={};self._credential_error='原有凭证无法解密，请重新导入；未覆盖原凭证'
        self._settings={**{'privacy_mode':False,'theme':'light','accent':'#397863','username':'','quality':'best','connections':4,'max_mb':4096,'media_mode':'all','request_strategy':'conservative'},**self._settings}
        self._settings.pop('proxy',None)
        self._settings.pop('quality_label',None)
        self._libraries=LibraryManager(self._data/'libraries.json')
        if not self._libraries.items and not (self._data/'libraries-initialized').exists():
            path=Path(self._settings.get('output') or self._home/'downloads')
            if not path.exists() and not self._settings.get('output'):path.mkdir(parents=True)
            if path.is_dir():self._libraries.add('默认媒体库',path)
            elif path.is_absolute():
                # Retain an offline legacy location without rewriting any queue paths.
                self._libraries.items=[{'id':'legacy','name':'原下载库（离线）','path':str(path),'quota_bytes':0}]
                self._libraries.default_id='legacy';self._libraries._save()
            (self._data/'libraries-initialized').touch()
        self._store=Store(self._data/'queue.sqlite3',defer_catalog=True);self._store.recover()
        self._relocation_errors={}
        self._repair_library_paths()
        self._cache=PreviewCache(self._data/'previews');self._preview_lock=threading.BoundedSemaphore(2)
        self._assets=LibraryAssets(self._data/'covers',self._cache)
        self._operation=OperationState()
        self._window=None;self._approved_paths=set();self._runner=None;self._worker=None
        self._trusted_origin=None
        self._events=queue.Queue(maxsize=2000);self._lock=threading.RLock();self._cancel=threading.Event();self._closed=False

    def _busy(self):return self._worker is not None and self._worker.is_alive()

    def _emit(self,event):
        self._operation.update(event)
        try:self._events.put_nowait(event)
        except queue.Full:
            try:self._events.get_nowait()
            except queue.Empty:pass
            try:self._events.put_nowait({'type':'resync'})
            except queue.Full:pass

    def call(self,action,payload=None):
        try:
            if self._window is not None:
                current=urlsplit(self._window.get_current_url() or '')
                expected=urlsplit(self._window.real_url or '')
                if current.hostname not in ('127.0.0.1','localhost') or current.netloc!=expected.netloc or current.path!='/index.html':raise ValueError('仅本地应用界面可调用桌面接口')
            if self._closed:raise ValueError('窗口正在关闭')
            if not isinstance(payload or {},dict):raise ValueError('请求格式无效')
            # Serialise control mutations, not long running downloads/thumbnail fetches.
            if action in ('preview','avatar','cover'):
                data=self._preview(payload or {}) if action=='preview' else self._asset(action,payload or {})
            else:
                with self._lock:data=self._dispatch(action,payload or {})
            return {'ok':True,'data':data}
        except Exception as exc:
            return {'ok':False,'error':redact(str(exc),self._credentials.values())}

    def _dispatch(self,action,p):
        if action=='snapshot':return self._snapshot(p)
        if action=='author_preferences':
            library=self._libraries.get(p.get('library_id'))
            if not p.get('library_id'):raise ValueError('请选择媒体库')
            before=self._store.catalog.author_preferences(library['id'],library['path'],p.get('keys'),starred=p.get('starred'),in_other=p.get('in_other'),restore=p.get('restore'))
            self._emit({'type':'changed'})
            return before
        if action=='poll':
            events=[]
            for _ in range(500):
                try:events.append(self._events.get_nowait())
                except queue.Empty:break
            return {'events':events,'busy':self._busy(),'operation':self._operation.snapshot()}
        if action=='tags_set':self._store.catalog.set_tags(p['id'],p['tags']);self._emit({'type':'changed'});return True
        if action=='tag_rename':self._store.catalog.rename_tag(p['old'],p['new']);self._emit({'type':'changed'});return True
        if action=='tag_delete':self._store.catalog.delete_tag(p['name']);self._emit({'type':'changed'});return True
        if action=='library_cover':
            library=self._libraries.get(p['id']);kind=p.get('kind')
            if kind=='image':
                if self._window is None:raise ValueError('图片选择器仅桌面版可用')
                import webview
                selected=self._window.create_file_dialog(webview.FileDialog.OPEN,allow_multiple=False,file_types=('图片 (*.jpg;*.jpeg;*.png;*.webp)',))
                if not selected:return None
                cover=self._assets.import_cover(selected[0])
            elif kind=='emoji':
                value=p.get('value','')
                if not isinstance(value,str) or not 1<=len(value)<=16 or any(unicodedata.category(c).startswith('C') and c!='\u200d' for c in value):raise ValueError('请输入简短 emoji')
                cover={'kind':'emoji','value':value}
            elif kind=='reset':cover=None
            else:raise ValueError('封面类型无效')
            # Store only managed identifiers, never input paths or remote URLs.
            with self._libraries.lock:
                for item in self._libraries.items:
                    if item['id']==library['id']:item['cover']=cover
                self._libraries._save()
            return cover
        if action=='settings':return self._save_settings(p)
        if action=='credentials':
            values=dict(self._credentials)
            for key in ('auth_token','ct0','proxy'):
                if key in p:
                    value=p[key]
                    if not isinstance(value,str) or len(value)>8192:raise ValueError('凭证格式无效')
                    values[key]=normalize_proxy(value) if key=='proxy' else value.strip()
            self._vault.save(values);self._credentials=values;self._credential_error='';return True
        if action=='import_legacy':
            base=self._home if (self._home/'cookies.txt').exists() else self._home.parent
            values=import_legacy(base)
            if not values.get('auth_token'):raise ValueError('未在软件目录或上一级找到登录凭证')
            credentials={k:values.get(k,'') for k in ('auth_token','ct0','proxy')}
            self._vault.save(credentials);self._credentials=credentials;self._settings['username']=values.get('username','');self._save_settings({});return True
        if action=='choose_folder':
            if self._window is None:raise ValueError('文件夹选择器仅桌面版可用')
            import webview
            result=self._window.create_file_dialog(webview.FileDialog.FOLDER)
            if not result:return None
            path=str(Path(result[0]).resolve());self._approved_paths.add(path);return path
        if action=='library_add':
            path=str(Path(p['path']).resolve())
            if path not in self._approved_paths:raise ValueError('请使用文件夹选择按钮选取路径')
            return self._libraries.add(p['name'],path,p.get('quota_bytes',0))
        if action=='library_update':
            if self._busy():raise ValueError('请先暂停操作再修改媒体库')
            path=p.get('path')
            if path is not None and str(Path(path).resolve()) not in self._approved_paths:raise ValueError('请通过文件夹选择器重新定位')
            if path is not None:self._check_rebind(self._libraries.get(p['id'])['path'],str(Path(path).resolve()))
            result=self._libraries.update(p['id'],p.get('name'),path,p.get('quota_bytes'))
            self._repair_library_paths()
            if p['id'] in self._relocation_errors:raise ValueError(self._relocation_errors[p['id']])
            return result
        if action=='library_default':self._libraries.set_default(p['id']);return True
        if action=='library_remove':
            if self._busy():raise ValueError('请先暂停操作再移除媒体库')
            self._libraries.remove(p['id']);return True
        if action=='library_refresh':return self._libraries.snapshot(refresh=True)
        if action=='ignore':self._store.ignore_tweets(p['ids'],bool(p.get('ignored',True)));self._emit({'type':'changed'});return True
        if action=='pause':
            if self._runner:self._runner.cancel.set()
            return True
        if action=='start':return self._start(p)
        if action=='organize':return self._organize(p)
        if action in ('open_profile','open_tweet','open_folder','open_media'):
            if action=='open_profile':webbrowser.open(profile_url(p['handle']));return True
            rows=self._store.get_many([int(p['job_id'])])
            if not rows:raise ValueError('找不到媒体记录')
            if action=='open_tweet':webbrowser.open(validate_source_url(rows[0]['source']));return True
            path=destination_for(rows[0])
            if action=='open_folder':path=path.parent
            if not path.exists():raise ValueError('本地文件不存在')
            os.startfile(str(path));return True
        if action=='clear_previews':
            if self._busy():raise ValueError('请暂停后清理缓存')
            self._cache.trim(clear=True);return True
        raise ValueError('不支持的操作')

    def _save_settings(self,p):
        values=dict(self._settings)
        allowed={'privacy_mode','theme','accent','username','quality','connections','max_mb','media_mode','request_strategy'}
        if set(p)-allowed:raise ValueError('不支持的设置字段')
        values.update(p)
        if not isinstance(values['privacy_mode'],bool):raise ValueError('隐私模式开关无效')
        if values['theme'] not in ('light','dark') or not re.fullmatch(r'#[0-9a-fA-F]{6}',values['accent']):raise ValueError('主题或颜色码无效')
        if values['quality'] not in ('best','720p','480p') or values['media_mode'] not in ('all','video','image'):raise ValueError('画质或媒体类型无效')
        if values['connections'] not in (1,2,4) or isinstance(values['max_mb'],bool) or not isinstance(values['max_mb'],int) or not 1<=values['max_mb']<=65536:raise ValueError('连接数或单文件上限无效')
        if values['request_strategy'] not in REQUEST_STRATEGIES.values():raise ValueError('请求策略无效')
        if not isinstance(values['username'],str) or values['username'] and not re.fullmatch(r'@?[A-Za-z0-9_]{1,30}',values['username']):raise ValueError('账号格式无效')
        atomic_json(self._settings_path,values);self._settings=values;return {k:values[k] for k in allowed}

    def _run_settings(self,library_id):
        library=self._libraries.get(library_id)
        if library['id'] in self._relocation_errors:raise ValueError(self._relocation_errors[library['id']])
        return {**self._settings,'output':library['path'],'proxy':self._credentials.get('proxy',''),'_storage_guard':self._libraries}

    def _check_rebind(self,old,new):
        if old==new:return
        with self._store.lock:
            collision=self._store.db.execute('SELECT 1 FROM jobs a JOIN jobs b ON a.tweet_id=b.tweet_id AND a.media_id=b.media_id AND a.quality=b.quality WHERE a.output=? AND b.output=? LIMIT 1',(old,new)).fetchone()
            if collision:raise ValueError('目标路径已有独立下载记录，不能自动合并；请选择原媒体库的实际位置')

    def _repair_library_paths(self):
        self._relocation_errors={}
        for library in self._libraries.items:
            try:
                new=Path(library['path'])
                for old_value in library.get('previous_paths',[]):
                    old=Path(old_value)
                    if old==new:continue
                    self._check_rebind(str(old),str(new))
                    def remap(value):
                        if not value:return value
                        path=comparable_path(Path(value))
                        return str(new/path.relative_to(old)) if path.is_relative_to(old) else value
                    with self._store.lock:
                        rows=[dict(r) for r in self._store.db.execute('SELECT * FROM jobs WHERE output=?',(str(old),))]
                        for row in rows:
                            updated={**row,'output':str(new)}
                            receipt_path=manifest(original_path(updated));receipt=read_json(receipt_path)
                            if receipt.get('archive_job')==row['id'] and receipt.get('sha256')==row['sha256'] and receipt.get('archive_output') in (str(old),str(new)):
                                receipt['archive_output']=str(new)
                                for key in ('local_path','pending_source','pending_target'):
                                    if key in receipt:receipt[key]=remap(receipt[key])
                                atomic_json(receipt_path,receipt)
                        with self._store.db:
                            for row in rows:self._store.db.execute('UPDATE jobs SET output=?,local_path=?,relocation_source=? WHERE id=?',(str(new),remap(row['local_path']),remap(row['relocation_source']),row['id']))
                if library.get('previous_paths'):
                    library['previous_paths']=[]
                    self._libraries._save()
            except Exception as exc:self._relocation_errors[library['id']]=f'重新定位记录尚未完成，请修复路径后重试：{exc}'

    def _start(self,p):
        if self._busy():raise ValueError('已有操作正在运行，请先暂停')
        mode=p.get('mode','continue');settings=self._run_settings(p.get('library_id'));source=None;force=None
        if mode not in ('continue','link','latest','all','verify','force','enrich'):raise ValueError('下载操作无效')
        if mode in ('latest','all'):
            if not self._credentials.get('auth_token'):raise ValueError('请先保存或导入 X 登录凭证')
            source=likes_url(settings['username'])
        if mode=='link':source=validate_source_url(p.get('url',''))
        if mode in ('force','enrich'):
            rows=self._store.get_many([int(p['job_id'])])
            if not rows:raise ValueError('找不到媒体记录')
            if mode=='force' and self._store.is_ignored(rows[0]['tweet_id']):raise ValueError('请先取消忽略')
            settings['output']=rows[0]['output'];force=rows[0]['id'] if mode=='force' else None
        run_id=self._operation.begin(reading=bool(source) or mode=='enrich',label='补全预览' if mode=='enrich' else '校验' if mode=='verify' else '下载')
        emit=lambda event:self._emit({**event,'run_id':run_id})
        self._runner=Runner(self._store,settings,self._credentials,emit)
        kwargs={'source':source,'limit':50 if mode=='latest' else 0,'recheck':mode=='verify','force_id':force}
        target=(lambda:self._runner.enrich(rows[0])) if mode=='enrich' else (lambda:self._runner.run(**kwargs))
        self._worker=threading.Thread(target=target,daemon=True);self._worker.start();return True

    def _organize(self,p):
        if self._busy():raise ValueError('请先暂停下载')
        library=self._libraries.get(p.get('library_id'));output=Path(library['path']);target=output/'集中媒体'
        search=None
        if p.get('search'):
            search=self._dispatch('choose_folder',{})
            if not search:return False
        if p.get('custom_target'):
            selected=self._dispatch('choose_folder',{})
            if not selected:return False
            target=Path(selected)
        cancel=threading.Event();self._runner=type('Cancellation',(),{'cancel':cancel})()
        emit=lambda event:self._emit({**event,'scope':'organize'})
        def work():
            try:
                rows=self._store.completed_for_output(output)
                with self._libraries.lease(target/'capacity.tmp') as lease:
                    lease.reserve(sum(r['bytes'] for r in rows)+16*1024**2)
                    result=rollback_completed(self._store,output,cancel,emit,search=search) if p.get('rollback') else flatten_completed(self._store,output,target,cancel,emit)
                emit({'type':'message','text':f"整理结束：移动 {result['moved']} 个，跳过 {result['skipped']} 个"})
            except Exception as exc:emit({'type':'message','text':redact(str(exc),self._credentials.values())})
            finally:emit({'type':'finished'})
        self._worker=threading.Thread(target=work,daemon=True);self._worker.start();return True

    def _snapshot(self,p):
        self._store.catalog.ensure_ready()
        output=self._libraries.get(p['library_id'])['path'] if p.get('library_id') else None
        collection=bool(p.get('collection'))
        if collection and output is None:raise ValueError('归档浏览需要媒体库')
        params=dict(query=str(p.get('query',''))[:200],output=output,tags=p.get('tags',[]),untagged=bool(p.get('untagged',False)),offset=max(0,int(p.get('offset',0))),limit=min(100,max(1,int(p.get('limit',50)))))
        if collection and p.get('view')=='authors':
            page={**self._store.catalog.authors_page(**params,library_id=p['library_id'],group=p.get('author_group','root')),'tasks':[],'counts':dict(all=0,pending=0,completed=0,ignored=0)}
        else:
            page=self._store.catalog.page(**params,filter=p.get('filter','all'),saved_only=collection,author_key=str(p.get('author_key',''))[:250] if collection else '')
        if collection:page['collection_author_count']=self._store.catalog.authors_page(output,limit=1)['total']
        for task in page['tasks']:
            for item in task['media']:item['error']=redact(item['error'],self._credentials.values())
        libraries=self._libraries.snapshot(cached_only=True)
        for library in libraries:
            library.update(self._store.catalog.library_counts(library['path']))
            if library['id'] in self._relocation_errors:library.update(online=False,error=self._relocation_errors[library['id']])
        return {**page,'version':__version__,'settings':{k:self._settings[k] for k in ('privacy_mode','theme','accent','username','quality','connections','max_mb','media_mode','request_strategy')},'has_credentials':bool(self._credentials.get('auth_token')),'credential_error':self._credential_error,'libraries':libraries,'default_library':self._libraries.default_id,'tag_options':self._store.catalog.tags(output,saved_only=collection),'operation':self._operation.snapshot(),'busy':self._busy()}

    def _asset(self,action,p):
        if not self._preview_lock.acquire(blocking=False):return None
        try:
            if action=='cover':return self._assets.cover(self._libraries.get(p['id']).get('cover'))
            author=self._store.catalog.author(p['id'])
            if not author.get('avatar_url'):return None
            return self._assets.avatar(author['avatar_url'],self._credentials.get('proxy',''),self._cancel)
        except Exception:return None
        finally:self._preview_lock.release()

    def _preview(self,p):
        if not self._preview_lock.acquire(blocking=False):return None
        try:
            rows=self._store.get_many([int(p['job_id'])])
            if not rows:return None
            image=self._cache.load(rows[0],self._credentials.get('proxy',''),self._cancel,(900,600))
            try:
                buffer=BytesIO();image.save(buffer,format='JPEG',quality=85)
                return 'data:image/jpeg;base64,'+base64.b64encode(buffer.getvalue()).decode('ascii')
            finally:image.close()
        except Exception:return None
        finally:self._preview_lock.release()

    def _close(self):
        if self._busy():
            self._runner.cancel.set();self._worker.join(timeout=1)
            if self._worker.is_alive():return False
        self._closed=True;self._cancel.set();self._store.close();return True
