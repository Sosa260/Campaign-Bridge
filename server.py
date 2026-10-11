"""Run: python3 server.py. Local demo at http://127.0.0.1:8510."""
import argparse
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from bridge import Bridge, Conflict, Invalid, SAMPLE

ROOT = Path(__file__).resolve().parent
TOKEN = os.environ.get('CAMPAIGN_TOKEN', 'local-demo-token')


class Handler(BaseHTTPRequestHandler):
    def reply(self, code, payload, content_type='application/json'):
        body = json.dumps(payload).encode() if content_type == 'application/json' else payload
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/':
            return self.reply(200, (ROOT / 'static/index.html').read_bytes(), 'text/html; charset=utf-8')
        if self.path == '/api/state':
            bridge = Bridge(self.server.db_path)
            try:
                return self.reply(200, bridge.snapshot())
            finally:
                bridge.close()
        self.reply(404, {'error': 'Not found'})

    def do_POST(self):
        if not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + TOKEN):
            return self.reply(401, {'error': 'Invalid bearer token'})
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            return self.reply(415, {'error': 'Use application/json'})
        bridge = None
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 65536:
                raise Invalid('JSON body must be between 1 and 65,536 bytes.')
            body = json.loads(self.rfile.read(size))
            bridge = Bridge(self.server.db_path)
            if self.path == '/api/import':
                result = bridge.import_customers(body)
            elif self.path == '/api/webhook':
                result = bridge.receive(body)
            elif self.path == '/api/dispatch':
                if not isinstance(body, dict) or set(body) - {'fail'} or type(body.get('fail', False)) is not bool:
                    raise Invalid('Use an object with an optional boolean fail field.')
                result = bridge.dispatch(body.get('fail', False))
            else:
                return self.reply(404, {'error': 'Not found'})
            self.reply(200, result)
        except Conflict as e:
            self.reply(409, {'error': str(e)})
        except (Invalid, ValueError, UnicodeDecodeError) as e:
            self.reply(400, {'error': str(e)})
        finally:
            if bridge:
                bridge.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8510)
    parser.add_argument('--db', default=str(ROOT / 'demo.sqlite3'))
    args = parser.parse_args()
    server = HTTPServer(('127.0.0.1', args.port), Handler)
    server.db_path = args.db
    print(f'CampaignBridge: http://127.0.0.1:{args.port} — synthetic data only', flush=True)
    server.serve_forever()
