import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from http.client import HTTPConnection
from http.server import HTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bridge import Bridge, Conflict, Invalid, SAMPLE
from server import Handler, TOKEN


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.b = Bridge(str(Path(self.temp.name) / 'test.db'))
        self.b.import_customers(SAMPLE)
        self.event = {'event_id': 'evt-1', 'customer_id': 'demo-001', 'outcome': 'promise_to_pay'}

    def tearDown(self):
        self.b.close()
        self.temp.cleanup()

    def test_duplicate_event_has_one_outbox_job(self):
        self.b.receive(self.event)
        self.assertTrue(self.b.receive(self.event)['duplicate'])
        self.assertEqual(len(self.b.snapshot()['outbox']), 1)

    def test_conflicting_event_does_not_change_account(self):
        self.b.receive(self.event)
        with self.assertRaises(Conflict):
            self.b.receive(dict(self.event, outcome='declined'))
        self.assertEqual(self.b.snapshot()['customers'][0]['outcome'], 'promise_to_pay')

    def test_missing_account_rolls_back_everything(self):
        with self.assertRaises(Invalid):
            self.b.receive(dict(self.event, customer_id='missing'))
        self.assertEqual(self.b.snapshot()['events'], [])
        self.assertEqual(self.b.snapshot()['outbox'], [])

    def test_import_is_atomic_and_boolean_balance_is_invalid(self):
        with self.assertRaises(Invalid):
            self.b.import_customers([{'id': 'new', 'name': 'Demo', 'balance_cents': 50},
                                     {'id': 'bad', 'name': 'Demo', 'balance_cents': True}])
        self.assertEqual(len(self.b.snapshot()['customers']), 3)

    def test_declined_does_not_queue_followup(self):
        self.b.receive(dict(self.event, outcome='declined'))
        self.assertEqual(self.b.snapshot()['outbox'], [])

    def test_failure_waits_then_recovers_once(self):
        self.b.receive(self.event)
        self.assertEqual(self.b.dispatch(fail=True, now=100)['processed'][0]['status'], 'retry')
        self.assertEqual(self.b.dispatch(now=101)['processed'], [])
        self.b.dispatch(now=102)
        self.b.dispatch(now=200)
        self.assertEqual(len(self.b.snapshot()['receipts']), 1)
        self.assertEqual(self.b.snapshot()['outbox'][0]['attempts'], 2)

    def test_three_failures_stop_retrying(self):
        self.b.receive(self.event)
        for now in [100, 102, 106]:
            self.b.dispatch(fail=True, now=now)
        self.assertEqual(self.b.snapshot()['outbox'][0]['status'], 'dead_letter')
        self.assertEqual(self.b.dispatch(now=999)['processed'], [])

    def test_restart_keeps_pending_work(self):
        self.b.receive(self.event)
        other = Bridge(str(Path(self.temp.name) / 'test.db'))
        try:
            self.assertEqual(len(other.snapshot()['outbox']), 1)
        finally:
            other.close()


class HTTPTests(unittest.TestCase):
    def test_auth_validation_and_webhook_roundtrip(self):
        with tempfile.TemporaryDirectory() as directory:
            server = HTTPServer(('127.0.0.1', 0), Handler)
            server.db_path = str(Path(directory) / 'http.db')
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                connection = HTTPConnection('127.0.0.1', server.server_port)
                def post(path, body, authorized=True):
                    headers = {'Content-Type': 'application/json'}
                    if authorized:
                        headers['Authorization'] = 'Bearer ' + TOKEN
                    connection.request('POST', path, json.dumps(body), headers)
                    response = connection.getresponse()
                    return response.status, json.loads(response.read())
                self.assertEqual(post('/api/import', SAMPLE, False)[0], 401)
                self.assertEqual(post('/api/import', SAMPLE)[0], 200)
                event = {'event_id': 'http-1', 'customer_id': 'demo-001', 'outcome': 'needs_help'}
                self.assertEqual(post('/api/webhook', event)[0], 200)
                self.assertTrue(post('/api/webhook', event)[1]['duplicate'])
                self.assertEqual(post('/api/webhook', dict(event, outcome='declined'))[0], 409)
                self.assertEqual(post('/api/webhook', {})[0], 400)
                self.assertEqual(post('/api/dispatch', {'fail': 'false'})[0], 400)
                self.assertEqual(post('/api/dispatch', {})[1]['processed'][0]['status'], 'delivered')
                connection.close()
            finally:
                server.shutdown()
                server.server_close()


if __name__ == '__main__':
    unittest.main()
