"""Measure real ledger/customer operations using exact disposable test resources.

The initial histories are valid fixtures built with the existing canonical engine
and inserted by COPY. Fixture creation is not measured or described as appending
through the application. Timed Store.append calls retain complete verification.
"""
import argparse
import datetime
import gc
import json
import math
import os
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import time
import tracemalloc
import uuid

from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
from checkpoints import CheckpointService
from ledger import canonical_json, create_new_block
from settings import load_environment
from shipment_access import ShipmentAccessService
from store import Store, table_names


FIELDS = [{'name': name, 'type': 'string', 'required': True}
          for name in ('shipment_id', 'event_type', 'event_time', 'source_system')]
SHIPMENT = 'BENCH-000'


def event(index):
    return {'shipment_id': 'BENCH-' + str(index % 100).zfill(3),
            'event_type': 'BENCHMARK_FIXTURE', 'event_time': '2026-10-29T00:00:00Z',
            'source_system': 'DISPOSABLE_BENCHMARK'}


def summarize(samples):
    values = sorted(sample['wall_ms'] for sample in samples)
    return {'count': len(values), 'p50_ms': round(statistics.median(values), 3),
            'p95_ms': round(values[math.ceil(0.95 * len(values)) - 1], 3),
            'python_cpu_p50_ms': round(statistics.median(sample['python_cpu_ms'] for sample in samples), 3),
            'samples': samples}


def measure(operation):
    started_wall, started_cpu = time.perf_counter_ns(), time.process_time_ns()
    result = operation()
    sample = {'wall_ms': round((time.perf_counter_ns() - started_wall) / 1_000_000, 3),
              'python_cpu_ms': round((time.process_time_ns() - started_cpu) / 1_000_000, 3)}
    return result, sample


def python_peak(operation):
    """Separate instrumentation run; tracing never affects the latency samples."""
    gc.collect()
    tracemalloc.start(1)
    try:
        result = operation()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return result, peak


def seed_history(store, ledger_id, size):
    """COPY engine-produced blocks into this newly created, owned ledger only."""
    blocks, _ = table_names(ledger_id)
    started = time.perf_counter()
    encoded_bytes = 0
    with store.connect() as connection:
        metadata = store._metadata(connection, ledger_id, lock=True)
        if metadata['head_index'] != 0:
            raise RuntimeError('Fixture ledger is not empty; refusing to seed')
        previous = store._blocks(connection, ledger_id)[0]
        with connection.cursor().copy(sql.SQL('COPY {} (position, record_id, record) FROM STDIN')
                .format(sql.Identifier(blocks))) as copy:
            for index in range(size):
                block = create_new_block(previous, {'record_id': 'fixture-' + str(index), 'data': event(index)})
                encoded_bytes += len(canonical_json(block))
                copy.write_row((block['index'], block['record_id'], Jsonb(block)))
                previous = block
        connection.execute('UPDATE ledger_registry SET head_index=%s,head_hash=%s WHERE id=%s',
                           (previous['index'], previous['hash'], ledger_id))
    return {'business_events': size, 'genesis_blocks': 1,
            'canonical_business_record_bytes': encoded_bytes,
            'seed_elapsed_seconds': round(time.perf_counter() - started, 3),
            'method': 'Canonical create_new_block fixtures inserted by one PostgreSQL COPY; not application append throughput'}


def run_case(store, size, count):
    owner = {'id': 'benchmark-' + uuid.uuid4().hex, 'is_admin': False}
    created = store.create_ledger(owner['id'], 'Disposable ledger benchmark', '', FIELDS, visibility='private')
    ledger_id, token = created['ledger']['id'], created['write_token']
    checkpoints = None
    with tempfile.TemporaryDirectory(prefix='chainguard-benchmark-') as directory:
        try:
            fixture = seed_history(store, ledger_id, size)
            if not store.read(ledger_id)['verification']['valid']:
                raise RuntimeError('Seeded fixture failed complete chain verification')
            append_samples = []
            for index in range(count):
                result, sample = measure(lambda index=index: store.append(
                    ledger_id, token, event(size + index), 'measured-' + str(index)))
                if result[1]:
                    raise RuntimeError('Timed append unexpectedly returned an idempotent duplicate')
                append_samples.append(sample)
            _, append_peak = python_peak(lambda: store.append(ledger_id, token,
                event(size + count), 'memory-probe'))
            checkpoints = CheckpointService(store, Path(directory) / 'checkpoint.key')
            checkpoint = checkpoints.create(ledger_id)
            service = ShipmentAccessService(store, checkpoints)
            grant = service.create(ledger_id, SHIPMENT, user=owner)
            access = grant['access_token']
            cold_samples = []
            for _ in range(count):
                service = ShipmentAccessService(store, checkpoints)
                result, sample = measure(lambda: service.events(SHIPMENT, access, limit=25))
                if not result['verification']['server_records_verified']:
                    raise RuntimeError('Cold customer verification failed')
                cold_samples.append(sample)
            checked_at = result['verification']['checked_at_ms']
            hit_samples = []
            for _ in range(count):
                result, sample = measure(lambda: service.events(SHIPMENT, access, limit=25))
                if result['verification']['checked_at_ms'] != checked_at:
                    raise RuntimeError('Customer cache expired during the cache-hit measurement')
                hit_samples.append(sample)
            memory_service = ShipmentAccessService(store, checkpoints)
            _, cold_peak = python_peak(lambda: memory_service.events(SHIPMENT, access, limit=25))
            _, hit_peak = python_peak(lambda: memory_service.events(SHIPMENT, access, limit=25))
            view = store.read(ledger_id)
            if not view['verification']['valid'] or len(view['blocks']) != size + count + 2:
                raise RuntimeError('Final history verification or record count failed')
            with store.connect() as connection:
                relation_bytes = connection.execute('SELECT pg_total_relation_size(%s::regclass) AS bytes',
                    (table_names(ledger_id)[0],)).fetchone()['bytes']
            return {'initial_business_events': size, 'fixture': fixture,
                'append': {**summarize(append_samples), 'separate_python_peak_bytes': append_peak},
                'customer_cold': {**summarize(cold_samples), 'separate_python_peak_bytes': cold_peak},
                'customer_cache_hit': {**summarize(hit_samples), 'separate_python_peak_bytes': hit_peak,
                                       'unchanged_verification_timestamp': True},
                'customer_page_limit': 25, 'shipment_count_in_fixture': 100,
                'selected_shipment_total': result['total'],
                'checkpoints_present': 1, 'checkpoint_head_index': checkpoint['index'],
                'final_total_blocks_including_genesis': len(view['blocks']),
                'relation_bytes_including_indexes': relation_bytes, 'final_verification': 'valid'}
        finally:
            # IDs and key material belong exclusively to this invocation.
            store.delete_ledger(ledger_id)
            if checkpoints is not None:
                key_id = checkpoints.verification_key['key_id']
                with store.connect() as connection:
                    connection.execute('DELETE FROM signing_key_bindings WHERE key_id=%s', (key_id,))
                    connection.execute('DELETE FROM verification_keys WHERE key_id=%s', (key_id,))


def write_report(path, evidence):
    lines = ['# Ledger and customer-read measurements', '',
        'Run: ' + evidence['measured_at'] + ' (Asia/Calcutta).', '',
        'These are measured local operations against the disposable `chainguard_test` database, not production capacity estimates.', '',
        '## Method', '',
        '- Valid fixture histories were generated in O(N) with the unchanged `create_new_block` engine and inserted by PostgreSQL COPY. Seeding is reported separately and is **not** application append throughput.',
        '- Each size starts with N business records plus genesis. Timed `Store.append` calls use real row-locked transactions, full-history verification and duplicate search. Sizes grow by one after each accepted append. One additional untimed append measures Python allocations.',
        '- Customer cold samples each use a newly initialized verification cache; cache-hit samples reuse the final service with no intervening ledger mutation. Both call the actual `ShipmentAccessService.events` method with authorization, PostgreSQL access and a 25-record page.',
        '- Fixtures distribute business events across 100 shipment identifiers and include one signed checkpoint. They are labeled benchmark fixtures, not real logistics events.',
        '- Wall latency includes database round trips. Python CPU time excludes the PostgreSQL server. Tracemalloc peaks are separate instrumentation calls and are not included in latency samples; they exclude native allocations, whole-process RSS and database memory.',
        '- p50 uses the sample median; p95 uses the nearest-rank observation. The reported sample counts give only a small empirical sample, not a stable tail-latency estimate. OS CPU accounting can produce zero-valued short-operation samples; that does not imply no CPU work.',
        '- Seeding and validation warm database/OS caches. No HTTP transport, concurrent load generator or cold-storage restart is included. PostgreSQL CPU/RSS, I/O utilization and host-wide contention were not measured.', '',
        '## Results', '',
        '| Initial business records | Operation | Samples | Wall p50 ms | Wall p95 ms | Python CPU p50 ms | Separate Python peak MiB |',
        '|---:|---|---:|---:|---:|---:|---:|']
    for case in evidence['cases']:
        for key, label in [('append', 'Real append (full verification)'), ('customer_cold', 'Customer verification cache miss'),
                           ('customer_cache_hit', 'Customer verification cache hit')]:
            values = case[key]
            lines.append('| ' + ' | '.join(map(str, [case['initial_business_events'], label, values['count'],
                values['p50_ms'], values['p95_ms'], values['python_cpu_p50_ms'],
                round(values['separate_python_peak_bytes'] / 1048576, 3)])) + ' |')
    lines += ['', '## Interpretation', '',
        'Writes still perform O(N) complete-history processing by design. The measurements do not claim that this write path scales to enterprise event volumes. Indexed customer pages and revision-aware bounded verification reuse reduce repeated poll work; cached results retain their original check timestamp and bounded age, rather than becoming a new independent verification.', '',
        'Each owned benchmark ledger, its grants/checkpoints, temporary signing key and exact key-registration rows were cleaned after measurement. Existing application records and the original ledger database were not used.', '',
        '## Reproduction and complete observations', '',
        'Run `.venv\\Scripts\\python.exe tools/benchmark_ledger.py` with a configured local `LEDGER_TEST_DATABASE_URL`. The tool refuses any database name other than `chainguard_test`. Run it when the regression suite and other load generators are idle.', '',
        '```json', json.dumps(evidence, indent=2), '```', '']
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(lines), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sizes', nargs='+', type=int, default=[1000, 10000])
    parser.add_argument('--samples', type=int, default=10)
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/PERFORMANCE_MEASUREMENTS.md')
    args = parser.parse_args()
    if not 5 <= args.samples <= 20 or len(args.sizes) > 3 or any(not 100 <= size <= 20000 for size in args.sizes):
        parser.error('Use 5–20 samples and at most three initial sizes between 100 and 20000')
    if (ROOT / '.env').is_file():
        load_environment(ROOT / '.env')
    dsn = os.environ.get('LEDGER_TEST_DATABASE_URL', '')
    try:
        allowed = conninfo_to_dict(dsn).get('dbname') == 'chainguard_test'
    except Exception:
        allowed = False
    if not allowed:
        raise SystemExit('Benchmark requires the isolated chainguard_test database; no connection attempted')
    store = Store(dsn)
    with store.connect() as connection:
        version = connection.execute('SHOW server_version').fetchone()['server_version']
        statement = connection.execute('SHOW statement_timeout').fetchone()['statement_timeout']
        lock = connection.execute('SHOW lock_timeout').fetchone()['lock_timeout']
    evidence = {'measured_at': datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30))).isoformat(timespec='seconds'),
        'platform': platform.platform(), 'processor': platform.processor(), 'python_version': platform.python_version(),
        'postgresql_version': version, 'statement_timeout': statement, 'lock_timeout': lock,
        'concurrency': 'Sequential benchmark; regression suite and other load generators expected idle', 'cases': []}
    for size in args.sizes:
        print('Measuring owned fixture with', size, 'initial business records...', flush=True)
        evidence['cases'].append(run_case(store, size, args.samples))
    write_report(args.output, evidence)
    print('Measurements and exact samples written to', args.output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
