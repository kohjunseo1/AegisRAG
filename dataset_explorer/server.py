#!/usr/bin/env python3
"""Read-only local explorer for the three supplied evaluation splits. Standard library only."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from statistics import mean, median
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
SOURCES = {
    'dev': 'pii_synth_finance_dev.jsonl',
    'test': 'pii_synth_finance_test.jsonl',
    'holdout': 'holdout_labeled.jsonl',
}
LABELS = ['person', 'phone_number', 'email', 'address',
          'resident_registration_number', 'account_number', 'card_number']


def load_data(directory: Path) -> dict:
    """Never writes source files, stores exports, loads env secrets, or calls models/APIs."""
    documents, splits = [], []
    duplicates = defaultdict(list)
    for split, filename in SOURCES.items():
        path = directory / filename
        if not path.is_file():
            raise ValueError('MISSING_SOURCE:' + filename)
        payload = path.read_bytes()
        try:
            rows = [json.loads(line) for line in payload.decode('utf-8').splitlines() if line.strip()]
        except (ValueError, UnicodeError):
            raise ValueError('INVALID_JSONL:' + filename) from None
        counts, presence, doc_types, hard_cases, fields = Counter(), Counter(), Counter(), Counter(), Counter()
        lengths, invalid, overlap_count, empty, ids = [], 0, 0, 0, set()
        for position, row in enumerate(rows, 1):
            if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not isinstance(row.get('text'), str) or not isinstance(row.get('entities'), list):
                raise ValueError(f'INVALID_DOCUMENT:{filename}:line{position}')
            if row['id'] in ids:
                raise ValueError(f'DUPLICATE_DOCUMENT_ID:{filename}:line{position}')
            ids.add(row['id'])
            text, entities, issues = row['text'], row['entities'], []
            valid = []
            for index, entity in enumerate(entities):
                if not isinstance(entity, dict) or not isinstance(entity.get('label'), str):
                    raise ValueError(f'INVALID_ENTITY:{filename}:line{position}')
                counts[entity['label']] += 1
                a, b = entity.get('start'), entity.get('end')
                if type(a) is not int or type(b) is not int or not 0 <= a < b <= len(text):
                    issues.append({'entity': index, 'kind': 'invalid_offset'})
                else:
                    valid.append((a, b))
                    if entity.get('text') != text[a:b]:
                        issues.append({'entity': index, 'kind': 'text_mismatch'})
            for label in {e['label'] for e in entities}:
                presence[label] += 1
            overlapped = any(a < d and c < b for i, (a, b) in enumerate(valid) for c, d in valid[i+1:])
            if overlapped:
                overlap_count += 1
            invalid += len(issues)
            empty += not entities
            lengths.append(len(text))
            doc_types[str(row.get('doc_type', '미지정'))] += 1
            hard_cases.update(str(tag) for tag in row.get('hard_cases', []))
            fields.update(row.keys())
            item = {'split_key': split, 'char_count': len(text), 'issues': issues,
                    'has_overlap': overlapped, 'record': row}
            documents.append(item)
            duplicates[hashlib.sha256(text.encode()).hexdigest()].append({'split': split, 'id': row['id']})
        splits.append({'key': split, 'filename': filename, 'sha256': hashlib.sha256(payload).hexdigest(),
            'documents': len(rows), 'entities': sum(counts.values()), 'labels': dict(counts),
            'label_documents': dict(presence), 'doc_types': dict(doc_types), 'hard_cases': dict(hard_cases),
            'fields': dict(fields), 'characters': sum(lengths), 'mean_chars': round(mean(lengths), 1) if lengths else 0,
            'median_chars': median(lengths) if lengths else 0, 'min_chars': min(lengths, default=0),
            'max_chars': max(lengths, default=0), 'empty_entities': empty, 'annotation_issues': invalid,
            'overlap_documents': overlap_count})
    duplicate_groups = [group for group in duplicates.values() if len(group) > 1]
    cross = [group for group in duplicate_groups if len({d['split'] for d in group}) > 1]
    known = set(splits[0]['doc_types']) | set(splits[1]['doc_types'])
    return {'splits': splits, 'documents': documents, 'labels': LABELS,
            'summary': {'documents': len(documents), 'entities': sum(s['entities'] for s in splits),
                'doc_types': len({t for s in splits for t in s['doc_types']}),
                'hard_case_tags': len({t for s in splits for t in s['hard_cases']}),
                'duplicate_groups': duplicate_groups, 'cross_split_duplicates': cross,
                'holdout_new_doc_types': sorted(set(splits[2]['doc_types']) - known)}}


class Handler(BaseHTTPRequestHandler):
    data: dict

    def log_message(self, *args):
        pass  # URLs and document contents must never enter access logs.

    def send(self, content, mime, status=200):
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        if self.headers.get('Host') not in allowed:
            return self.send(b'INVALID_HOST', 'text/plain', 403)
        path = urlsplit(self.path).path
        if path == '/api/data':
            origin = self.headers.get('Origin')
            if self.headers.get('Sec-Fetch-Site') == 'cross-site' or (origin and origin not in {'http://' + h for h in allowed}):
                return self.send(b'INVALID_ORIGIN', 'text/plain', 403)
            return self.send(json.dumps(self.data, ensure_ascii=False).encode(), 'application/json; charset=utf-8')
        assets = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'application/javascript'), '/style.css': ('style.css', 'text/css')}
        if path not in assets:
            return self.send(b'NOT_FOUND', 'text/plain', 404)
        name, mime = assets[path]
        return self.send((HERE / name).read_bytes(), mime + '; charset=utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=ROOT/'step2_privacy_rag/local_eval')
    parser.add_argument('--port', type=int, default=8770)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('port must be between 1024 and 65535')
    try:
        Handler.data = load_data(args.data_dir)
        server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    except ValueError as exc:
        parser.exit(2, str(exc) + '\n')
    except OSError:
        parser.exit(2, 'STARTUP_FAILED: check source access and whether the port is already in use.\n')
    print(f'Dataset Explorer: http://127.0.0.1:{args.port}', flush=True)
    print(f"Loaded {Handler.data['summary']['documents']} documents. Read-only, local-only; Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
