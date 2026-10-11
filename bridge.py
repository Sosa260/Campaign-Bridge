"""CampaignBridge: synthetic campaign integration with a transactional outbox.

Python 3.10+, standard library only. No customer data or external messaging.
"""
import hashlib
import json
import sqlite3
import time


class Invalid(ValueError):
    pass


class Conflict(ValueError):
    pass


class Bridge:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS customers (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, balance_cents INTEGER NOT NULL,
            outcome TEXT NOT NULL DEFAULT 'awaiting_call');
        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, customer_id TEXT NOT NULL,
            outcome TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS outbox (
            id TEXT PRIMARY KEY, customer_id TEXT NOT NULL, action TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
            retry_at REAL NOT NULL DEFAULT 0, last_error TEXT);
        CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, action TEXT NOT NULL);
        """)

    def close(self):
        self.db.close()

    def import_customers(self, rows):
        if not isinstance(rows, list) or not rows or len(rows) > 1000:
            raise Invalid('Provide between 1 and 1,000 customer records.')
        seen = set()
        for r in rows:
            if not isinstance(r, dict) or set(r) != {'id', 'name', 'balance_cents'}:
                raise Invalid('Each record needs id, name and balance_cents only.')
            if any(not isinstance(r[k], str) or not r[k].strip() or len(r[k]) > 100 for k in ('id', 'name')):
                raise Invalid('Customer id and name must be nonempty text, at most 100 characters.')
            if type(r['balance_cents']) is not int or not 0 <= r['balance_cents'] <= 100000000:
                raise Invalid('balance_cents must be a nonnegative integer, up to 100,000,000.')
            if r['id'] in seen:
                raise Invalid('Duplicate customer id in batch.')
            seen.add(r['id'])
        with self.db:
            for r in rows:
                self.db.execute('INSERT INTO customers(id,name,balance_cents) VALUES(?,?,?) '
                                'ON CONFLICT(id) DO UPDATE SET name=excluded.name, balance_cents=excluded.balance_cents',
                                (r['id'], r['name'], r['balance_cents']))
        return {'imported': len(rows)}

    def receive(self, event):
        allowed = {'promise_to_pay': 'send_payment_instructions',
                   'needs_help': 'create_support_task', 'declined': None}
        if not isinstance(event, dict) or set(event) != {'event_id', 'customer_id', 'outcome'}:
            raise Invalid('Event needs event_id, customer_id and outcome only.')
        if any(not isinstance(v, str) or not v.strip() or len(v) > 100 for v in event.values()):
            raise Invalid('Event fields must be nonempty text, at most 100 characters.')
        if event['outcome'] not in allowed:
            raise Invalid('Unsupported outcome.')
        digest = hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest()
        with self.db:
            # Reserve the write lock before checking idempotency and customer state.
            self.db.execute('BEGIN IMMEDIATE')
            old = self.db.execute('SELECT payload_hash FROM events WHERE id=?', (event['event_id'],)).fetchone()
            if old:
                if old[0] != digest:
                    raise Conflict('Event id already used for different data.')
                return {'accepted': True, 'duplicate': True, 'followup_queued': False}
            if not self.db.execute('SELECT 1 FROM customers WHERE id=?', (event['customer_id'],)).fetchone():
                raise Invalid('Customer not found. Import account data before processing a call.')
            self.db.execute('INSERT INTO events VALUES(?,?,?,?,?)',
                            (event['event_id'], digest, event['customer_id'], event['outcome'], time.time()))
            self.db.execute('UPDATE customers SET outcome=? WHERE id=?', (event['outcome'], event['customer_id']))
            action = allowed[event['outcome']]
            if action:
                self.db.execute('INSERT INTO outbox(id,customer_id,action) VALUES(?,?,?)',
                                (event['event_id'], event['customer_id'], action))
        return {'accepted': True, 'duplicate': False, 'followup_queued': bool(action)}

    def dispatch(self, fail=False, now=None):
        """Local mock destination. No external request or message is sent.

        Due deliveries use exponential backoff and a three-attempt limit. Receipt
        IDs model a destination supporting idempotency. Manual retry after dead
        letter requires operator inspection; it is deliberately not automatic.
        """
        now = time.time() if now is None else now
        processed = []
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            jobs = self.db.execute("SELECT * FROM outbox WHERE status IN ('pending','retry') AND retry_at<=?", (now,)).fetchall()
            for j in jobs:
                attempt = j['attempts'] + 1
                status = ('dead_letter' if attempt >= 3 else 'retry') if fail else 'delivered'
                if not fail:
                    self.db.execute('INSERT OR IGNORE INTO receipts VALUES(?,?)', (j['id'], j['action']))
                self.db.execute('UPDATE outbox SET status=?,attempts=?,retry_at=?,last_error=? WHERE id=?',
                                (status, attempt, now + 2 ** attempt if fail else 0,
                                 'Simulated destination HTTP 503' if fail else None, j['id']))
                processed.append({'id': j['id'], 'status': status, 'attempt': attempt})
        return {'processed': processed, 'simulation': True}

    def snapshot(self):
        return {table: [dict(r) for r in self.db.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]
                for table in ('customers', 'events', 'outbox', 'receipts')}


SAMPLE = [
    {'id': 'demo-001', 'name': 'Demo customer A', 'balance_cents': 250000},
    {'id': 'demo-002', 'name': 'Demo customer B', 'balance_cents': 120000},
    {'id': 'demo-003', 'name': 'Demo customer C', 'balance_cents': 85000},
]
