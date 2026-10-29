"""Recover only owned disposable ledger tables, retaining trusted keys/evidence.

This is a table-data recovery drill, not a destructive full-database restore.
The original/application databases and unrelated test resources are never targets.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
from checkpoints import CheckpointService, verify_checkpoint
from multi_snapshots import SnapshotService
from settings import load_environment
from store import Store, table_names
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict


def test_connection(value):
    parameters = conninfo_to_dict(value)
    if parameters.get('dbname') != 'chainguard_test':
        raise ValueError('Recovery verification requires exactly chainguard_test')
    return parameters


def database_environment(parameters):
    environment = os.environ.copy()
    for key, variable in (('host','PGHOST'),('port','PGPORT'),('dbname','PGDATABASE'),
                          ('user','PGUSER'),('password','PGPASSWORD'),('sslmode','PGSSLMODE')):
        if key in parameters:
            environment[variable] = parameters[key]
        else:
            environment.pop(variable, None)
    return environment


def run_database_tool(command, environment):
    options = {'env': environment, 'stdin': subprocess.DEVNULL, 'capture_output': True,
               'text': True, 'timeout': 45, 'check': False}
    if os.name == 'nt':
        options['creationflags'] = subprocess.CREATE_NO_WINDOW
    result = subprocess.run(command, **options)
    if result.returncode:
        # Do not expose credentials, source connection strings, archive data or raw stderr.
        raise RuntimeError(Path(command[0]).name + ' failed during the disposable recovery drill')


def private_file_access(path):
    if os.name != 'nt':
        mode = path.stat().st_mode & 0o777
        return {'platform': 'POSIX', 'mode': oct(mode), 'result': 'PASS' if mode & 0o077 == 0 else 'FAIL'}
    command = """$acl=Get-Acl -LiteralPath $env:CHAINGUARD_ACL_PATH;
        @($acl.Access | ForEach-Object { @{sid=$_.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value; rights=[int]$_.FileSystemRights; type=[string]$_.AccessControlType} }) | ConvertTo-Json -Compress"""
    result = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',command],
        env={**os.environ,'CHAINGUARD_ACL_PATH':str(path)}, capture_output=True, text=True,
        timeout=10, check=False, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        return {'platform':'Windows','result':'UNVERIFIED','reason':'ACL inspection failed'}
    entries = json.loads(result.stdout or '[]')
    if isinstance(entries,dict):
        entries=[entries]
    broad={'S-1-1-0','S-1-5-11','S-1-5-32-545'}
    broad_read=any(entry['type']=='Allow' and entry['sid'] in broad and entry['rights'] & 1 for entry in entries)
    return {'platform':'Windows','result':'FAIL' if broad_read else 'PASS',
            'broad_group_read_allow':broad_read,
            'scope':'No Everyone/Authenticated Users/Users read-data allowance; authorized account/admin ACLs retained'}


def verify_recovery(database_url):
    parameters=test_connection(database_url)
    dump, restore = shutil.which('pg_dump'), shutil.which('pg_restore')
    if not dump or not restore:
        raise RuntimeError('Install PostgreSQL client tools pg_dump and pg_restore before the recovery drill')
    environment=database_environment(parameters)
    store=Store(database_url)
    ledger_id=key_id=None
    with tempfile.TemporaryDirectory(prefix='chainguard-recovery-') as directory:
        temporary=Path(directory)
        if os.name!='nt':
            temporary.chmod(0o700)
        try:
            created=store.create_ledger('recovery-'+uuid.uuid4().hex,'Disposable recovery fixture','',
                [{'name':'value','type':'integer','required':True}])
            ledger_id=created['ledger']['id']
            for index in range(12):
                store.append(ledger_id,created['write_token'],{'value':index},'recovery-'+str(index))
            history=store.read(ledger_id)
            signer=CheckpointService(store,temporary/'checkpoint.key')
            key_id=signer.verification_key['key_id']
            checkpoint=signer.create(ledger_id)
            snapshot,_=SnapshotService(store,ledger_id).export()
            signing_hash=hashlib.sha256((temporary/'checkpoint.key').read_bytes()).hexdigest()
            signing_access=private_file_access(temporary/'checkpoint.key')
            if signing_access['result']!='PASS':
                raise RuntimeError('Temporary signing-key access is not verified private')
            tables=table_names(ledger_id)
            archive=temporary/'owned-ledger.dump'
            run_database_tool([dump,'--format=custom','--no-owner','--no-privileges',
                '--table=public.'+tables[0],'--table=public.'+tables[1],
                '--file='+str(archive)],environment)
            if os.name!='nt':
                archive.chmod(0o600)
            archive_access=private_file_access(archive)
            if archive_access['result']!='PASS':
                raise RuntimeError('Temporary sensitive archive access is not verified private')
            # Table identifiers are generated UUID names, frozen before destructive fixture work.
            with store.connect() as connection:
                connection.execute(sql.SQL('TRUNCATE TABLE {}, {}').format(*(sql.Identifier(table) for table in tables)))
            if store.read(ledger_id)['verification']['valid']:
                raise RuntimeError('The recovery drill failed to remove its fixture records')
            run_database_tool([restore,'--data-only','--no-owner','--no-privileges',
                '--single-transaction','--exit-on-error','--dbname=chainguard_test',str(archive)],environment)
            restored=store.read(ledger_id)
            if restored['blocks']!=history['blocks'] or not restored['verification']['valid']:
                raise RuntimeError('Restored fixture history differs from the original')
            if not SnapshotService(store,ledger_id).validate(snapshot)['valid']:
                raise RuntimeError('Original encrypted snapshot does not validate after recovery')
            result=verify_checkpoint(checkpoint,signer.verification_key,ledger_id,restored)
            if not result['history_verified']:
                raise RuntimeError('Retained trusted checkpoint does not verify restored history')
            restarted=CheckpointService(store,temporary/'checkpoint.key')
            if hashlib.sha256((temporary/'checkpoint.key').read_bytes()).hexdigest()!=signing_hash or restarted.verification_key!=signer.verification_key:
                raise RuntimeError('The recovery drill changed the retained signing key')
            return {'result':'PASS','database':'chainguard_test','recovered_records_including_genesis':len(restored['blocks']),
                'pg_dump_custom_archive':True,'pg_restore_data_only':True,'original_block_hashes_preserved':True,
                'encrypted_snapshot_valid':True,'retained_checkpoint_full_history_valid':True,
                'retained_signing_key_unchanged':True,'signing_key_access':signing_access,'archive_access':archive_access,
                'scope':'Owned ledger block/secret table data restored; registry, checkpoint metadata and protected signing key retained',
                'full_database_disaster_recovery_tested':False,'original_or_application_database_used':False}
        finally:
            if ledger_id:
                store.delete_ledger(ledger_id)
            if key_id:
                with store.connect() as connection:
                    connection.execute('DELETE FROM signing_key_bindings WHERE key_id=%s',(key_id,))
                    connection.execute('DELETE FROM verification_keys WHERE key_id=%s',(key_id,))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence',type=Path,default=ROOT/'test-output/backup-restore-evidence.json')
    arguments=parser.parse_args()
    load_environment(ROOT/'.env')
    try:
        result=verify_recovery(os.environ.get('LEDGER_TEST_DATABASE_URL',''))
    except (ValueError,RuntimeError) as error:
        result={'result':'PENDING','reason':str(error)}
    except Exception as error:
        result={'result':'PENDING','reason':type(error).__name__+'; inspect the isolated drill privately'}
    result['checked_at']=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')
    arguments.evidence.parent.mkdir(parents=True,exist_ok=True)
    arguments.evidence.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,indent=2))
    return 0 if result['result']=='PASS' else 1


if __name__=='__main__':
    raise SystemExit(main())
