# SPDX-License-Identifier: GPL-2.0-only
"""Namespaced media relationships with transactionally synchronized X compatibility."""
from pathlib import Path
import time
from .migrations import backup_before_migration

SCHEMA='''
CREATE TABLE IF NOT EXISTS model_migrations(name TEXT PRIMARY KEY,cursor INTEGER NOT NULL DEFAULT 0,done INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS model_sources(id INTEGER PRIMARY KEY,platform TEXT NOT NULL,scope TEXT NOT NULL,kind TEXT NOT NULL,label TEXT NOT NULL,UNIQUE(platform,scope));
CREATE TABLE IF NOT EXISTS model_contents(id INTEGER PRIMARY KEY,source_id INTEGER NOT NULL,native_id TEXT NOT NULL,author TEXT,text TEXT NOT NULL DEFAULT '',UNIQUE(source_id,native_id));
CREATE TABLE IF NOT EXISTS model_media(id INTEGER PRIMARY KEY,content_id INTEGER NOT NULL,native_id TEXT NOT NULL,media_type TEXT NOT NULL,UNIQUE(content_id,native_id,media_type));
CREATE TABLE IF NOT EXISTS model_variants(id INTEGER PRIMARY KEY,media_id INTEGER NOT NULL,quality TEXT NOT NULL,extension TEXT NOT NULL,width INTEGER NOT NULL DEFAULT 0,height INTEGER NOT NULL DEFAULT 0,UNIQUE(media_id,quality,extension,width,height));
CREATE TABLE IF NOT EXISTS model_locations(id INTEGER PRIMARY KEY,variant_id INTEGER NOT NULL,legacy_job_id INTEGER UNIQUE,output TEXT NOT NULL,local_path TEXT NOT NULL DEFAULT '',state TEXT NOT NULL,error TEXT NOT NULL DEFAULT '',bytes INTEGER NOT NULL DEFAULT 0,sha256 TEXT NOT NULL DEFAULT '',verification TEXT NOT NULL DEFAULT '',generation INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS model_tasks(id INTEGER PRIMARY KEY,job_id INTEGER UNIQUE,variant_id INTEGER NOT NULL,location_id INTEGER,kind TEXT NOT NULL DEFAULT 'download',state TEXT NOT NULL DEFAULT 'pending',error TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS model_saved(seq INTEGER PRIMARY KEY AUTOINCREMENT,media_id INTEGER NOT NULL UNIQUE,added_at REAL);
CREATE INDEX IF NOT EXISTS model_locations_output_state ON model_locations(output,state,variant_id);
CREATE INDEX IF NOT EXISTS model_media_content ON model_media(content_id);
CREATE INDEX IF NOT EXISTS model_contents_source ON model_contents(source_id,native_id);
'''

TELEGRAM_SCHEMA = '''
CREATE TABLE IF NOT EXISTS telegram_saved(
    library TEXT NOT NULL, variant INTEGER NOT NULL, entry INTEGER NOT NULL,
    local_location INTEGER NOT NULL, model_location INTEGER NOT NULL,
    message_order INTEGER NOT NULL, url TEXT NOT NULL,
    PRIMARY KEY(library,variant)
);
CREATE INDEX IF NOT EXISTS telegram_saved_entry ON telegram_saved(library,entry);
CREATE INDEX IF NOT EXISTS telegram_saved_location ON telegram_saved(local_location);
CREATE UNIQUE INDEX IF NOT EXISTS telegram_saved_model ON telegram_saved(model_location);
CREATE TABLE IF NOT EXISTS telegram_messages(
    content INTEGER PRIMARY KEY, sent_at INTEGER NOT NULL DEFAULT 0,
    forward_id TEXT NOT NULL DEFAULT '', forward_title TEXT NOT NULL DEFAULT '',
    forward_url TEXT NOT NULL DEFAULT '', avatar_key TEXT NOT NULL DEFAULT ''
);
'''


def sync_sql(ref, updating=False):
    """The same statements power triggers and checkpointed migration; ref is internal SQL only."""
    source="(SELECT id FROM model_sources WHERE platform='x' AND scope='')"
    content=f'(SELECT id FROM model_contents WHERE source_id={source} AND native_id={ref}tweet_id)'
    media=f'(SELECT id FROM model_media WHERE content_id={content} AND native_id={ref}media_id AND media_type={ref}media_type)'
    variant=f'(SELECT id FROM model_variants WHERE media_id={media} AND quality={ref}quality AND extension={ref}extension AND width=COALESCE({ref}width,0) AND height=COALESCE({ref}height,0))'
    metadata_guard=' WHERE NEW.author IS NOT OLD.author OR NEW.tweet_text IS NOT OLD.tweet_text' if updating else ''
    return [
        "INSERT INTO model_sources(platform,scope,kind,label) VALUES('x','','social','X') ON CONFLICT(platform,scope) DO NOTHING",
        f"INSERT INTO model_contents(source_id,native_id,author,text) VALUES({source},{ref}tweet_id,NULLIF({ref}author,''),{ref}tweet_text) ON CONFLICT(source_id,native_id) DO UPDATE SET author=COALESCE(excluded.author,author),text=CASE WHEN excluded.text!='' THEN excluded.text ELSE text END"+metadata_guard,
        f'INSERT INTO model_media(content_id,native_id,media_type) VALUES({content},{ref}media_id,{ref}media_type) ON CONFLICT(content_id,native_id,media_type) DO NOTHING',
        f'INSERT INTO model_variants(media_id,quality,extension,width,height) VALUES({media},{ref}quality,{ref}extension,COALESCE({ref}width,0),COALESCE({ref}height,0)) ON CONFLICT(media_id,quality,extension,width,height) DO NOTHING',
        f'INSERT INTO model_locations(variant_id,legacy_job_id,output,local_path,state,error,bytes,sha256,verification,generation) VALUES({variant},{ref}id,{ref}output,COALESCE({ref}local_path,\'\'),{ref}state,COALESCE({ref}error,\'\'),COALESCE({ref}bytes,0),COALESCE({ref}sha256,\'\'),COALESCE({ref}verification,\'\'),{ref}receipt_generation) ON CONFLICT(legacy_job_id) DO UPDATE SET variant_id=excluded.variant_id,output=excluded.output,local_path=excluded.local_path,state=excluded.state,error=excluded.error,bytes=excluded.bytes,sha256=excluded.sha256,verification=excluded.verification,generation=excluded.generation',
        f'INSERT INTO model_tasks(job_id,variant_id,location_id,state,error) VALUES({ref}id,{variant},(SELECT id FROM model_locations WHERE legacy_job_id={ref}id),{ref}state,COALESCE({ref}error,\'\')) ON CONFLICT(job_id) DO UPDATE SET variant_id=excluded.variant_id,location_id=excluded.location_id,state=excluded.state,error=excluded.error',
    ]


class MediaModel:
    def __init__(self,db,lock):
        self.db,self.lock=db,lock
        self.migrate()
        with lock:
            db.executescript(TELEGRAM_SCHEMA)

    def migrate(self,batch=500,after_batch=None):
        with self.lock:
            exists=self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_migrations'").fetchone()
            prior=self.migration() if exists else None
            if prior and prior['done']:return
            if not prior:backup_before_migration(self.db,'model-v1')
            self.db.executescript(SCHEMA)
            body=';\n'.join(sync_sql('NEW.'))+';'
            update_body=';\n'.join(sync_sql('NEW.',updating=True))+';'
            self.db.executescript(f'''
                CREATE TRIGGER IF NOT EXISTS model_jobs_insert AFTER INSERT ON jobs BEGIN {body} END;
                CREATE TRIGGER IF NOT EXISTS model_jobs_update AFTER UPDATE ON jobs BEGIN {update_body} END;
                CREATE TRIGGER IF NOT EXISTS model_resource_insert AFTER INSERT ON resource_entries BEGIN
                    INSERT OR IGNORE INTO model_saved(media_id,added_at)
                    SELECT m.id,NEW.added_at FROM model_media m
                    JOIN model_contents c ON c.id=m.content_id JOIN model_sources s ON s.id=c.source_id
                    WHERE s.platform=NEW.platform AND s.scope='' AND c.native_id=NEW.message_id AND m.native_id=NEW.media_id AND m.media_type=NEW.media_type;
                END;
            ''')
            with self.db:self.db.execute("INSERT OR IGNORE INTO model_migrations VALUES('model-v1',0,0)")
            cursor=self.migration()['cursor']
            while True:
                rows=[dict(r) for r in self.db.execute('SELECT * FROM jobs WHERE id>? ORDER BY id LIMIT ?',(cursor,max(1,int(batch))))]
                if not rows:break
                with self.db:
                    for row in rows:
                        for sql in sync_sql(':'):self.db.execute(sql,row)
                    cursor=rows[-1]['id']
                    self.db.execute("UPDATE model_migrations SET cursor=? WHERE name='model-v1'",(cursor,))
                if after_batch:after_batch(cursor)
            with self.db:
                self.db.execute('''INSERT OR IGNORE INTO model_saved(media_id,added_at)
                    SELECT m.id,r.added_at FROM resource_entries r JOIN model_sources s ON s.platform=r.platform AND s.scope=''
                    JOIN model_contents c ON c.source_id=s.id AND c.native_id=r.message_id
                    JOIN model_media m ON m.content_id=c.id AND m.native_id=r.media_id AND m.media_type=r.media_type ORDER BY r.seq''')
                self.db.execute("UPDATE model_migrations SET done=1 WHERE name='model-v1'")

    def migration(self):
        row=self.db.execute("SELECT cursor,done FROM model_migrations WHERE name='model-v1'").fetchone()
        return dict(row) if row else None

    def content(self,platform,scope,native_id,author=None,text=''):
        if any(not isinstance(v,str) or len(v)>500 for v in (platform,scope,native_id)) or not platform or not native_id:raise ValueError('内容身份无效')
        if author is not None and not isinstance(author,str):raise ValueError('作者无效')
        with self.lock,self.db:
            source_kind={'local':'local','x':'social','telegram':'message'}.get(platform,'website')
            self.db.execute('INSERT OR IGNORE INTO model_sources(platform,scope,kind,label) VALUES(?,?,?,?)',(platform,scope,source_kind,platform))
            source=self.db.execute('SELECT id FROM model_sources WHERE platform=? AND scope=?',(platform,scope)).fetchone()[0]
            self.db.execute("INSERT INTO model_contents(source_id,native_id,author,text) VALUES(?,?,?,?) ON CONFLICT(source_id,native_id) DO UPDATE SET author=COALESCE(excluded.author,author),text=CASE WHEN excluded.text!='' THEN excluded.text ELSE text END",(source,native_id,author,text))
            return self.db.execute('SELECT id FROM model_contents WHERE source_id=? AND native_id=?',(source,native_id)).fetchone()[0]

    def media(self,content_id,native_id,media_type):
        if media_type not in ('video','image') or not isinstance(native_id,str) or not native_id:raise ValueError('媒体身份无效')
        with self.lock,self.db:
            if not self.db.execute('SELECT 1 FROM model_contents WHERE id=?',(content_id,)).fetchone():raise ValueError('内容不存在')
            self.db.execute('INSERT OR IGNORE INTO model_media(content_id,native_id,media_type) VALUES(?,?,?)',(content_id,native_id,media_type))
            return self.db.execute('SELECT id FROM model_media WHERE content_id=? AND native_id=? AND media_type=?',(content_id,native_id,media_type)).fetchone()[0]

    def variant(self,media_id,quality,extension,width=0,height=0):
        with self.lock,self.db:
            if not self.db.execute('SELECT 1 FROM model_media WHERE id=?',(media_id,)).fetchone():raise ValueError('媒体不存在')
            self.db.execute('INSERT OR IGNORE INTO model_variants(media_id,quality,extension,width,height) VALUES(?,?,?,?,?)',(media_id,quality,extension,width,height))
            return self.db.execute('SELECT id FROM model_variants WHERE media_id=? AND quality=? AND extension=? AND width=? AND height=?',(media_id,quality,extension,width,height)).fetchone()[0]

    def location(self,variant_id,output,path,state='completed',bytes=0,sha256=''):
        with self.lock,self.db:
            variant=self.db.execute('SELECT media_id FROM model_variants WHERE id=?',(variant_id,)).fetchone()
            if not variant:raise ValueError('媒体版本不存在')
            cursor=self.db.execute('INSERT INTO model_locations(variant_id,output,local_path,state,bytes,sha256) VALUES(?,?,?,?,?,?)',(variant_id,str(Path(output).resolve()),str(Path(path).resolve()),state,bytes,sha256))
            if state=='completed':self.db.execute('INSERT OR IGNORE INTO model_saved(media_id,added_at) VALUES(?,?)',(variant[0],time.time()))
            return cursor.lastrowid

    def counts(self):
        with self.lock:return {name:self.db.execute('SELECT COUNT(*) FROM model_'+name).fetchone()[0] for name in ('contents','media','variants','locations','tasks')}

    def create_task(self,variant_id,kind='download',location_id=None,state='pending'):
        if kind not in ('download','import','verify','process') or state not in ('pending','downloading','paused','failed','completed','removed'):
            raise ValueError('任务类型或状态无效')
        with self.lock,self.db:
            if not self.db.execute('SELECT 1 FROM model_variants WHERE id=?',(variant_id,)).fetchone():raise ValueError('媒体版本不存在')
            if location_id is not None and not self.db.execute('SELECT 1 FROM model_locations WHERE id=? AND variant_id=?',(location_id,variant_id)).fetchone():raise ValueError('文件位置与版本不匹配')
            return self.db.execute('INSERT INTO model_tasks(variant_id,location_id,kind,state) VALUES(?,?,?,?)',(variant_id,location_id,kind,state)).lastrowid

    def task(self,job_id):
        with self.lock:
            row=self.db.execute('SELECT l.* FROM model_tasks t JOIN model_locations l ON l.id=t.location_id WHERE t.job_id=?',(job_id,)).fetchone()
            return dict(row) if row else None
