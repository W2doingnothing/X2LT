# SPDX-License-Identifier: GPL-2.0-only
"""Saved-media projection: stable identity/order, no media filesystem scans."""
import json
import time
from pathlib import Path
from .catalog import MEDIA_FIELDS, normalize_tag


class Resources:
    def __init__(self, db, lock):
        self.db,self.lock=db,lock
        with lock,db:
            db.execute('''CREATE TABLE IF NOT EXISTS resource_entries (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, platform TEXT NOT NULL,
                message_id TEXT NOT NULL, media_type TEXT NOT NULL, media_id TEXT NOT NULL,
                added_at REAL, UNIQUE(platform,message_id,media_type,media_id))''')
            if not db.execute("SELECT 1 FROM catalog_migrations WHERE name='resources-v1'").fetchone():
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='model_saved'").fetchone():
                    db.execute("DELETE FROM model_saved WHERE media_id IN (SELECT m.id FROM model_media m JOIN model_contents c ON c.id=m.content_id JOIN model_sources s ON s.id=c.source_id WHERE s.platform='x' AND s.scope='')")
                db.execute('''INSERT OR IGNORE INTO resource_entries(platform,message_id,media_type,media_id,added_at)
                    SELECT 'x',tweet_id,media_type,media_id,NULL FROM jobs WHERE state='completed'
                    GROUP BY tweet_id,media_type,media_id ORDER BY MIN(id)''')
                db.execute("INSERT INTO catalog_migrations VALUES('resources-v1',1)")

    def record(self, job_id):
        # Called inside the completed-state write transaction and Store lock.
        self.db.execute('''INSERT OR IGNORE INTO resource_entries(platform,message_id,media_type,media_id,added_at)
            SELECT 'x',tweet_id,media_type,media_id,? FROM jobs WHERE id=? AND state='completed' ''',(time.time(),job_id))

    def _scope(self, outputs):
        paths=list(dict.fromkeys(str(Path(p).resolve()) for p in outputs))
        if not paths:return '0',[]
        return "l.state='completed' AND l.output IN ("+','.join('?' for _ in paths)+')',paths

    def _base(self):
        return """ FROM model_locations l JOIN model_variants v ON v.id=l.variant_id
            JOIN model_media m ON m.id=v.media_id JOIN model_contents c ON c.id=m.content_id
            JOIN model_sources s ON s.id=c.source_id JOIN model_saved saved ON saved.media_id=m.id """

    def summary(self, outputs, include_telegram=True):
        scope,args=self._scope(outputs)
        if not include_telegram:
            scope += " AND s.platform!='telegram'"
        with self.lock:
            rows=self.db.execute('SELECT m.media_type,COUNT(DISTINCT m.id) count'+self._base()+' WHERE '+scope+' GROUP BY m.media_type',args).fetchall()
            counts={r['media_type']:r['count'] for r in rows}
            return {'video_count':counts.get('video',0),'image_count':counts.get('image',0)}

    def page(self, outputs, kind='all', query='', tags=None, offset=0, limit=50, local_tags=False):
        if kind not in ('all','video','image'):raise ValueError('媒体类型无效')
        if not isinstance(tags or [],(list,tuple)) or len(tags or [])>20:raise ValueError('标签筛选无效')
        scope,args=self._scope(outputs)
        if kind!='all':scope+=' AND m.media_type=?';args.append(kind)
        for word in str(query)[:200].split():
            scope+=" AND (c.text LIKE ? ESCAPE '\\' OR l.local_path LIKE ? ESCAPE '\\' OR (c.native_id || '_' || m.native_id || '.' || v.extension) LIKE ? ESCAPE '\\' OR m.native_id LIKE ? ESCAPE '\\')"
            escaped=word.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
            args.extend(['%'+escaped+'%']*4)
        for tag in set(normalize_tag(t)[0] for t in (tags or [])):
            match = """(s.scope='' AND EXISTS(SELECT 1 FROM catalog_message_tags mt
                WHERE mt.platform=s.platform AND mt.message_id=c.native_id AND mt.tag=?))"""
            args.append(tag)
            if local_tags:
                match += """ OR EXISTS(SELECT 1 FROM telegram_saved ts JOIN local_tags lt ON lt.entry=ts.entry
                    WHERE ts.model_location=l.id AND lt.normalized=?)"""
                args.append(tag)
            scope += ' AND (' + match + ')'
        offset,limit=max(0,int(offset)),min(100,max(1,int(limit)))
        with self.lock:
            total=self.db.execute('SELECT COUNT(DISTINCT m.id)'+self._base()+' WHERE '+scope,args).fetchone()[0]
            if offset>=total:offset=max(0,(total-1)//limit)*limit
            entries=self.db.execute('SELECT DISTINCT m.id,m.native_id media_id,m.media_type,c.id content_id,'
                'c.native_id message_id,c.text,c.author,s.platform,s.scope,s.label,saved.seq,saved.added_at,'
                'tm.forward_title'+self._base()+' LEFT JOIN telegram_messages tm ON tm.content=c.id WHERE '+
                scope+' ORDER BY saved.seq DESC LIMIT ? OFFSET ?',[*args,limit,offset]).fetchall()
            if not entries:return {'resources':[],'total':total,'offset':offset}
            ids=[r['id'] for r in entries]
            locations_scope,location_args=self._scope(outputs)
            rows=self.db.execute('SELECT l.*,v.media_id,v.quality,v.extension,v.width,v.height,'
                'ts.entry local_entry,ts.local_location,ts.library,ts.url'+self._base()+
                ' LEFT JOIN telegram_saved ts ON ts.model_location=l.id WHERE '+locations_scope+
                ' AND m.id IN ('+','.join('?' for _ in ids)+') ORDER BY v.width*v.height DESC,l.bytes DESC,l.id DESC',[*location_args,*ids]).fetchall()
            by_media={}
            for row in rows:by_media.setdefault(row['media_id'],[]).append(row)
            tag_keys=list(dict.fromkeys((r['platform'],r['message_id']) for r in entries if not r['scope']))
            labels_by_content={}
            if tag_keys:
                conditions=' OR '.join('(mt.platform=? AND mt.message_id=?)' for _ in tag_keys)
                tag_rows=self.db.execute('SELECT mt.platform,mt.message_id,t.name FROM catalog_message_tags mt JOIN catalog_tags t ON t.normalized=mt.tag WHERE '+conditions+' ORDER BY t.normalized',[part for key in tag_keys for part in key])
                for row in tag_rows:labels_by_content.setdefault((row['platform'],row['message_id']),[]).append(row['name'])
            resources=[]
            for entry in entries:
                locations=[]
                for r in by_media[entry['id']]:
                    filename=Path(r['local_path']).name if r['local_path'] else f"{entry['message_id']}_{entry['media_id']}.{r['extension']}"
                    item = dict(id=r['legacy_job_id'] if r['legacy_job_id'] is not None else -r['id'],media_id=entry['media_id'],media_type=entry['media_type'],extension=r['extension'],state=r['state'],error=r['error'],bytes=r['bytes'],width=r['width'],height=r['height'],quality=r['quality'],output=r['output'],verification=r['verification'],filename=filename)
                    if r['local_entry']:
                        item.update(local_entry=r['local_entry'], local_location=r['local_location'],
                                    library_id=r['library'], has_source=bool(r['url']), content_id=entry['content_id'])
                    locations.append(item)
                    if local_tags and r['local_entry']:
                        labels = self.db.execute('SELECT name FROM local_tags WHERE entry=? ORDER BY normalized',
                                                 (r['local_entry'],)).fetchall()
                        labels_by_content.setdefault(entry['id'], {}).update((label['name'], None) for label in labels)
                labels=labels_by_content.get((entry['platform'],entry['message_id']),[]) if not entry['scope'] else []
                if entry['platform'] == 'telegram':
                    labels = list(labels_by_content.get(entry['id'], {}))
                telegram = entry['platform'] == 'telegram'
                fallback = {'image': '图片', 'video': '视频'}.get(entry['media_type'], '媒体') if telegram else locations[0]['filename']
                key=[entry[k] for k in ('platform','message_id','media_type','media_id')]
                if entry['scope']:key.insert(1,entry['scope'])
                resources.append(dict(key=json.dumps(key,separators=(',',':')),platform=entry['platform'],
                    source_label=(entry['forward_title'] or '') if telegram else entry['label'],
                    message_id=entry['message_id'],media_id=entry['media_id'],media_type=entry['media_type'],
                    added_at=entry['added_at'],title=next((line.strip() for line in entry['text'].splitlines() if line.strip()),fallback),
                    author='' if telegram else entry['author'] or '',tags=labels,locations=locations))
            return {'resources':resources,'total':total,'offset':offset}
