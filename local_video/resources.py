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
        return "j.state='completed' AND j.output IN ("+','.join('?' for _ in paths)+')',paths

    def summary(self, outputs):
        scope,args=self._scope(outputs)
        with self.lock:
            rows=self.db.execute('''SELECT media_type,COUNT(*) count FROM (
                SELECT DISTINCT tweet_id,media_type,media_id FROM jobs j WHERE '''+scope+''') GROUP BY media_type''',args).fetchall()
            counts={r['media_type']:r['count'] for r in rows}
            return {'video_count':counts.get('video',0),'image_count':counts.get('image',0)}

    def page(self, outputs, kind='all', query='', tags=None, offset=0, limit=50):
        if kind not in ('all','video','image'):raise ValueError('媒体类型无效')
        if not isinstance(tags or [],(list,tuple)) or len(tags or [])>20:raise ValueError('标签筛选无效')
        scope,args=self._scope(outputs)
        if kind!='all':scope+=' AND j.media_type=?';args.append(kind)
        for word in str(query)[:200].split():
            scope+=" AND (j.tweet_text LIKE ? ESCAPE '\\' OR j.local_path LIKE ? ESCAPE '\\' OR (j.tweet_id || '_' || j.media_id || '.' || j.extension) LIKE ? ESCAPE '\\' OR j.media_id LIKE ? ESCAPE '\\')"
            escaped=word.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
            args.extend(['%'+escaped+'%']*4)
        for tag in set(normalize_tag(t)[0] for t in (tags or [])):
            scope+=" AND EXISTS(SELECT 1 FROM catalog_message_tags mt WHERE mt.platform='x' AND mt.message_id=j.tweet_id AND mt.tag=?)"
            args.append(tag)
        cte='''WITH matched AS (SELECT DISTINCT r.* FROM jobs j JOIN resource_entries r
            ON r.platform='x' AND r.message_id=j.tweet_id AND r.media_type=j.media_type AND r.media_id=j.media_id
            WHERE '''+scope+') '
        offset,limit=max(0,int(offset)),min(100,max(1,int(limit)))
        with self.lock:
            total=self.db.execute(cte+'SELECT COUNT(*) FROM matched',args).fetchone()[0]
            if offset>=total:offset=max(0,(total-1)//limit)*limit
            page=self.db.execute(cte+'SELECT * FROM matched ORDER BY seq DESC LIMIT ? OFFSET ?',[*args,limit,offset]).fetchall()
            if not page:return {'resources':[],'total':total,'offset':offset}
            # One batched locations query; query matching picks resources, not versions.
            seqs=[r['seq'] for r in page]
            locations_scope,location_args=self._scope(outputs)
            rows=self.db.execute('''SELECT j.*,r.seq FROM jobs j JOIN resource_entries r
                ON r.platform='x' AND r.message_id=j.tweet_id AND r.media_type=j.media_type AND r.media_id=j.media_id
                WHERE '''+locations_scope+' AND r.seq IN ('+','.join('?' for _ in seqs)+') ORDER BY COALESCE(j.width,0)*COALESCE(j.height,0) DESC,j.bytes DESC,j.id DESC',[*location_args,*seqs]).fetchall()
            by_seq={}
            for row in rows:by_seq.setdefault(row['seq'],[]).append(row)
            ids=list(dict.fromkeys(r['message_id'] for r in page))
            labels={}
            for tag in self.db.execute('''SELECT mt.message_id,t.name FROM catalog_message_tags mt
                JOIN catalog_tags t ON t.normalized=mt.tag WHERE mt.platform='x' AND mt.message_id IN ('''+','.join('?' for _ in ids)+') ORDER BY t.normalized',ids):
                labels.setdefault(tag['message_id'],[]).append(tag['name'])
            resources=[]
            for entry in page:
                versions=by_seq[entry['seq']];head=versions[0]
                locations=[{**{k:r[k] for k in MEDIA_FIELDS},'filename':Path(r['local_path']).name if r['local_path'] else f"{r['tweet_id']}_{r['media_id']}.{r['extension']}"} for r in versions]
                resources.append({'key':json.dumps([entry[k] for k in ('platform','message_id','media_type','media_id')],separators=(',',':')),
                    'platform':entry['platform'],'source_label':'X','message_id':entry['message_id'],
                    'media_id':entry['media_id'],'media_type':entry['media_type'],'added_at':entry['added_at'],
                    'title':next((line.strip() for line in head['tweet_text'].splitlines() if line.strip()),locations[0]['filename']),
                    'author':head['author'],'tags':labels.get(entry['message_id'],[]),'locations':locations})
            return {'resources':resources,'total':total,'offset':offset}
