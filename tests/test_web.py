"""Real ASGI upload/route and trusted-worker result-integrity regression tests."""
import csv
import io
import json
import os
from pathlib import Path
import sys
import zipfile

import pytest
from fastapi.testclient import TestClient
from PIL import Image

APP = Path(__file__).resolve().parents[1] / 'cpu-mewc-web/roles/mewc_web/files/app'
sys.path.insert(0, str(APP))
from app import main, storage, worker
from app.md_to_csv import validate, convert


@pytest.fixture
def area(tmp_path, monkeypatch):
    root = tmp_path / 'mewc-web'
    for name in ('inbox', 'jobs', 'requests', 'cancel', 'tmp', 'work'):
        (root / name).mkdir(parents=True)
    monkeypatch.setattr(storage, 'MOUNT', tmp_path)
    monkeypatch.setattr(storage, 'ROOT', root)
    monkeypatch.setattr(storage, 'mounted', lambda: None)
    monkeypatch.setattr(main, 'RESERVE', 0)
    monkeypatch.setattr(main, 'MAX_BYTES', 1024**2)
    monkeypatch.setattr(main, 'EXPANDED', 1024**2)
    monkeypatch.setattr(worker, 'RESERVE', 0)
    monkeypatch.setenv('DETECT_IMAGE', 'example/detect@sha256:' + 'a' * 64)
    return root


@pytest.fixture
def client(area):
    with TestClient(main.app, base_url='http://localhost') as client:
        yield client


def png():
    output = io.BytesIO()
    Image.new('RGB', (4, 4)).save(output, format='PNG')
    return output.getvalue()


def upload(client, name='a.png'):
    response = client.post('/jobs', files=[('files', (name, png(), 'image/png'))], follow_redirects=False)
    assert response.status_code == 303, response.text
    return response.headers['location'].split('/')[-1]


def md(names, detections=None):
    return {'detection_categories': {'1': 'animal'}, 'images': [{'file': name, 'detections': [] if detections is None else detections} for name in names]}


@pytest.mark.parametrize('name', ['/tmp/escape.png', '../outside.png', 'a/../../outside.png', 'C:/escape.png', 'a\\escape.png', 'a//escape.png'])
def test_upload_rejects_escape(client, area, name):
    response = client.post('/jobs', files=[('files', (name, png(), 'image/png'))])
    assert response.status_code == 400
    assert not list((area / 'inbox').iterdir())


def test_duplicate_direct_and_zip(client, area):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('a.png', png())
    response = client.post('/jobs', files=[('files', ('a.png', png())), ('zipfile_upload', ('x.zip', archive.getvalue()))])
    assert response.status_code == 400
    assert not list((area / 'inbox').iterdir())


@pytest.mark.parametrize('kind', ['absolute', 'symlink', 'expansion', 'count', 'invalid'])
def test_zip_boundary(client, area, monkeypatch, kind):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        if kind == 'absolute':
            z.writestr('/escape.png', png())
        elif kind == 'symlink':
            info = zipfile.ZipInfo('a.png')
            info.external_attr = 0o120777 << 16
            z.writestr(info, '/etc/passwd')
        elif kind == 'expansion':
            monkeypatch.setattr(main, 'EXPANDED', 10)
            z.writestr('a.png', png())
        elif kind == 'count':
            monkeypatch.setattr(main, 'MAX_FILES', 1)
            z.writestr('a.png', png())
            z.writestr('b.png', png())
        else:
            z.writestr('a.png', 'not an image')
    response = client.post('/jobs', files={'zipfile_upload': ('x.zip', archive.getvalue())})
    assert response.status_code == 400
    assert not list((area / 'inbox').iterdir())


def test_measured_body_limit(client, area, monkeypatch):
    monkeypatch.setattr(main, 'MAX_BYTES', 20)
    response = client.post('/jobs', files={'files': ('a.png', png())})
    assert response.status_code == 413
    assert not list((area / 'inbox').iterdir())


def test_chunked_measured_limit(client, monkeypatch):
    monkeypatch.setattr(main, 'MAX_BYTES', 20)
    response = client.post('/jobs', content=iter([b'x' * 12, b'x' * 12]), headers={'Content-Type': 'multipart/form-data; boundary=X'})
    assert response.status_code == 413


def test_parser_file_limit(client, area):
    response = client.post('/jobs', files=[('files', (f'{i}.png', png())) for i in range(102)])
    assert response.status_code == 400
    assert not list((area / 'inbox').iterdir())


def test_no_mount_no_creation(client, area, monkeypatch):
    def unavailable():
        raise ValueError('Research volume is not mounted')
    monkeypatch.setattr(storage, 'mounted', unavailable)
    response = client.post('/jobs', files={'files': ('a.png', png())})
    assert response.status_code == 503
    assert not list((area / 'inbox').iterdir())


def test_missing_job_reads_do_not_create(client, area):
    assert client.get('/jobs/' + 'a' * 32).status_code == 404
    assert client.post('/jobs/bad/start').status_code == 404
    assert not list((area / 'jobs').iterdir())


def test_cross_site_start_rejected(client):
    jid = upload(client)
    assert client.post(f'/jobs/{jid}/start', headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403


def test_queue_duplicate(client):
    jid = upload(client)
    assert client.post(f'/jobs/{jid}/start').status_code == 200
    assert client.post(f'/jobs/{jid}/start').status_code == 409
    assert client.get(f'/jobs/{jid}/status').json()['status'] == 'queued'


def test_snapshot_symlink_component(client, area):
    jid = upload(client)
    base = area / 'inbox' / jid
    (base / 'uploads' / 'a.png').unlink()
    (base / 'uploads' / 'a.png').symlink_to('/etc/passwd')
    job = area / 'jobs' / jid
    job.mkdir()
    with pytest.raises(OSError):
        worker.snapshot(jid, job)


@pytest.mark.parametrize('failure', ['exit', 'missing', 'invalid', 'incomplete', 'duplicate', 'cancel', 'timeout'])
def test_worker_never_publishes_failed_attempt(client, area, monkeypatch, failure):
    jid = upload(client)
    def fake(args, jid, log, timeout):
        work = next((area / 'work').iterdir())
        if failure == 'exit':
            (work / 'md.json').write_text(json.dumps(md(['a.png'])))
            raise ValueError('exit 1')
        if failure == 'cancel':
            raise InterruptedError('cancelled')
        if failure == 'timeout':
            raise TimeoutError('timed out')
        if failure == 'invalid':
            (work / 'md.json').write_text('{')
        if failure == 'incomplete':
            (work / 'md.json').write_text(json.dumps(md([])))
        if failure == 'duplicate':
            (work / 'md.json').write_text(json.dumps(md(['a.png', 'a.png'])))
    monkeypatch.setattr(worker, 'execute', fake)
    result = worker.run(jid)
    assert result['status'] in ('failed', 'cancelled')
    assert result['processed'] == 0
    assert result['total'] == 1
    assert client.get(f'/jobs/{jid}/download/md.json').status_code == 409
    assert not list((area / 'work').iterdir())


def test_valid_empty_then_stale_retry(client, area, monkeypatch):
    jid = upload(client, 'folder/a #?.png')
    def fake(args, jid, log, timeout):
        work = next((area / 'work').iterdir())
        (work / 'md.json').write_text(json.dumps(md(['folder/a #?.png'])))
    monkeypatch.setattr(worker, 'execute', fake)
    result = worker.run(jid)
    assert result['status'] == 'succeeded'
    assert result['empty_images'] == result['processed'] == 1
    assert client.get(f'/jobs/{jid}/files/raw/folder/a%20%23%3F.png').status_code == 200
    monkeypatch.setattr(worker, 'execute', lambda *args: None)
    retry = worker.run(jid)
    assert retry['attempt'] != result['attempt']
    assert retry['status'] == 'failed'
    assert client.get(f'/jobs/{jid}/download/md.json').status_code == 409


def test_partial_zero_confidence_and_csv(client, area, monkeypatch):
    jid = upload(client)
    data = md(['a.png'], [{'category': '1', 'conf': 0.0, 'bbox': [0, 0, 1, 1]}])
    output = area / 'test.csv'
    assert validate(data, ['a.png'])['processed'] == 1
    convert(data, output)
    row = list(csv.DictReader(output.open()))[0]
    assert row['confidence'] == '0.0'
    assert row['label'] == 'animal'
    data['images'][0].update(detections=None, failure='decode failure')
    def fake(args, jid, log, timeout):
        (next((area / 'work').iterdir()) / 'md.json').write_text(json.dumps(data))
    monkeypatch.setattr(worker, 'execute', fake)
    result = worker.run(jid)
    assert result['status'] == 'partial'
    assert result['failed_images'] == 1 and result['processed'] == 0
    assert client.get(f'/jobs/{jid}/summary').json()['total'] == 0


def test_unset_image_fails_closed(client, monkeypatch):
    jid = upload(client)
    monkeypatch.delenv('DETECT_IMAGE')
    assert worker.run(jid)['status'] == 'failed'


def test_raw_sibling_and_symlink_rejected(client, area, monkeypatch):
    jid = upload(client)
    job = area / 'jobs' / jid
    job.mkdir()
    worker.snapshot(jid, job)
    assert client.get(f'/jobs/{jid}/files/raw/..%2Fuploads-evil%2Fsecret.png').status_code == 404
    (job / 'uploads' / 'a.png').unlink()
    (job / 'uploads' / 'a.png').symlink_to('/etc/passwd')
    assert client.get(f'/jobs/{jid}/files/raw/a.png').status_code == 404


@pytest.mark.parametrize('record', [None, [{'category': '1', 'conf': float('nan'), 'bbox': [0, 0, 1, 1]}], [{'category': '1', 'conf': 1, 'bbox': [0.9, 0, 1, 1]}]])
def test_schema_rejects_invalid_record(record):
    data = md(['a.png'])
    data['images'][0]['detections'] = record
    with pytest.raises(ValueError):
        validate(data, ['a.png'])


def test_resources_and_immutable_model(area, monkeypatch):
    model = area.parent / 'models' / 'model.pt'
    model.parent.mkdir()
    model.write_bytes(b'model')
    monkeypatch.setenv('DETECT_MODEL', 'model.pt')
    args = worker.docker_args('a' * 32, 'b' * 32, area / 'work', worker.image_config())
    assert '--memory' in args and '--pids-limit' in args and '--cpus' in args
    assert any('dst=/code/mewc-model.pt,readonly' in item for item in args)
    assert '-e' in args and 'MD_MODEL=mewc-model.pt' in args


def test_execute_nonzero_cleans_container(area, tmp_path, monkeypatch):
    executable = tmp_path / 'fake-detector'
    executable.write_text('#!/bin/sh\necho failed\nexit 9\n')
    executable.chmod(0o755)
    cleaned = []
    monkeypatch.setattr(worker, 'cleanup', lambda jid: cleaned.append(jid))
    with pytest.raises(ValueError, match='status 9'):
        worker.execute([str(executable)], 'a' * 32, tmp_path / 'log', 10)
    assert cleaned == ['a' * 32]


def test_execute_timeout_cleans_container(area, tmp_path, monkeypatch):
    cleaned = []
    monkeypatch.setattr(worker, 'cleanup', lambda jid: cleaned.append(jid))
    with pytest.raises(TimeoutError):
        worker.execute([sys.executable, '-c', 'import time; time.sleep(20)'], 'a' * 32, tmp_path / 'log', 0)
    assert cleaned == ['a' * 32]


def test_dns_rebinding_and_origin_rejected(client):
    assert client.get('/', headers={'Host': 'attacker.example'}).status_code == 400
    jid = upload(client)
    assert client.post(f'/jobs/{jid}/start', headers={'Origin': 'https://attacker.example'}).status_code == 403


def test_cancel_queued_without_execution(client, area, monkeypatch):
    jid = upload(client)
    client.post(f'/jobs/{jid}/start')
    assert client.post(f'/jobs/{jid}/cancel').status_code == 200
    monkeypatch.setattr(worker, 'execute', lambda *args: pytest.fail('Cancelled job executed'))
    assert worker.run(jid)['status'] == 'cancelled'


def test_cancel_terminal_rejected(client):
    jid = upload(client)
    assert client.post(f'/jobs/{jid}/cancel').status_code == 409


def test_single_worker_lock(area):
    import fcntl
    with (area / 'work' / 'worker.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            worker.serve()


def test_restart_reconciles_and_removes_attempt(area, monkeypatch):
    jid, attempt = 'a' * 32, 'b' * 32
    job = area / 'jobs' / jid
    job.mkdir()
    storage.atomic_json(job / 'state.json', {'status': 'running', 'attempt': attempt, 'processed': 0, 'total': 2})
    (area / 'work' / attempt).mkdir()
    (area / 'work' / attempt / 'partial.json').write_text('{}')
    cleaned = []
    monkeypatch.setattr(worker, 'cleanup', lambda jid: cleaned.append(jid))
    monkeypatch.setattr(worker, 'STOP', False)
    monkeypatch.setattr(worker.time, 'sleep', lambda seconds: monkeypatch.setattr(worker, 'STOP', True))
    worker.serve()
    assert cleaned == [jid]
    assert storage.state(jid)['status'] == 'failed'
    assert not (area / 'work' / attempt).exists()


def test_converter_failure_with_valid_json_does_not_publish(client, area, monkeypatch):
    jid = upload(client)
    def fake(*args):
        (next((area / 'work').iterdir()) / 'md.json').write_text(json.dumps(md(['a.png'])))
    def broken(*args):
        raise OSError('disk full')
    monkeypatch.setattr(worker, 'execute', fake)
    monkeypatch.setattr(worker, 'convert', broken)
    assert worker.run(jid)['status'] == 'failed'
    assert client.get(f'/jobs/{jid}/download/md.json').status_code == 409


def test_source_snapshot_is_immutable_on_retry(client, area):
    jid = upload(client)
    job = area / 'jobs' / jid
    job.mkdir()
    first = worker.snapshot(jid, job)
    (area / 'inbox' / jid / 'uploads' / 'a.png').write_bytes(b'modified')
    second = worker.snapshot(jid, job)
    assert second == first
    assert (job / 'uploads' / 'a.png').read_bytes() == png()


def test_execute_cancel_cleans_container(area, tmp_path, monkeypatch):
    jid = 'a' * 32
    (area / 'cancel' / jid).touch()
    cleaned = []
    monkeypatch.setattr(worker, 'cleanup', lambda jid: cleaned.append(jid))
    with pytest.raises(InterruptedError):
        worker.execute([sys.executable, '-c', 'import time; time.sleep(20)'], jid, tmp_path / 'log', 60)
    assert cleaned == [jid]


@pytest.mark.parametrize('rm_status,ls_status,listed', [(1, 1, ''), (1, 0, 'deadbeef'), (0, 1, '')])
def test_failed_removal_is_cleanup_pending(area, monkeypatch, rm_status, ls_status, listed):
    import subprocess
    def fake(args, **kwargs):
        return subprocess.CompletedProcess(args, rm_status if args[1] == 'rm' else ls_status, stdout=listed)
    monkeypatch.setattr(worker.subprocess, 'run', fake)
    with pytest.raises(worker.CleanupPending):
        worker.cleanup('a' * 32)


def test_already_absent_requires_successful_daemon_query(area, monkeypatch):
    import subprocess
    monkeypatch.setattr(worker.subprocess, 'run', lambda args, **kw: subprocess.CompletedProcess(args, 1 if args[1] == 'rm' else 0, stdout=''))
    worker.cleanup('a' * 32)


def test_pending_cleanup_retains_work_and_blocks_queue(client, area, monkeypatch):
    jid = upload(client)
    def failed(*args):
        raise worker.CleanupPending('daemon unavailable')
    monkeypatch.setattr(worker, 'execute', failed)
    result = worker.run(jid)
    assert result['status'] == 'cleanup_pending'
    workspace = area / 'work' / result['attempt']
    assert workspace.exists()
    monkeypatch.setattr(worker, 'cleanup', failed)
    assert worker.reconcile() is False
    assert storage.state(jid)['status'] == 'cleanup_pending'
    assert workspace.exists()
    second = upload(client, 'b.png')
    client.post(f'/jobs/{second}/start')
    monkeypatch.setattr(worker, 'run', lambda jid: pytest.fail('Admitted a job before cleanup'))
    monkeypatch.setattr(worker, 'STOP', False)
    monkeypatch.setattr(worker.time, 'sleep', lambda seconds: monkeypatch.setattr(worker, 'STOP', True))
    worker.serve()
    assert (area / 'requests' / second).exists()
    monkeypatch.setattr(worker, 'cleanup', lambda jid: None)
    assert worker.reconcile() is True
    assert storage.state(jid)['status'] == 'failed'
    assert not workspace.exists()


def test_cleanup_failure_overrides_cancelled_status(area, tmp_path, monkeypatch):
    jid = 'a' * 32
    (area / 'cancel' / jid).touch()
    def failed(jid):
        raise worker.CleanupPending('daemon unavailable')
    monkeypatch.setattr(worker, 'cleanup', failed)
    with pytest.raises(worker.CleanupPending):
        worker.execute([sys.executable, '-c', 'import time; time.sleep(20)'], jid, tmp_path / 'log', 60)


def test_queued_retry_blocks_previous_result_and_reloads_as_queued(client, area, monkeypatch):
    jid = upload(client)
    def fake(*args):
        (next((area / 'work').iterdir()) / 'md.json').write_text(json.dumps(md(['a.png'])))
    monkeypatch.setattr(worker, 'execute', fake)
    assert worker.run(jid)['status'] == 'succeeded'
    assert client.get(f'/jobs/{jid}/download/md.json').status_code == 200
    assert client.post(f'/jobs/{jid}/start').status_code == 200
    assert client.get(f'/jobs/{jid}/download/md.json').status_code == 409
    assert client.get(f'/jobs/{jid}/summary').status_code == 409
    page = client.get(f'/jobs/{jid}').text
    assert 'st.textContent = "queued"' in page
    assert 'st.textContent = "succeeded"' not in page
