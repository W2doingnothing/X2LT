# SPDX-License-Identifier: GPL-2.0-only
"""Desktop local media import and comic reading."""
import base64
from collections import OrderedDict
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import threading
from uuid import uuid4

from ..archive import original_path
from ..cloud_catalog import safe_filename
from ..images import is_alias, read_image
from ..local_collection import LocalCollection, file_signature, path_key
from ..local_import_files import copy_into, fingerprint, read_page, scan, scan_root, selected_path, validate_file_name
from ..previews import PreviewCache
from ..removal import recycle_file, recycle_verified
from ..transfer import Paused
from ..windows_thumbnail import windows_thumbnail


class LocalLibraryService:
    def __init__(self, data, store, libraries, native, runtime):
        self.collection = LocalCollection(store)
        self.libraries, self.native, self.runtime = libraries, native, runtime
        self.cover_directory = Path(data) / 'comic-covers'
        self.preview_directory = Path(data) / 'local-thumbnails'
        self.preview_cache = None
        self.picks = OrderedDict()
        self.jobs = OrderedDict()
        self.lock = threading.RLock()
        self.preview_slots = threading.BoundedSemaphore(3)
        self.reader_slots = threading.BoundedSemaphore(2)

    def notify(self):
        self.runtime.emit({'type': 'changed'})

    def pick(self, p):
        if p['kind'] not in ('media','comic') or p['selection'] not in ('files','folder'):
            raise ValueError('文件选择类型无效')
        paths = self.native.choose_local_import(p['kind'], p['selection'])
        if not paths:
            return None
        paths = tuple(selected_path(path) for path in paths)
        identifier = uuid4().hex
        with self.lock:
            self.picks[identifier] = p['kind'], paths
            if len(self.picks) > 24:
                self.picks.popitem(last=False)
        return dict(id=identifier, names=[path.name or str(path) for path in paths])

    def job(self, identifier):
        job = self.jobs.get(identifier)
        if job is None:
            raise ValueError('找不到本地导入记录，请重新扫描')
        return job

    def launch(self, job, target):
        job['thread'] = threading.Thread(target=target, args=(job,),
                                         name='x2lt-local-import', daemon=True)
        job['thread'].start()

    def scan(self, p):
        library = self.libraries.get(p['library_id'])
        if p['storage'] not in ('reference','copy'):
            raise ValueError('入库方式无效')
        with self.lock:
            selection = self.picks.get(p['selection'])
            if not selection or selection[0] != p['kind']:
                raise ValueError('请用文件选择器重新选择媒体')
            return self.begin_scan(library['id'], p, selection[1])

    def scan_library(self, p):
        library = self.libraries.get(p['library_id'])
        root = selected_path(library['path'])
        if not root.is_dir():
            raise ValueError('媒体库目录不可用')
        with self.lock:
            return self.begin_scan(library['id'], dict(
                kind=library.get('scan_mode','media'),storage='reference',library_scan=True,
                include_loose=p.get('include_loose',False)), (root,))

    def begin_scan(self, library, options, paths):
        if any(j['stage'] in ('scanning','importing') for j in self.jobs.values()):
            raise ValueError('请等待当前本地导入任务完成')
        identifier = uuid4().hex
        job = dict(id=identifier,stage='scanning',total=0,done=0,bytes=0,copy_bytes=0,duplicate=0,
                   already=0,missing=0,excluded=0,library=library,options=dict(options),paths=paths,
                   entries={},failures=[],error='',cancel=threading.Event())
        self.jobs[identifier] = job
        if len(self.jobs) > 12:
            self.jobs.popitem(last=False)
        self.launch(job, self.do_scan)
        return {'id': identifier}

    def protected_downloads(self, root):
        paths, digests = set(), set()
        for row in self.collection.rows('SELECT * FROM jobs WHERE output=?', (str(root),)):
            if row.get('sha256'):
                digests.add(row['sha256'])
            for value in (row.get('local_path'), row.get('relocation_source')):
                if value:
                    paths.add(Path(value))
            try:
                paths.add(original_path(row))
            except (ValueError, OSError):
                continue
        for row in self.collection.rows("SELECT local_path,sha256 FROM model_locations WHERE local_path<>''"):
            path = Path(row['local_path'])
            if root in path.parents:
                paths.add(path)
                if row['sha256']:
                    digests.add(row['sha256'])
        return paths, digests

    def validate_scan_target(self, job, library):
        if job['options'].get('library_scan') and (
                selected_path(library['path']) != job['paths'][0]
                or library.get('scan_mode','media') != job['options']['kind']):
            raise ValueError('媒体库路径或扫描模式已更改，请重新扫描')

    def do_scan(self, job):
        p = job['options']
        paths = job['paths']
        seen = set()
        directory_scan = p.get('library_scan', False)

        def scan_error(path, exc):
            with self.lock:
                job['total'] += 1
                job['done'] += 1
                if job['total'] > 25000:
                    raise ValueError('本次导入超过 25000 项，请分批选择')
                job['failures'].append(dict(id=uuid4().hex,title=path.name,error=self.public_error(exc)))

        def skip():
            with self.lock:
                job['excluded'] += 1

        try:
            protected_hashes = set()
            if directory_scan:
                protected_paths, protected_hashes = self.protected_downloads(paths[0])
                source = scan_root(paths[0],p['kind'],p['include_loose'],job['cancel'],scan_error,
                                   protected_paths.__contains__,skip)
                missing = sum(self.collection.state(row) != 'available' for row in self.collection.rows(
                    """SELECT l.* FROM local_locations l JOIN local_memberships m ON m.entry=l.entry
                       WHERE l.owner=? AND m.library=?""", (job['library'],job['library'])))
                with self.lock:
                    job['missing'] = missing
            else:
                source = scan(paths,p['kind'],p.get('recursive',False),p.get('batch',False),job['cancel'],scan_error)
            for item in source:
                if job['cancel'].is_set():
                    raise Paused('扫描已取消')
                with self.lock:
                    if job['total'] >= 25000:
                        raise ValueError('本次导入超过 25000 项，请分批选择')
                    job['total'] += 1
                path = Path(item['path'])
                identifier = uuid4().hex
                try:
                    signature = file_signature(path)
                    content_hash = self.collection.known(path, item['kind'], signature) if path.is_file() else None
                    content_hash = content_hash or fingerprint(path, item['kind'], item['pages'], job['cancel'])
                    if signature != file_signature(path):
                        raise ValueError('扫描时源文件发生变化')
                except Paused:
                    raise
                except (OSError, ValueError) as exc:
                    with self.lock:
                        job['failures'].append(dict(id=identifier,title=item['title'],error=self.public_error(exc)))
                        job['done'] += 1
                    continue
                identity = (item['kind'], content_hash)
                first = identity not in seen
                prior = self.collection.by_digest(*identity)
                if directory_scan and (content_hash in protected_hashes or (
                        prior and self.collection.excluded(job['library'], prior['id']))):
                    with self.lock:
                        job['total'] -= 1
                    skip()
                    continue
                already = bool(prior and directory_scan and self.collection.one(
                    'SELECT 1 FROM local_memberships WHERE library=? AND entry=?', (job['library'],prior['id'])))
                duplicate = bool(prior) or not first
                seen.add(identity)
                approved = next((root for root in paths if root.is_dir() and (root == path or root in path.parents)), path.parent)
                needs_copy = p['storage'] == 'copy' and first and not self.managed_copy(prior, job['library'])
                with self.lock:
                    job['entries'][identifier] = dict(item, id=identifier, duplicate=duplicate, digest=content_hash,
                                                      signature=signature, approved=approved)
                    job['done'] += 1
                    job['duplicate'] += int(duplicate)
                    job['already'] += int(already)
                    job['bytes'] += item['bytes']
                    if needs_copy:
                        job['copy_bytes'] += item['bytes']
            with self.lock:
                job['stage'] = 'cancelled' if job['cancel'].is_set() else 'preview'
        except Paused:
            with self.lock:
                job['stage'] = 'cancelled'
        except (OSError, ValueError) as exc:
            with self.lock:
                job['error'], job['stage'] = self.public_error(exc), 'failed'
        except Exception:
            with self.lock:
                job['error'], job['stage'] = '无法完成扫描，请检查文件格式和访问权限', 'failed'

    @staticmethod
    def public_error(exc):
        return str(exc) if isinstance(exc, ValueError) else '文件不可读或目标磁盘不可用'

    def status(self, p):
        with self.lock:
            job = self.job(p['id'])
            retryable = (len(job['failures']) + len(job['pending'][job['done']:])
                         if job.get('pending') else int(job['stage'] in ('failed','cancelled')))
            return {k: job[k] for k in ('id','stage','total','done','bytes','copy_bytes','duplicate','error',
                                      'already','missing','excluded')} | dict(
                failed=len(job['failures']), failures=job['failures'][:500],
                retryable=retryable,
                items=[{k: item[k] for k in ('id','title','kind','bytes','duplicate')}
                       for item in list(job['entries'].values())[:500]])

    def start(self, p):
        with self.lock:
            job = self.job(p['id'])
            if job['stage'] != 'preview':
                raise ValueError('请等待扫描完成')
            self.validate_scan_target(job,self.libraries.get(job['library']))
            identifiers = list(dict.fromkeys(p.get('ids', job['entries'])))
            if not identifiers or any(i not in job['entries'] for i in identifiers):
                raise ValueError('请选择可入库的媒体')
            self.begin_copy(job, identifiers)
        return True

    def begin_copy(self, job, identifiers):
        if any(other is not job and other['stage'] in ('scanning','importing') for other in self.jobs.values()):
            raise ValueError('请等待当前本地导入任务完成')
        job.update(stage='importing', done=0, total=len(identifiers), pending=identifiers,
                   failures=[], error='', cancel=threading.Event())
        self.launch(job, self.do_import)

    def cancel(self, p):
        with self.lock:
            job = self.job(p['id'])
            if job['stage'] not in ('preview','scanning','importing'):
                raise ValueError('此任务已结束')
            job['cancel'].set()
            if job['stage'] == 'preview':
                job['stage'] = 'cancelled'
        return True

    def retry(self, p):
        with self.lock:
            job = self.job(p['id'])
            if job['stage'] not in ('failed','completed','cancelled'):
                raise ValueError('当前任务仍在运行')
            if not job.get('pending'):
                if any(other is not job and other['stage'] in ('scanning','importing') for other in self.jobs.values()):
                    raise ValueError('请等待当前本地导入任务完成')
                job.update(stage='scanning',total=0,done=0,bytes=0,copy_bytes=0,duplicate=0,entries={},failures=[],
                           already=0,missing=0,excluded=0,
                           error='',cancel=threading.Event())
                self.launch(job, self.do_scan)
                return True
            retryable = list(dict.fromkeys([f['id'] for f in job['failures']] + job['pending'][job['done']:]))
            identifiers = [p['item_id']] if p.get('item_id') else retryable
            if not identifiers or any(i not in retryable or i not in job['entries'] for i in identifiers):
                raise ValueError('没有可重试的入库项目')
            retained = [f for f in job['failures'] if f['id'] not in identifiers]
            recorded = {f['id'] for f in retained}
            retained.extend(dict(id=i,title=job['entries'][i]['title'],error='尚未导入')
                            for i in job['pending'][job['done']:] if i not in identifiers and i not in recorded)
            self.begin_copy(job, identifiers)
            job['failures'].extend(retained)
        return True

    def do_import(self, job):
        try:
            protected = self.protected_downloads(job['paths'][0]) if job['options'].get('library_scan') else None
        except Exception as exc:
            with self.lock:
                job['error'], job['stage'] = self.public_error(exc), 'failed'
            return
        for identifier in job['pending']:
            if job['cancel'].is_set():
                break
            item = job['entries'][identifier]
            try:
                self.import_item(job, item, protected)
            except Paused as exc:
                if job['cancel'].is_set():
                    break
                with self.lock:
                    job['failures'].append(dict(id=identifier,title=item['title'],error=str(exc)))
            except Exception as exc:
                with self.lock:
                    job['failures'].append(dict(id=identifier,title=item['title'],error=self.public_error(exc)))
            with self.lock:
                job['done'] += 1
        with self.lock:
            job['stage'] = 'cancelled' if job['cancel'].is_set() else 'failed' if job['failures'] else 'completed'
        self.notify()

    def managed_copy(self, entry, library):
        return entry and any(row['managed'] and not row['scanned'] and row['owner'] == library
                             and self.collection.state(row) == 'available'
                             for row in self.collection.location_rows(entry['id']))

    def import_item(self, job, item, protected=None):
        library = self.libraries.get(job['library'])
        self.validate_scan_target(job,library)
        path = Path(item['path'])
        if path.resolve(strict=True) != path or is_alias(path) or file_signature(path) != item['signature']:
            raise ValueError('预览后文件发生变化，请重新扫描')
        copy = job['options']['storage'] == 'copy'
        if not copy:
            if path.is_dir() and fingerprint(path,item['kind'],item['pages'],job['cancel']) != item['digest']:
                raise ValueError('预览后漫画页面发生变化，请重新扫描')
            scanned = job['options'].get('library_scan', False)
            if scanned:
                if protected and (path in protected[0] or item['digest'] in protected[1]):
                    return
                entry = self.collection.by_digest(item['kind'],item['digest'])
                if entry and self.collection.excluded(library['id'],entry['id']):
                    return
            self.collection.add(item,item['digest'],path,item['approved'],library['id'],scanned,scanned)
            return
        prior = self.collection.by_digest(item['kind'], item['digest'])
        if self.managed_copy(prior, library['id']):
            self.collection.set_link(prior['id'], library['id'], True)
            return
        root = Path(library['path'])
        name = '漫画页面' if path.is_dir() else safe_filename(item['title'], item['extension'])
        parent = root / 'X2LT本地' / item['digest']
        dest = parent / name
        with self.libraries.lease(dest) as lease:
            if parent.resolve() != parent or is_alias(parent):
                raise ValueError('目标媒体库路径无效')
            parent.mkdir(parents=True, exist_ok=True)
            if dest.exists() or is_alias(dest):
                dest = dest.with_name(f'{dest.stem}-{uuid4().hex[:10]}{dest.suffix}')
            lease.reserve(item['bytes'])
            staging = parent / ('.incoming-' + uuid4().hex)
            try:
                confirmed = copy_into(path, item['kind'], item['pages'], staging, job['cancel'])
                if job['cancel'].is_set():
                    raise Paused('导入已取消')
                if confirmed != item['digest'] or parent.resolve() != parent or dest.exists() or is_alias(dest):
                    raise ValueError('复制内容或目标已改变')
                staging.rename(dest)
            finally:
                if staging.is_relative_to(root) and staging.resolve() == staging and not is_alias(staging):
                    if staging.is_file():
                        staging.unlink()
                    elif staging.is_dir():
                        shutil.rmtree(staging)
            self.collection.add(item, item['digest'], dest, root, library['id'], True)

    def page(self, p):
        library = self.libraries.get(p['library_id'])
        return self.collection.page(library['id'],p.get('query',''),p.get('tags',[]),
                                    p.get('untagged',False),p.get('offset',0),p.get('limit',40))

    def tags(self, p):
        self.collection.set_tags(p['id'],p['tags'])
        self.notify()
        return True

    def scan_exclusions(self, p):
        library = self.libraries.get(p['library_id'])
        return self.collection.exclusions(library['id'])

    def scan_restore(self, p):
        library = self.libraries.get(p['library_id'])
        self.collection.restore(library['id'],p['id'])
        self.notify()
        return True

    def link(self, p):
        library = self.libraries.get(p['library_id'])
        self.collection.set_link(p['id'],library['id'],p['linked'])
        self.notify()
        return True

    def open(self, p):
        entry = self.collection.entry(p['id'])
        row = self.collection.get_location(entry['id'], p.get('location_id'))
        if not row:
            raise ValueError('本地文件不可用，请重新定位')
        path = Path(row['path'])
        if entry['kind'] == 'comic' and not p.get('folder'):
            raise ValueError('请使用漫画阅读器打开')
        if p.get('folder'):
            path = path if path.is_dir() else path.parent
        return self.native.open_file(path)

    def relocate(self, p):
        entry = self.collection.entry(p['id'])
        row = self.collection.one('SELECT * FROM local_locations WHERE id=? AND entry=?',
                                  (p['location_id'], entry['id']))
        if not row:
            raise ValueError('找不到需要重新定位的文件')
        picked = self.native.choose_local_import('comic' if entry['kind']=='comic' else 'media',
                                                 'folder' if row['directory'] else 'files', single=True)
        if not picked:
            return False
        path = selected_path(picked[0])
        matched = next(iter(scan([path],'comic' if entry['kind']=='comic' else 'media', True)), None)
        if not matched or matched['kind'] != entry['kind']:
            raise ValueError('选中的文件不是同类媒体')
        pages = json.loads(entry['pages']) if entry['kind']=='comic' else []
        if fingerprint(path,entry['kind'],pages,self.runtime.cancel) != entry['digest']:
            raise ValueError('选中文件内容不同，原收藏保持不变')
        with self.collection.lock, self.collection.db:
            current = self.collection.one('SELECT * FROM local_locations WHERE path_key=?', (path_key(path),))
            if current and current['entry'] != entry['id']:
                raise ValueError('目标位置已关联到其他媒体')
            if current and current['id'] != row['id']:
                self.collection.db.execute(
                    'UPDATE model_locations SET local_path=? WHERE id IN '
                    '(SELECT model_location FROM telegram_saved WHERE local_location=?)',
                    (str(path), row['id']))
                self.collection.db.execute('UPDATE telegram_saved SET local_location=? WHERE local_location=?',
                                           (current['id'], row['id']))
                self.collection.db.execute('DELETE FROM local_locations WHERE id=?', (row['id'],))
            else:
                owner = row['owner'] if current else None
                root = Path(row['root']) if current else path if path.is_dir() else path.parent
                self.collection.put_location(entry['id'],path,root,owner,row['id'],bool(current and row['scanned']))
        self.notify()
        return True

    def owned_path(self, p):
        library = self.libraries.get(p['library_id'])
        entry = self.collection.entry(p['id'])
        row = self.collection.get_location(entry['id'],p['location_id'])
        membership = self.collection.one('SELECT 1 FROM local_memberships WHERE library=? AND entry=?',
                                         (library['id'],entry['id']))
        if not membership or not row or not row['managed'] or row['owner'] != library['id']:
            raise ValueError('只能管理当前媒体库的文件')
        root = selected_path(library['path'])
        path = selected_path(row['path'])
        if root not in path.parents:
            raise ValueError('文件不在当前媒体库路径内')
        protected, _ = self.protected_downloads(root)
        if path in protected or (row['directory'] and any(path in item.parents for item in protected)):
            raise ValueError('文件包含下载任务管理的媒体，请在下载任务中整理')
        ancestors = [path_key(parent) for parent in path.parents]
        placeholders = ','.join('?' for _ in ancestors)
        comics = self.collection.rows("""
            SELECT l.*,e.pages FROM local_locations l JOIN local_entries e ON e.id=l.entry
            WHERE l.directory=1 AND e.kind='comic' AND l.path_key IN (""" + placeholders + """)
            AND EXISTS (SELECT 1 FROM local_memberships m WHERE m.entry=e.id)
            """, ancestors)
        for comic in comics:
            if self.collection.state(comic) != 'available':
                continue
            folder = Path(comic['path'])
            if any((folder / page['id']).is_relative_to(path) for page in json.loads(comic['pages'])):
                raise ValueError('此文件属于已入库漫画，请从漫画卡片管理整个目录')
        return library['id'], root, path, entry, row, protected

    def manage_file(self, p):
        library, root, path, entry, row, protected = self.owned_path(p)
        if p.get('move'):
            picked = self.native.choose_folder()
            if not picked:
                return False
            folder = selected_path(picked)
        else:
            folder = path.parent
        if not folder.is_dir() or (folder != root and root not in folder.parents):
            raise ValueError('只能移动到当前媒体库内的文件夹')
        name = validate_file_name(p.get('name',path.name),path,row['directory'])
        target = folder / name
        if str(target) == str(path):
            return True
        if path in target.parents or is_alias(target) or (path_key(target) != path_key(path) and target.exists()):
            raise ValueError('目标位置不可用或已有同名文件')
        if target in protected or (row['directory'] and any(target in item.parents for item in protected)):
            raise ValueError('目标位置已由下载任务占用')

        with self.lock:
            if any(job['stage'] in ('importing','scanning') for job in self.jobs.values()):
                raise ValueError('请等待当前本地导入任务完成')
            with self.libraries.lease(target), self.collection.lock:
                if selected_path(self.libraries.get(library)['path']) != root or self.collection.state(row) != 'available':
                    raise ValueError('源文件或媒体库位置已变化，请重新扫描')
                pages = json.loads(entry['pages']) if row['directory'] else []
                if fingerprint(path,entry['kind'],pages) != entry['digest']:
                    raise ValueError('当前文件内容已变化，请重新扫描')
                if selected_path(folder) != folder or is_alias(target) or (
                        path_key(target) != path_key(path) and target.exists()):
                    raise ValueError('目标位置不可用或已有同名文件')
                candidates = [row]
                if row['directory']:
                    prefix = path_key(path) + os.sep
                    candidates.extend(candidate for candidate in self.collection.rows(
                        'SELECT * FROM local_locations WHERE substr(path_key,1,?)=?', (len(prefix),prefix))
                        if self.collection.state(candidate) == 'available')
                changes = []
                for candidate in candidates:
                    old = Path(candidate['path'])
                    moved = target / old.relative_to(path)
                    if self.collection.one('SELECT 1 FROM local_locations WHERE path_key=? AND id<>?',
                                           (path_key(moved),candidate['id'])):
                        raise ValueError('目标位置已有关联记录，请先重新定位')
                    changes.append((candidate,moved))
                path.rename(target)
                try:
                    with self.collection.db:
                        for candidate, moved in changes:
                            self.collection.put_location(candidate['entry'],moved,root,candidate['owner'],
                                                         candidate['id'],bool(candidate['scanned']))
                except Exception:
                    target.rename(path)
                    raise
        self.notify()
        return True

    def remove(self, p):
        library = self.libraries.get(p['library_id'])
        if not p.get('recycle'):
            self.collection.entry(p['id'])
            self.collection.exclude(library['id'],p['id'])
            self.notify()
            return True
        if not p.get('location_id'):
            raise ValueError('请选择一个要移入回收站的文件')
        _, _, path, entry, row, _ = self.owned_path(p)
        if row['directory']:
            raise ValueError('文件夹漫画须通过资源管理器删除')
        active_libraries = {item['id'] for item in self.libraries.items}
        other = any(member['library'] in active_libraries for member in self.collection.rows(
            'SELECT library FROM local_memberships WHERE entry=? AND library<>?', (entry['id'],library['id'])))
        if other and not any(candidate['id'] != row['id'] and self.collection.state(candidate) == 'available'
                             for candidate in self.collection.location_rows(entry['id'])):
            raise ValueError('其他媒体库仍引用这个唯一副本，请先移除关联或保存另一份')
        recycle_verified(path, entry['bytes'], entry['digest'], recycle_file)
        with self.collection.lock, self.collection.db:
            self.collection.db.execute('DELETE FROM local_locations WHERE id=?',(row['id'],))
        if self.collection.get_location(entry['id']) is None:
            self.collection.exclude(library['id'],entry['id'])
        self.notify()
        return True

    @staticmethod
    def data_image(image):
        with image:
            buffer = BytesIO()
            image.save(buffer, 'JPEG', quality=85)
        return 'data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')

    def clear_previews(self):
        self.local_preview_cache().trim(clear=True)

    def local_preview_cache(self):
        with self.lock:
            if self.preview_cache is None:
                self.preview_cache = PreviewCache(self.preview_directory,max_bytes=128 * 1024**2)
            return self.preview_cache

    def local_raster(self, entry, row, size):
        path = Path(row['path'])
        key = sha256(f"local-v1:{entry['kind']}:{entry['digest']}:{path.suffix.lower()}".encode()).hexdigest()

        def generate():
            if self.collection.state(row) != 'available':
                return None
            image = (windows_thumbnail(path,(960,960)) if entry['kind'] == 'video'
                     else read_image(path,size=(1600,1600)))
            if image is not None and self.collection.state(row) != 'available':
                image.close()
                return None
            return image

        image = self.local_preview_cache().load_generated(key,generate,self.runtime.cancel,size)
        if image is None:
            return None
        if self.collection.state(row) != 'available':
            image.close()
            return None
        return self.data_image(image)

    def preview(self, p):
        if not self.preview_slots.acquire(blocking=False):
            return None
        try:
            entry = self.collection.entry(p['id'])
            size = (1600,1600) if p.get('large') else (480,480)
            if entry['kind'] == 'comic':
                if entry['cover'] == '__custom__':
                    cover = self.cover_directory / (str(entry['id']) + '.jpg')
                    if cover.is_file() and not is_alias(cover):
                        return self.data_image(read_image(cover,size=size))
                pages = self.comic_state(entry)['pages']
                if not pages:
                    return None
                return self.comic_page(dict(id=entry['id'],page_id=entry['cover'] if entry['cover'] not in (None,'__custom__')
                                            else pages[0]['id'],thumbnail=not p.get('large')))
            row = self.collection.get_location(entry['id'], p.get('location_id'))
            if not row:
                return None
            return self.local_raster(entry,row,size)
        except (OSError, ValueError, Paused):
            return None
        finally:
            self.preview_slots.release()

    def comic_state(self, entry):
        if entry['kind'] != 'comic':
            raise ValueError('该媒体不是漫画')
        original = json.loads(entry['pages'])
        pages = {page['id']:page for page in original}
        order = json.loads(entry['page_order']) or list(pages)
        return dict(id=entry['id'],title=entry.get('display_title') or entry['title'],pages=[pages[key] for key in order if key in pages],
                    page=entry['page'],spread=bool(entry['spread']),rtl=bool(entry['rtl']),cover=entry['cover'])

    def comic_get(self, p):
        return self.comic_state(self.collection.entry(p['id']))

    def comic_page(self, p):
        entry = self.collection.entry(p['id'])
        state = self.comic_state(entry)
        if not any(page['id'] == p['page_id'] for page in state['pages']):
            raise ValueError('找不到该漫画页面')
        size = (360,360) if p.get('thumbnail') else (1800,1800)
        with self.reader_slots:
            for row in self.collection.location_rows(entry['id']):
                if self.collection.state(row) != 'available':
                    continue
                try:
                    return self.data_image(read_page(Path(row['path']), p['page_id'], size))
                except (OSError, ValueError):
                    continue
        raise ValueError('漫画页面无法读取，文件可能已移动或发生变化')

    def comic_update(self, p):
        identifier = p['id']
        self.comic_get(p)
        if p.get('custom_cover'):
            source = self.native.choose_image()
            if not source:
                return self.comic_get(p)
            with read_image(source, size=(960,960)) as image:
                self.cover_directory.mkdir(parents=True,exist_ok=True)
                if self.cover_directory.resolve() != self.cover_directory or is_alias(self.cover_directory):
                    raise ValueError('漫画封面目录无效')
                temp = self.cover_directory / (uuid4().hex + '.tmp')
                try:
                    with temp.open('xb') as output:
                        image.save(output,'JPEG',quality=86)
                    temp.replace(self.cover_directory / (str(identifier) + '.jpg'))
                finally:
                    temp.unlink(missing_ok=True)
        with self.collection.lock, self.collection.db:
            entry = self.collection.entry(identifier)
            state = self.comic_state(entry)
            keys = [page['id'] for page in state['pages']]
            order = p.get('order')
            if order and (len(order) != len(keys) or set(order) != set(keys)):
                raise ValueError('请保留每张漫画页面一次')
            updates = {name:int(p[name]) for name in ('spread','rtl') if name in p}
            if 'page' in p:
                updates['page'] = min(max(0, p['page']),max(0,len(keys)-1))
            if order is not None:
                previous = keys[min(state['page'], len(keys)-1)] if keys else None
                new_order = order or [page['id'] for page in json.loads(entry['pages'])]
                updates.update(page_order=json.dumps(order),
                               page=new_order.index(previous) if previous in new_order else 0)
            if 'cover' in p:
                if p['cover'] and p['cover'] not in keys:
                    raise ValueError('找不到要设为封面的页面')
                updates['cover'] = p['cover'] or None
            if p.get('custom_cover'):
                updates['cover'] = '__custom__'
            if updates:
                sql = ','.join(name+'=?' for name in updates)
                self.collection.db.execute('UPDATE local_entries SET '+sql+' WHERE id=?',(*updates.values(),identifier))
        return self.comic_get(p)

    def close(self):
        with self.lock:
            active = [job for job in self.jobs.values() if job['stage'] in ('scanning','importing')]
            for job in active:
                job['cancel'].set()
        return not active
