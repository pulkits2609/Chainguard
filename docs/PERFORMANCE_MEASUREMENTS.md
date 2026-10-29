# Ledger and customer-read measurements

Run: 2026-10-29T06:04:30+05:30 (Asia/Calcutta).

These are measured local operations against the disposable `chainguard_test` database, not production capacity estimates.

## Method

- Valid fixture histories were generated in O(N) with the unchanged `create_new_block` engine and inserted by PostgreSQL COPY. Seeding is reported separately and is **not** application append throughput.
- Each size starts with N business records plus genesis. Timed `Store.append` calls use real row-locked transactions, full-history verification and duplicate search. Sizes grow by one after each accepted append. One additional untimed append measures Python allocations.
- Customer cold samples each use a newly initialized verification cache; cache-hit samples reuse the final service with no intervening ledger mutation. Both call the actual `ShipmentAccessService.events` method with authorization, PostgreSQL access and a 25-record page.
- Fixtures distribute business events across 100 shipment identifiers and include one signed checkpoint. They are labeled benchmark fixtures, not real logistics events.
- Wall latency includes database round trips. Python CPU time excludes the PostgreSQL server. Tracemalloc peaks are separate instrumentation calls and are not included in latency samples; they exclude native allocations, whole-process RSS and database memory.
- p50 uses the sample median; p95 uses the nearest-rank observation. The reported sample counts give only a small empirical sample, not a stable tail-latency estimate. OS CPU accounting can produce zero-valued short-operation samples; that does not imply no CPU work.
- Seeding and validation warm database/OS caches. No HTTP transport, concurrent load generator or cold-storage restart is included. PostgreSQL CPU/RSS, I/O utilization and host-wide contention were not measured.

## Results

| Initial business records | Operation | Samples | Wall p50 ms | Wall p95 ms | Python CPU p50 ms | Separate Python peak MiB |
|---:|---|---:|---:|---:|---:|---:|
| 1000 | Real append (full verification) | 10 | 59.343 | 77.814 | 31.25 | 1.718 |
| 1000 | Customer verification cache miss | 10 | 63.176 | 157.943 | 31.25 | 1.72 |
| 1000 | Customer verification cache hit | 10 | 38.886 | 55.472 | 0.0 | 0.036 |
| 10000 | Real append (full verification) | 10 | 301.178 | 404.085 | 234.375 | 17.045 |
| 10000 | Customer verification cache miss | 10 | 283.887 | 408.213 | 234.375 | 17.047 |
| 10000 | Customer verification cache hit | 10 | 32.775 | 135.457 | 0.0 | 0.062 |

## Interpretation

Writes still perform O(N) complete-history processing by design. The measurements do not claim that this write path scales to enterprise event volumes. Indexed customer pages and revision-aware bounded verification reuse reduce repeated poll work; cached results retain their original check timestamp and bounded age, rather than becoming a new independent verification.

Each owned benchmark ledger, its grants/checkpoints, temporary signing key and exact key-registration rows were cleaned after measurement. Existing application records and the original ledger database were not used.

## Reproduction and complete observations

Run `.venv\Scripts\python.exe tools/benchmark_ledger.py` with a configured local `LEDGER_TEST_DATABASE_URL`. The tool refuses any database name other than `chainguard_test`. Run it when the regression suite and other load generators are idle.

```json
{
  "measured_at": "2026-10-29T06:04:30+05:30",
  "platform": "Windows-10-10.0.26300-SP0",
  "python_version": "3.11.3",
  "postgresql_version": "18.6",
  "statement_timeout": "5s",
  "lock_timeout": "2s",
  "concurrency": "Sequential benchmark; regression suite and other load generators expected idle",
  "cases": [
    {
      "initial_business_events": 1000,
      "fixture": {
        "business_events": 1000,
        "genesis_blocks": 1,
        "canonical_business_record_bytes": 386783,
        "seed_elapsed_seconds": 0.08,
        "method": "Canonical create_new_block fixtures inserted by one PostgreSQL COPY; not application append throughput"
      },
      "append": {
        "count": 10,
        "p50_ms": 59.343,
        "p95_ms": 77.814,
        "python_cpu_p50_ms": 31.25,
        "samples": [
          {
            "wall_ms": 61.07,
            "python_cpu_ms": 31.25
          },
          {
            "wall_ms": 73.377,
            "python_cpu_ms": 31.25
          },
          {
            "wall_ms": 77.814,
            "python_cpu_ms": 31.25
          },
          {
            "wall_ms": 55.531,
            "python_cpu_ms": 15.625
          },
          {
            "wall_ms": 57.615,
            "python_cpu_ms": 46.875
          },
          {
            "wall_ms": 53.399,
            "python_cpu_ms": 31.25
          },
          {
            "wall_ms": 63.493,
            "python_cpu_ms": 31.25
          },
          {
            "wall_ms": 54.464,
            "python_cpu_ms": 46.875
          },
          {
            "wall_ms": 48.965,
            "python_cpu_ms": 15.625
          },
          {
            "wall_ms": 70.485,
            "python_cpu_ms": 31.25
          }
        ],
        "separate_python_peak_bytes": 1801239
      },
      "customer_cold": {
        "count": 10,
        "p50_ms": 63.176,
        "p95_ms": 157.943,
        "python_cpu_p50_ms": 31.25,
        "samples": [
          {
            "wall_ms": 51.437,
            "python_cpu_ms": 31.25
          },
          {
            "wall_ms": 157.943,
            "python_cpu_ms": 46.875
          },
          {
            "wall_ms": 85.636,
            "python_cpu_ms": 78.125
          },
          {
            "wall_ms": 54.398,
            "python_cpu_ms": 31.25
          },
          {
            "wall_ms": 52.002,
            "python_cpu_ms": 31.25
          },
          {
            "wall_ms": 57.117,
            "python_cpu_ms": 15.625
          },
          {
            "wall_ms": 72.721,
            "python_cpu_ms": 31.25
          },
          {
            "wall_ms": 82.213,
            "python_cpu_ms": 31.25
          },
          {
            "wall_ms": 58.698,
            "python_cpu_ms": 31.25
          },
          {
            "wall_ms": 67.654,
            "python_cpu_ms": 15.625
          }
        ],
        "separate_python_peak_bytes": 1803144
      },
      "customer_cache_hit": {
        "count": 10,
        "p50_ms": 38.886,
        "p95_ms": 55.472,
        "python_cpu_p50_ms": 0.0,
        "samples": [
          {
            "wall_ms": 46.372,
            "python_cpu_ms": 0.0
          },
          {
            "wall_ms": 55.472,
            "python_cpu_ms": 15.625
          },
          {
            "wall_ms": 37.299,
            "python_cpu_ms": 15.625
          },
          {
            "wall_ms": 34.923,
            "python_cpu_ms": 0.0
          },
          {
            "wall_ms": 46.724,
            "python_cpu_ms": 0.0
          },
          {
            "wall_ms": 34.676,
            "python_cpu_ms": 0.0
          },
          {
            "wall_ms": 38.346,
            "python_cpu_ms": 0.0
          },
          {
            "wall_ms": 39.427,
            "python_cpu_ms": 15.625
          },
          {
            "wall_ms": 49.545,
            "python_cpu_ms": 0.0
          },
          {
            "wall_ms": 37.946,
            "python_cpu_ms": 15.625
          }
        ],
        "separate_python_peak_bytes": 37776,
        "unchanged_verification_timestamp": true
      },
      "customer_page_limit": 25,
      "shipment_count_in_fixture": 100,
      "selected_shipment_total": 11,
      "checkpoints_present": 1,
      "checkpoint_head_index": 1011,
      "final_total_blocks_including_genesis": 1012,
      "relation_bytes_including_indexes": 843776,
      "final_verification": "valid"
    },
    {
      "initial_business_events": 10000,
      "fixture": {
        "business_events": 10000,
        "genesis_blocks": 1,
        "canonical_business_record_bytes": 3887784,
        "seed_elapsed_seconds": 0.253,
        "method": "Canonical create_new_block fixtures inserted by one PostgreSQL COPY; not application append throughput"
      },
      "append": {
        "count": 10,
        "p50_ms": 301.178,
        "p95_ms": 404.085,
        "python_cpu_p50_ms": 234.375,
        "samples": [
          {
            "wall_ms": 299.109,
            "python_cpu_ms": 234.375
          },
          {
            "wall_ms": 262.551,
            "python_cpu_ms": 250.0
          },
          {
            "wall_ms": 395.071,
            "python_cpu_ms": 234.375
          },
          {
            "wall_ms": 303.248,
            "python_cpu_ms": 250.0
          },
          {
            "wall_ms": 404.085,
            "python_cpu_ms": 265.625
          },
          {
            "wall_ms": 263.793,
            "python_cpu_ms": 218.75
          },
          {
            "wall_ms": 258.078,
            "python_cpu_ms": 234.375
          },
          {
            "wall_ms": 288.572,
            "python_cpu_ms": 234.375
          },
          {
            "wall_ms": 322.697,
            "python_cpu_ms": 265.625
          },
          {
            "wall_ms": 369.991,
            "python_cpu_ms": 234.375
          }
        ],
        "separate_python_peak_bytes": 17872887
      },
      "customer_cold": {
        "count": 10,
        "p50_ms": 283.887,
        "p95_ms": 408.213,
        "python_cpu_p50_ms": 234.375,
        "samples": [
          {
            "wall_ms": 272.409,
            "python_cpu_ms": 250.0
          },
          {
            "wall_ms": 408.213,
            "python_cpu_ms": 250.0
          },
          {
            "wall_ms": 285.141,
            "python_cpu_ms": 218.75
          },
          {
            "wall_ms": 259.684,
            "python_cpu_ms": 218.75
          },
          {
            "wall_ms": 274.197,
            "python_cpu_ms": 234.375
          },
          {
            "wall_ms": 262.952,
            "python_cpu_ms": 218.75
          },
          {
            "wall_ms": 300.07,
            "python_cpu_ms": 265.625
          },
          {
            "wall_ms": 282.917,
            "python_cpu_ms": 234.375
          },
          {
            "wall_ms": 301.608,
            "python_cpu_ms": 250.0
          },
          {
            "wall_ms": 284.857,
            "python_cpu_ms": 218.75
          }
        ],
        "separate_python_peak_bytes": 17874702
      },
      "customer_cache_hit": {
        "count": 10,
        "p50_ms": 32.775,
        "p95_ms": 135.457,
        "python_cpu_p50_ms": 0.0,
        "samples": [
          {
            "wall_ms": 48.217,
            "python_cpu_ms": 0.0
          },
          {
            "wall_ms": 28.894,
            "python_cpu_ms": 0.0
          },
          {
            "wall_ms": 50.753,
            "python_cpu_ms": 0.0
          },
          {
            "wall_ms": 135.457,
            "python_cpu_ms": 0.0
          },
          {
            "wall_ms": 31.15,
            "python_cpu_ms": 0.0
          },
          {
            "wall_ms": 32.783,
            "python_cpu_ms": 0.0
          },
          {
            "wall_ms": 31.603,
            "python_cpu_ms": 0.0
          },
          {
            "wall_ms": 32.767,
            "python_cpu_ms": 0.0
          },
          {
            "wall_ms": 46.409,
            "python_cpu_ms": 0.0
          },
          {
            "wall_ms": 31.3,
            "python_cpu_ms": 0.0
          }
        ],
        "separate_python_peak_bytes": 65239,
        "unchanged_verification_timestamp": true
      },
      "customer_page_limit": 25,
      "shipment_count_in_fixture": 100,
      "selected_shipment_total": 101,
      "checkpoints_present": 1,
      "checkpoint_head_index": 10011,
      "final_total_blocks_including_genesis": 10012,
      "relation_bytes_including_indexes": 6684672,
      "final_verification": "valid"
    }
  ],
  "processor": "Intel64 Family 6 Model 183 Stepping 1, GenuineIntel"
}
```
