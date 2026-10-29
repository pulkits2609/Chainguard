"""Cheap process-local read gates for the documented single-process server.

These gates run before database work. Persisted integration and customer grant
limits remain separate; restart does not revoke or reset those security controls.
"""
import ipaddress
import math
import threading
import time


class ResourceLimitError(ValueError):
    def __init__(self, message, retry_after=1, record_audit=False):
        super().__init__(message)
        self.retry_after, self.record_audit = retry_after, record_audit


class ReadBudget:
    def __init__(self, reads_per_minute=60, customer_attempts_per_minute=30,
                 concurrent_work=4, max_sources=4096, clock=time.monotonic):
        for value in (reads_per_minute, customer_attempts_per_minute, concurrent_work, max_sources):
            if type(value) is not int or not 1 <= value <= 10000:
                raise ValueError('Read resource limits must be bounded positive integers')
        if concurrent_work > 8:
            raise ValueError('At most eight expensive requests may run concurrently')
        self.limits = {'ledger': reads_per_minute, 'customer': customer_attempts_per_minute}
        self.max_sources, self.clock = max_sources, clock
        self.buckets, self.lock = {}, threading.Lock()
        self.work = threading.BoundedSemaphore(concurrent_work)

    def check(self, peer_ip, group):
        address = str(ipaddress.ip_address(peer_ip))
        if group not in self.limits:
            raise ValueError('Unknown resource group')
        now = self.clock()
        window = int(now // 60)
        key = (address, group)
        with self.lock:
            if len(self.buckets) >= self.max_sources:
                self.buckets = {key: value for key, value in self.buckets.items() if value[0] == window}
            if key not in self.buckets and len(self.buckets) >= self.max_sources:
                raise ResourceLimitError('Read source capacity reached; retry later', max(1, math.ceil(60-now % 60)))
            previous, count = self.buckets.get(key, (window, 0))
            count = count + 1 if previous == window else 1
            limit = self.limits[group]
            self.buckets[key] = (window, min(count, limit + 2))
            if count > limit:
                raise ResourceLimitError('Too many read or capability attempts', max(1, math.ceil(60-now % 60)), count == limit + 1)

    def acquire(self):
        if not self.work.acquire(blocking=False):
            raise ResourceLimitError('Verification workers are busy; retry shortly')

    def release(self):
        self.work.release()
