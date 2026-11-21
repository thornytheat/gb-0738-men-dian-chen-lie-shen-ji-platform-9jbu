import json
import sqlite3
from contextlib import contextmanager

from app.errors import AuditError
from app.manifest import canonical

DDL = (
    '''CREATE TABLE IF NOT EXISTS runs (
        run_id TEXT PRIMARY KEY, snapshot TEXT NOT NULL
    )''',
    '''CREATE TABLE IF NOT EXISTS confirmations (
        seq INTEGER PRIMARY KEY,
        confirmation_id TEXT NOT NULL UNIQUE,
        request_id TEXT NOT NULL UNIQUE,
        dataset TEXT NOT NULL,
        shelf_key TEXT NOT NULL,
        plan TEXT NOT NULL,
        digest TEXT NOT NULL,
        actor TEXT NOT NULL,
        note TEXT NOT NULL,
        evidence_ref TEXT NOT NULL,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL
    )''',
    '''CREATE TABLE IF NOT EXISTS appeal_events (
        seq INTEGER PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES runs(run_id),
        shelf_key TEXT NOT NULL,
        version INTEGER NOT NULL CHECK (version > 0),
        request_id TEXT NOT NULL,
        payload TEXT NOT NULL,
        event TEXT NOT NULL,
        UNIQUE (run_id, shelf_key, version),
        UNIQUE (run_id, request_id)
    )''',
)


class Store:
    def __init__(self, database):
        self.database = database

    @contextmanager
    def connection(self, write=False):
        connection = sqlite3.connect(self.database, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute('PRAGMA foreign_keys = ON')
            connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self):
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self.connection(write=True) as connection:
            for statement in DDL:
                connection.execute(statement)

    @staticmethod
    def read(connection, run_id, required=True):
        found = connection.execute('SELECT snapshot FROM runs WHERE run_id = ?', (run_id,)).fetchone()
        if found is None:
            if required:
                raise AuditError('RUN_NOT_FOUND', '未找到该巡检批次', 404)
            return None
        events = connection.execute('SELECT event FROM appeal_events WHERE run_id = ? ORDER BY seq', (run_id,))
        return {**json.loads(found['snapshot']), 'events': [json.loads(event['event']) for event in events]}

    def get(self, run_id, required=True):
        with self.connection() as connection:
            return self.read(connection, run_id, required)

    def save(self, snapshot):
        run_id = snapshot['manifest']['run_id']
        record = canonical(snapshot)
        with self.connection(write=True) as connection:
            inserted = connection.execute('INSERT INTO runs VALUES (?, ?) ON CONFLICT(run_id) DO NOTHING', (run_id, record))
            return self.read(connection, run_id), inserted.rowcount == 0

    def batches(self):
        """按生成时间倒序列出已留存批次，供追溯旧版结算。"""
        with self.connection() as connection:
            rows = connection.execute('SELECT rowid AS seq, run_id, snapshot FROM runs').fetchall()
        found = []
        for row in rows:
            snapshot = json.loads(row['snapshot'])
            found.append({'run_id': row['run_id'], 'seq': row['seq'],
                          'created_at': snapshot['manifest']['created_at'],
                          'version': snapshot['manifest']['version'],
                          'parameters': snapshot['manifest']['parameters'],
                          'counts': snapshot['settlement']['counts'],
                          'original_cents': snapshot['settlement']['original_cents'],
                          'confirmations': len(snapshot['manifest'].get('confirmations', [])),
                          'overlap_summary': snapshot['settlement'].get('overlap_summary')})
        return sorted(found, key=lambda item: (item['created_at'], item['seq']), reverse=True)

    def latest_run_id(self):
        rows = self.batches()
        return rows[0]['run_id'] if rows else None

    def append_event(self, run_id, payload, build):
        with self.connection(write=True) as connection:
            snapshot = self.read(connection, run_id)
            raw = canonical(payload)
            old = connection.execute('SELECT payload, event FROM appeal_events WHERE run_id = ? AND request_id = ?',
                                     (run_id, payload['request_id'])).fetchone()
            if old:
                if old['payload'] != raw:
                    raise AuditError('REQUEST_CONFLICT', '提交编号已用于其他内容', 409)
                return json.loads(old['event']), True
            event = build(snapshot, payload)
            connection.execute('''INSERT INTO appeal_events
                (run_id, shelf_key, version, request_id, payload, event) VALUES (?, ?, ?, ?, ?, ?)''',
                (run_id, event['key'], event['version'], payload['request_id'], raw, canonical(event)))
            return event, False

    def save_confirmation(self, payload, build):
        """登记一条叠期柜位确认；相同请求可重放，同一柜位同一版本只能确认一次。"""
        with self.connection(write=True) as connection:
            existing = self.confirmations(connection, payload['dataset'])
            raw = canonical(payload)
            old = connection.execute('SELECT payload, confirmation_id FROM confirmations WHERE request_id = ?',
                                     (payload['request_id'],)).fetchone()
            if old:
                if old['payload'] != raw:
                    raise AuditError('REQUEST_CONFLICT', '提交编号已用于其他内容', 409)
                return self.confirmation(connection, old['confirmation_id']), True
            record, outcome = build(existing, payload)
            if outcome == 'reused':
                return record, True
            try:
                connection.execute('''INSERT INTO confirmations
                    (confirmation_id, request_id, dataset, shelf_key, plan, digest, actor, note, evidence_ref,
                     payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                    (record['confirmation_id'], payload['request_id'], payload['dataset'], record['key'],
                     record['plan'], record['digest'], record['actor'], record['note'], record['evidence_ref'],
                     raw, record['created_at']))
            except sqlite3.IntegrityError as error:
                raise AuditError('CONFIRMATION_CONFLICT', '该柜位版本已另行确认', 409, payload['key']) from error
            return record, False

    def confirmations(self, connection=None, dataset=None):
        columns = 'confirmation_id, shelf_key, plan, digest, actor, note, evidence_ref, created_at'
        if connection is None:
            with self.connection() as opened:
                return self.confirmations(opened, dataset)
        if dataset is None:
            rows = connection.execute(f'SELECT {columns} FROM confirmations ORDER BY seq')
        else:
            rows = connection.execute(f'SELECT {columns} FROM confirmations WHERE dataset = ? ORDER BY seq', (dataset,))
        return {row['shelf_key']: self.record(row) for row in rows}

    @staticmethod
    def record(row):
        return {'confirmation_id': row['confirmation_id'], 'key': row['shelf_key'], 'plan': row['plan'],
                'digest': row['digest'], 'actor': row['actor'], 'note': row['note'],
                'evidence_ref': row['evidence_ref'], 'created_at': row['created_at']}

    def confirmation(self, connection, confirmation_id):
        row = connection.execute('SELECT confirmation_id, shelf_key, plan, digest, actor, note, evidence_ref, '
                                 'created_at FROM confirmations WHERE confirmation_id = ?',
                                 (confirmation_id,)).fetchone()
        if row is None:
            raise AuditError('CONFIRMATION_NOT_FOUND', '未找到该叠期确认记录', 404)
        return self.record(row)

    def get_confirmation(self, confirmation_id):
        with self.connection() as connection:
            return self.confirmation(connection, confirmation_id)
