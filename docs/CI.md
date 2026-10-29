# Continuous integration

`.github/workflows/ci.yml` runs on pushes, pull requests and manual dispatch. The job uses an Ubuntu 24.04 runner, PostgreSQL 18 service, Python 3.12 and Node.js 24. The three GitHub-maintained actions are pinned to full upstream v6 commit SHAs, verified from their official repositories when this workflow was added. Its service-container setup follows [GitHub's PostgreSQL service documentation](https://docs.github.com/en/actions/tutorials/use-containerized-services/create-postgresql-service-containers).

The workflow installs the existing pinned Python requirements and locked frontend dependencies. In its disposable PostgreSQL service, it creates an ordinary non-superuser application role owning `chainguard` and `chainguard_test`. The public CI-only credentials are examples for that temporary service, not deployment credentials; no repository secrets or local `.env` are required.

Verification includes:

1. The complete backend and launcher Python suites, frontend tests and production build through `python -B tools/run_checks.py --frontend`.
2. Repeatable initialization of the application database and protected keys, performed twice.
3. A loopback Ubuntu application startup smoke: registration, private template creation, denial of anonymous private access, application restart, login to the existing account, unchanged ledger history and logout.
4. Bash syntax checks for both deployment entry points.

The job has a 20-minute timeout, read-only repository permissions, no persisted checkout credentials, and cancellation of superseded runs on the same ref. It does not use `pull_request_target`, production databases, deployment keys or the separately installed AudioShare application. CI never uploads `.env`, signing keys, HMAC keys or local logs.

The runner accepts `LEDGER_TEST_DATABASE_URL` from its process environment even when no `.env` exists. Local `.env` remains supported, while an explicit environment value wins. The runner rejects missing, malformed or non-`chainguard_test` targets before running any checks.

## Evidence boundary

Adding a workflow is not a passing hosted run. Hosted Ubuntu/PostgreSQL verification is established only by the exact pushed commit's successful [GitHub Actions result](https://github.com/pulkits2609/Chainguard/actions/workflows/ci.yml). Check that revision's status before describing CI as passing; the workflow file and local test results alone do not establish it.

Even a green job demonstrates direct application startup and restart on Ubuntu, not systemd installation, reboot survival, TLS certificate renewal, backup/restore or a complete production deployment. Those remain separate operational checks. The external AudioShare acceptance harness remains an explicitly separate local test because that independently controlled application's binaries are not included in this repository.
