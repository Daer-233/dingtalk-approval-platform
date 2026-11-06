from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from .auth import PaymentError


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / 'attachments').mkdir(exist_ok=True)
        self.path = self.root / 'payment-tools.sqlite3'
        with self.db() as c:
            c.executescript('''
              PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS runs(grant_id TEXT PRIMARY KEY, owner TEXT, corp TEXT,
                workspace TEXT,event TEXT,expires INTEGER,closed INTEGER DEFAULT 0);
              CREATE TABLE IF NOT EXISTS attachments(ref TEXT PRIMARY KEY,owner TEXT,corp TEXT,workspace TEXT,
                event TEXT,name TEXT,path TEXT,sha TEXT,size INTEGER,created REAL,
                UNIQUE(owner,corp,workspace,event));
              CREATE TABLE IF NOT EXISTS operations(op_key TEXT PRIMARY KEY,owner TEXT,corp TEXT,workspace TEXT,
                phase TEXT,state TEXT,result TEXT,created REAL,updated REAL);
              CREATE TABLE IF NOT EXISTS tasks(task_id TEXT PRIMARY KEY,owner TEXT,corp TEXT,workspace TEXT,
                event TEXT,created REAL);
            ''')

    @contextmanager
    def db(self):
        c = sqlite3.connect(self.path, timeout=20)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def register_run(self, actor):
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT * FROM runs WHERE grant_id=?', (actor.grant,)).fetchone()
            if row:
                if not self._same(row, actor) or row['event'] != actor.event or row['closed']:
                    raise PaymentError('run_closed', '付款工具授权已撤销。', 403)
                return
            c.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,0)',
                      (actor.grant, actor.staff, actor.corp, actor.workspace, actor.event, actor.expires))

    def run_active(self, actor):
        with self.db() as c:
            row = c.execute('SELECT * FROM runs WHERE grant_id=?', (actor.grant,)).fetchone()
            return bool(row and self._same(row, actor) and row['event'] == actor.event
                        and not row['closed'] and row['expires'] > time.time())

    def revoke(self, actor):
        with self.db() as c:
            c.execute('UPDATE runs SET closed=1 WHERE grant_id=?', (actor.grant,))

    @staticmethod
    def _same(row, actor):
        return (row['owner'], row['corp'], row['workspace']) == (actor.staff, actor.corp, actor.workspace)

    def event_attachment(self, actor):
        with self.db() as c:
            row = c.execute('SELECT * FROM attachments WHERE owner=? AND corp=? AND workspace=? AND event=?',
                            (actor.staff, actor.corp, actor.workspace, actor.event)).fetchone()
            return dict(row) if row else None

    def latest_attachment(self, actor):
        with self.db() as c:
            row=c.execute('SELECT * FROM attachments WHERE owner=? AND corp=? AND workspace=? AND created>? ORDER BY created DESC LIMIT 1',
                (actor.staff,actor.corp,actor.workspace,time.time()-1800)).fetchone()
            return {'attachment_ref':row['ref'],'file_name':row['name'],'size_bytes':row['size']} if row else None

    def save_attachment(self, actor, name, path, sha, size):
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            old = c.execute('SELECT * FROM attachments WHERE owner=? AND corp=? AND workspace=? AND event=?',
                            (actor.staff, actor.corp, actor.workspace, actor.event)).fetchone()
            if old:
                if old['sha'] != sha:
                    raise PaymentError('event_conflict', '同一消息的文件内容发生变化，请重新发送。', 409)
                return dict(old)
            ref = uuid4().hex
            c.execute('INSERT INTO attachments VALUES(?,?,?,?,?,?,?,?,?,?)',
                      (ref, actor.staff, actor.corp, actor.workspace, actor.event, name, str(path), sha, size, time.time()))
            return dict(c.execute('SELECT * FROM attachments WHERE ref=?', (ref,)).fetchone())

    def attachment(self, ref, actor):
        with self.db() as c:
            row = c.execute('SELECT * FROM attachments WHERE ref=?', (ref,)).fetchone()
            if not row or not self._same(row, actor):
                raise PaymentError('not_found', '附件不存在或不属于当前账号。', 404)
            return dict(row)

    def own(self, task_id, actor):
        with self.db() as c:
            row = c.execute('SELECT * FROM tasks WHERE task_id=?', (task_id,)).fetchone()
            if not row or not self._same(row, actor):
                raise PaymentError('not_found', '任务不存在或不属于当前账号。', 404)

    def add_task(self, task_id, actor):
        with self.db() as c:
            old = c.execute('SELECT * FROM tasks WHERE task_id=?', (task_id,)).fetchone()
            if old and not self._same(old, actor):
                raise PaymentError('task_conflict', '任务归属冲突。', 409)
            c.execute('INSERT OR IGNORE INTO tasks VALUES(?,?,?,?,?,?)',
                      (task_id, actor.staff, actor.corp, actor.workspace, actor.event, time.time()))

    def task_ids(self, actor, limit):
        with self.db() as c:
            return [r[0] for r in c.execute('SELECT task_id FROM tasks WHERE owner=? AND corp=? AND workspace=? ORDER BY created DESC LIMIT ?',
                    (actor.staff, actor.corp, actor.workspace, limit))]

    def begin(self, key, actor, phase):
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT * FROM operations WHERE op_key=?', (key,)).fetchone()
            if row:
                if not self._same(row, actor):
                    raise PaymentError('not_found', '操作不存在。', 404)
                if row['state'] == 'done':
                    return json.loads(row['result'])
                raise PaymentError('uncertain' if row['state'] == 'uncertain' else 'in_progress',
                                   '上次处理结果待核对，请先进入平台查看，不会自动重复创建。', 409)
            c.execute('INSERT INTO operations VALUES(?,?,?,?,?,?,?,?,?)',
                      (key, actor.staff, actor.corp, actor.workspace, phase, 'running', None, time.time(), time.time()))
            return None

    def finish(self, key, result=None, *, uncertain=False):
        with self.db() as c:
            c.execute('UPDATE operations SET state=?,result=?,updated=? WHERE op_key=?',
                      ('uncertain' if uncertain else 'done', json.dumps(result, ensure_ascii=False), time.time(), key))

    def recover(self):
        with self.db() as c:
            c.execute("UPDATE operations SET state='uncertain' WHERE state='running'")
