"""Resource protection and freshness regressions for the source-review findings."""
from concurrent.futures import ThreadPoolExecutor
import os
import time
import unittest
from unittest.mock import patch

import test_webapp as fixtures
from request_limits import ReadBudget, ResourceLimitError


class ReadBudgetTests(unittest.TestCase):
    def test_source_limits_bound_memory_reset_and_preserve_peer_isolation(self):
        clock = [0.0]
        budget = ReadBudget(reads_per_minute=2, customer_attempts_per_minute=1,
                            max_sources=3, clock=lambda: clock[0])
        budget.check('127.0.0.1', 'ledger')
        budget.check('127.0.0.1', 'ledger')
        with self.assertRaises(ResourceLimitError) as error:
            budget.check('127.0.0.1', 'ledger')
        self.assertGreaterEqual(error.exception.retry_after, 1)
        budget.check('127.0.0.2', 'ledger')
        budget.check('127.0.0.1', 'customer')
        with self.assertRaises(ResourceLimitError):
            budget.check('127.0.0.3', 'ledger')
        clock[0] = 61
        budget.check('127.0.0.3', 'ledger')


@unittest.skipUnless(os.environ.get('LEDGER_TEST_DATABASE_URL'), 'Isolated PostgreSQL required')
class ReadHttpTests(unittest.TestCase):
    setUp = fixtures.WebTests.setUp
    tearDown = fixtures.WebTests.tearDown
    request = fixtures.WebTests.request
    register = fixtures.WebTests.register
    create = fixtures.WebTests.create

    def test_database_statement_and_lock_timeouts_are_effective(self):
        import psycopg
        with patch.dict(os.environ,{'LEDGER_DB_STATEMENT_TIMEOUT_MS':'50','LEDGER_DB_LOCK_TIMEOUT_MS':'10'}):
            with self.server.store.connect() as connection:
                self.assertEqual(connection.execute('SHOW statement_timeout').fetchone()['statement_timeout'],'50ms')
                self.assertEqual(connection.execute('SHOW lock_timeout').fetchone()['lock_timeout'],'10ms')
                with self.assertRaises(psycopg.errors.QueryCanceled):
                    connection.execute('SELECT pg_sleep(0.2)')

    def test_public_verify_and_invalid_customer_attempts_stop_before_database(self):
        _, cookie, csrf = self.register()
        ledger = self.create(cookie, csrf)['ledger']['id']
        self.server.read_budget = ReadBudget(reads_per_minute=2, customer_attempts_per_minute=2, clock=lambda:0.0)
        with patch.object(self.server.store, 'get_ledger', wraps=self.server.store.get_ledger) as lookup:
            self.assertEqual(self.request('POST', '/api/ledgers/'+ledger+'/verify', {})[0], 200)
            self.assertEqual(self.request('POST', '/api/ledgers/'+ledger+'/verify', {})[0], 200)
            before = lookup.call_count
            status, _, headers = self.request('POST', '/api/ledgers/'+ledger+'/verify', {})
            self.assertEqual(status, 429)
            self.assertIn('Retry-After', headers)
            self.assertEqual(lookup.call_count, before)
        with patch.object(self.server.shipments, 'events', wraps=self.server.shipments.events) as events:
            path = '/api/customer/shipments/GUESSED/events'
            for _ in range(2):
                self.assertEqual(self.request('GET', path, token='invalid')[0], 403)
            self.assertEqual(self.request('GET', path, token='invalid')[0], 429)
            self.assertEqual(events.call_count, 2)

    def test_concurrent_expensive_requests_leave_health_route_responsive(self):
        _, cookie, csrf = self.register()
        ledger = self.create(cookie, csrf)['ledger']['id']
        self.server.read_budget = ReadBudget(concurrent_work=1)
        original = self.server.store.read
        import threading
        entered, release = threading.Event(), threading.Event()
        def slow_read(identifier):
            entered.set()
            release.wait(3)
            return original(identifier)
        with patch.object(self.server.store, 'read', side_effect=slow_read), ThreadPoolExecutor(max_workers=4) as workers:
            first = workers.submit(self.request, 'GET', '/api/ledgers/'+ledger)
            self.assertTrue(entered.wait(2))
            try:
                started = time.monotonic()
                health = self.request('GET', '/api/me')
                rejected = self.request('GET', '/api/ledgers/'+ledger)
                self.assertEqual(health[0], 200)
                self.assertEqual(rejected[0], 429)
                self.assertLess(time.monotonic()-started, 2)
            finally:
                release.set()
            self.assertEqual(first.result(timeout=5)[0], 200)
