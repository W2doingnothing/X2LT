# SPDX-License-Identifier: GPL-2.0-only
"""Atomic-published SQLite snapshots, including uncheckpointed WAL content."""
import os
from pathlib import Path
import sqlite3
import tempfile


def backup_before_migration(db, version):
    filename=db.execute('PRAGMA database_list').fetchone()[2]
    if not filename or not db.execute('SELECT 1 FROM jobs LIMIT 1').fetchone():return None
    source=Path(filename)
    target=source.with_name(source.name+'.pre-'+version+'.bak')
    if target.exists():return target
    fd,temp=tempfile.mkstemp(prefix=target.name+'.',suffix='.tmp',dir=target.parent)
    os.close(fd)
    try:
        destination=sqlite3.connect(temp)
        try:db.backup(destination)
        finally:destination.close()
        try:os.link(temp,target)
        except FileExistsError:pass
        return target
    finally:Path(temp).unlink(missing_ok=True)
