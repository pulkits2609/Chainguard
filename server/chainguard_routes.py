"""Focused ChainGuard administrative and customer routes on the existing server."""
from pathlib import Path
import os
import time
from urllib.parse import parse_qs, unquote

from checkpoints import CheckpointService
from shipment_access import ShipmentAccessService
from integrations import IntegrationService, IntegrationError
from security_audit import AuditService
from store import StoreForbidden, StoreNotFound
from psycopg.conninfo import conninfo_to_dict


def initialize_services(server, key_directory=None):
    database = conninfo_to_dict(server.store.database_url).get('dbname')
    testing = database == 'chainguard_test'
    directory = Path(key_directory or Path(__file__).resolve().parents[1] / '.local' / database)
    server.audit = AuditService(server.store, int(os.environ.get('LEDGER_AUDIT_RETENTION_DAYS', '30')))
    server.integrations = IntegrationService(server.store,
        secret_key_path=(None if testing else os.environ.get('LEDGER_INTEGRATION_KEY_PATH')) or directory / 'integration.key',
        max_requests_per_minute=int(os.environ.get('LEDGER_MAX_REQUESTS_PER_MINUTE', '600')),
        max_body_bytes=int(os.environ.get('LEDGER_MAX_BODY_BYTES', '65536')), audit=server.audit)
    server.checkpoints = CheckpointService(server.store,
        (None if testing else os.environ.get('LEDGER_SIGNING_KEY_PATH')) or directory / 'checkpoint.key', audit=server.audit)
    server.shipments = ShipmentAccessService(server.store, server.checkpoints,
        default_lifetime_seconds=int(os.environ.get('LEDGER_CUSTOMER_ACCESS_SECONDS', '86400')), audit=server.audit)


def query_values(query, allowed):
    values = parse_qs(query, keep_blank_values=True)
    if set(values) - set(allowed) or any(len(items) != 1 for items in values.values()):
        raise ValueError('Unsupported or repeated query parameter')
    return {key: items[0] for key, items in values.items()}


def pagination(query):
    values = query_values(query, ('limit', 'offset'))
    try:
        return {key: int(value) for key, value in values.items()}
    except ValueError:
        raise ValueError('Pagination values must be integers') from None


def require_owner(handler, ledger_id, csrf=False):
    user = handler.current_user(required=True, csrf=csrf)
    metadata = handler.server.store.get_ledger(ledger_id)
    if not user['is_admin'] and metadata['owner_id'] != user['id']:
        raise StoreNotFound('Ledger not found')
    return user


def bearer(handler):
    headers = handler.headers.get_all('Authorization', [])
    if len(headers) != 1 or not headers[0].startswith('Bearer ') or not 1 <= len(headers[0][7:]) <= 512:
        raise StoreForbidden('A valid bearer capability is required')
    return headers[0][7:]


def route(handler, parsed):
    server, method = handler.server, handler.command
    parts = parsed.path.strip('/').split('/')
    handled = True
    if parts == ['api', 'overview'] and method == 'GET':
        user = handler.current_user(required=True)
        ledgers = server.store.list_ledgers(user['id'], user['is_admin'])
        statuses = []
        # Bounded overview work; counts cover all owned ledgers and status checks cover the first 100.
        for ledger in ledgers[:100]:
            report = server.store.read(ledger['id'])['verification']
            statuses.append({'id': ledger['id'], 'name': ledger['name'], **report})
        recent_rejected, recent_failed = 0, 0
        if user['is_admin']:
            with server.store.connect() as connection:
                since = int(time.time() * 1000) - 86400000
                recent_rejected = connection.execute("SELECT COUNT(*) AS n FROM security_events WHERE timestamp_ms >= %s AND outcome = 'rejected'", (since,)).fetchone()['n']
                recent_failed = connection.execute("SELECT COUNT(*) AS n FROM security_events WHERE timestamp_ms >= %s AND category IN ('integrity', 'verification_failed') AND outcome IN ('failed', 'rejected')", (since,)).fetchone()['n']
        handler.send_json(200, {'total_ledgers': len(ledgers),
            'active_ledgers': sum(row['state'] == 'active' for row in ledgers),
            'total_blocks': sum(row['block_count'] for row in ledgers),
            'configured_integrations': len(server.integrations.list(user)), 'ledger_statuses': statuses,
            'statuses_truncated': len(ledgers) > 100, 'recent_rejected_requests': recent_rejected,
            'recent_verification_failures': recent_failed})
    elif parts == ['api', 'security', 'events'] and method == 'GET':
        user = handler.current_user(required=True, admin=True)
        handler.send_json(200, server.audit.list(user, query_values(parsed.query,
            ('severity', 'category', 'integration_id', 'ledger_id', 'from', 'to', 'limit', 'offset'))))
    elif parts[:2] == ['api', 'integrations']:
        if len(parts) == 4 and parts[3] == 'webhook' and method == 'POST':
            server.integrations.throttle(handler.client_address[0])
            raw = handler.body(raw=True)
            block, duplicate = server.integrations.ingest(parts[2], raw, handler.headers, handler.client_address[0], throttled=True)
            handler.send_json(200 if duplicate else 201, {'block': block, 'duplicate': duplicate})
        else:
            user = handler.current_user(required=True, csrf=method != 'GET')
            if len(parts) == 2 and method == 'GET':
                handler.send_json(200, {'integrations': server.integrations.list(user)})
            elif len(parts) == 2 and method == 'POST':
                handler.send_json(201, server.integrations.create(user, handler.body()))
            elif len(parts) == 3 and method == 'GET':
                handler.send_json(200, {'integration': server.integrations.get(parts[2], user)})
            elif len(parts) == 3 and method == 'PATCH':
                handler.send_json(200, {'integration': server.integrations.update(parts[2], user, handler.body())})
            elif len(parts) == 3 and method == 'DELETE':
                handler.body()
                server.integrations.delete(parts[2], user)
                handler.send_json(200, {'deleted': True})
            elif len(parts) == 4 and parts[3] == 'rotate-secret' and method == 'POST':
                handler.body()
                handler.send_json(200, server.integrations.rotate_secret(parts[2], user))
            elif len(parts) == 4 and parts[3] == 'policy' and method in ('GET', 'PATCH'):
                policy = server.integrations.policy(parts[2], user) if method == 'GET' else server.integrations.update_policy(parts[2], user, handler.body())
                handler.send_json(200, {'policy': policy})
            else:
                raise StoreNotFound('API route not found')
    elif len(parts) == 3 and parts[:2] == ['api', 'verification-keys'] and method == 'GET':
        handler.send_json(200, server.checkpoints.public_key(parts[2]))
    elif len(parts) == 5 and parts[:3] == ['api', 'customer', 'shipments'] and method == 'GET':
        token = bearer(handler)
        shipment = unquote(parts[3])
        if parts[4] == 'events':
            handler.send_json(200, server.shipments.events(shipment, token, **pagination(parsed.query)))
        elif parts[4] == 'proof':
            if parsed.query:
                raise ValueError('Proof accepts no query parameters')
            handler.send_json(200, server.shipments.proof(shipment, token))
        else:
            raise StoreNotFound('API route not found')
    elif len(parts) >= 4 and parts[:2] == ['api', 'ledgers'] and parts[3] in (
            'signed-checkpoints', 'verify-checkpoint', 'shipment-access', 'history-export'):
        ledger_id, operation = parts[2:4]
        user = require_owner(handler, ledger_id, csrf=method != 'GET')
        if operation == 'signed-checkpoints' and len(parts) == 4 and method == 'POST':
            if handler.body() != {}:
                raise ValueError('Expected an empty checkpoint request')
            checkpoint = server.checkpoints.create(ledger_id)
            handler.send_json(201, {'checkpoint': checkpoint})
        elif operation == 'signed-checkpoints' and len(parts) == 4 and method == 'GET':
            handler.send_json(200, server.checkpoints.list(ledger_id, **pagination(parsed.query)))
        elif operation == 'signed-checkpoints' and len(parts) == 5 and method == 'GET':
            handler.send_json(200, server.checkpoints.get(ledger_id, parts[4]))
        elif operation == 'verify-checkpoint' and len(parts) == 4 and method == 'POST':
            value = handler.body()
            if not isinstance(value, dict) or set(value) != {'checkpoint'}:
                raise ValueError('Expected a checkpoint object')
            report = server.checkpoints.verify(ledger_id, value['checkpoint'])
            if not report['valid']:
                server.audit.record('integrity', 'verify', 'failed', 'checkpoint_mismatch', ledger_id=ledger_id, severity='error')
            handler.send_json(200, report)
        elif operation == 'history-export' and len(parts) == 4 and method == 'GET':
            view = server.store.read(ledger_id)
            view['ledger'] = handler.metadata(view['ledger'], user)
            handler.send_json(200, view)
        elif operation == 'shipment-access' and len(parts) == 4 and method == 'POST':
            value = handler.body()
            if not isinstance(value, dict) or not {'shipment_id'} <= set(value) or set(value) - {'shipment_id', 'expires_at'}:
                raise ValueError('Expected shipment_id and optional expires_at')
            result = server.shipments.create(ledger_id, user=user, **value)
            handler.send_json(201, result)
        elif operation == 'shipment-access' and len(parts) == 4 and method == 'GET':
            handler.send_json(200, server.shipments.list(ledger_id, user=user, **pagination(parsed.query)))
        elif operation == 'shipment-access' and len(parts) == 5 and method == 'DELETE':
            handler.body()
            result = server.shipments.revoke(ledger_id, parts[4], user=user)
            handler.send_json(200, result)
        else:
            raise StoreNotFound('API route not found')
    else:
        handled = False
    return handled
