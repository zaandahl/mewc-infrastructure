"""One trusted detector worker. Spool messages contain job IDs only."""
import fcntl
import hashlib
import json
import os
import re
import selectors
import shutil
import signal
import subprocess
import time
import uuid

from . import storage as store
from .md_to_csv import convert, validate

STOP = False
MAX_BYTES = int(os.getenv('MEWC_MAX_EXPANDED_MB', '2048')) * 1024**2
MAX_FILES = int(os.getenv('MEWC_MAX_FILES', '10000'))
RESERVE = int(os.getenv('MEWC_RESERVE_MB', '2048')) * 1024**2


def stop(signum, frame):
    global STOP
    STOP = True


def image_config():
    image = os.environ.get('DETECT_IMAGE', '')
    if not re.fullmatch(r'[a-zA-Z0-9._:/-]+@sha256:[a-f0-9]{64}', image):
        raise ValueError('DETECT_IMAGE must be an operator-configured sha256 digest')
    cpus = int(os.environ.get('DETECT_CPUS', '4'))
    timeout = int(os.environ.get('DETECT_TIMEOUT_SECONDS', '21600'))
    confidence = float(os.environ.get('DETECT_CONFIDENCE', '0.3'))
    memory = os.environ.get('DETECT_MEMORY', '8g')
    if not 1 <= cpus <= 256 or not 1 <= timeout <= 604800 or not 0 <= confidence <= 1 or not re.fullmatch(r'[1-9][0-9]*[mg]', memory):
        raise ValueError('Invalid operator resource configuration')
    return image, cpus, timeout, confidence, memory


def snapshot(jid, job):
    destination = job / 'uploads'
    if destination.exists():
        return json.loads((job / 'manifest.json').read_text())
    with store.open_input(store.ROOT / 'inbox', f'{jid}/manifest.json') as stream:
        raw = stream.read(2 * 1024**2 + 1)
    if len(raw) > 2 * 1024**2:
        raise ValueError('Manifest too large')
    names = json.loads(raw).get('files')
    if not isinstance(names, list) or not 1 <= len(names) <= MAX_FILES:
        raise ValueError('Invalid input manifest')
    temp = job / 'snapshot.tmp'
    shutil.rmtree(temp, ignore_errors=True)
    temp.mkdir(mode=0o750)
    total, seen, hashes = 0, set(), {}
    try:
        for name in names:
            rel = store.relative(name)
            if rel.suffix.lower() not in store.ALLOWED or str(rel).casefold() in seen:
                raise ValueError('Invalid or duplicate image name')
            seen.add(str(rel).casefold())
            dest = temp / rel
            dest.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
            digest = hashlib.sha256()
            with store.open_input(store.ROOT / 'inbox', f'{jid}/uploads/{name}') as src, dest.open('xb') as dst:
                while chunk := src.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_BYTES or shutil.disk_usage(store.ROOT).free < RESERVE:
                        raise ValueError('Input storage limit exceeded')
                    digest.update(chunk)
                    dst.write(chunk)
            dest.chmod(0o640)
            hashes[name] = digest.hexdigest()
        manifest = {'files': names, 'sha256': hashes, 'bytes': total}
        store.atomic_json(job / 'manifest.json', manifest)
        temp.rename(destination)
        return manifest
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def docker_args(jid, attempt, work, config):
    image, cpus, timeout, confidence, memory = config
    args = ['docker', 'run', '--rm', '--name', f'mewc-{jid}', '--user', f'{os.getuid()}:{os.getgid()}',
            '--network', 'none', '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '--cpus', str(cpus), '--memory', memory, '--memory-swap', memory, '--pids-limit', '512',
            '--log-driver', 'none', '--tmpfs', '/tmp:rw,nosuid,size=512m',
            '--mount', f'type=bind,src={work},dst=/images',
            '-e', 'INPUT_DIR=/images', '-e', 'MD_FILE=md.json', '-e', f'THRESHOLD={confidence}',
            '-e', 'RECURSIVE=True', '-e', 'RELATIVE_FILENAMES=True', '-e', f'NCORES={cpus}',
            '-e', 'CHECKPOINT_FREQ=25', '-e', f'OMP_NUM_THREADS={cpus}', '-e', f'MKL_NUM_THREADS={cpus}',
            '-e', f'OPENBLAS_NUM_THREADS={cpus}']
    model = os.environ.get('DETECT_MODEL', '')
    if model:
        model_path = store.safe_file(store.MOUNT / 'models', model)
        args += ['--mount', f'type=bind,src={model_path},dst=/code/mewc-model.pt,readonly', '-e', 'MD_MODEL=mewc-model.pt']
    if os.environ.get('DETECT_GPUS') == 'all':
        args += ['--gpus', 'all']
    return args + [image]


class CleanupPending(RuntimeError):
    """Container absence is unverified; retain its workspace and block new work."""


def cleanup(jid):
    store.job_id(jid)
    name = f'mewc-{jid}'
    try:
        subprocess.run(['docker', 'rm', '-f', name], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=30, check=False)
        # An rm failure can mean already absent OR an unavailable daemon. A
        # successful daemon query with no exact name is the required evidence.
        check = subprocess.run(['docker', 'container', 'ls', '--all', '--filter',
                                f'name=^/{name}$', '--format', '{{.ID}}'],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True, timeout=30, check=False)
        if check.returncode or check.stdout.strip():
            raise CleanupPending('Container removal is unverified; worker is blocked pending cleanup')
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CleanupPending('Docker unavailable during cleanup; worker is blocked pending cleanup') from exc


def execute(args, jid, log, timeout):
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline, written = time.monotonic() + timeout, 0
    try:
        with log.open('wb') as output:
            while process.poll() is None:
                if STOP or (store.ROOT / 'cancel' / jid).exists():
                    raise InterruptedError('Job cancelled or worker stopped')
                if time.monotonic() >= deadline:
                    raise TimeoutError('Detector timeout exceeded')
                if shutil.disk_usage(store.ROOT).free < RESERVE:
                    raise ValueError('Research-volume reserve reached')
                for key, _ in selector.select(0.25):
                    chunk = os.read(key.fd, 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                    if written < 1024**2:
                        output.write(chunk[:1024**2 - written])
                        written += len(chunk[:1024**2 - written])
            # Keep the final diagnostic tail even for a very short-lived process.
            while chunk := process.stdout.read(65536):
                keep = chunk[:max(0, 1024**2 - written)]
                output.write(keep)
                written += len(keep)
            if process.returncode:
                raise ValueError(f'Detector exited with status {process.returncode}')
    finally:
        selector.close()
        try:
            cleanup(jid)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            process.stdout.close()


def run(jid):
    store.job_id(jid)
    store.mounted()
    job = store.ROOT / 'jobs' / jid
    job.mkdir(exist_ok=True, mode=0o750)
    attempt = uuid.uuid4().hex
    out = job / 'attempts' / attempt
    out.mkdir(parents=True, mode=0o750)
    work = store.ROOT / 'work' / attempt
    state = {'status': 'running', 'attempt': attempt, 'processed': 0, 'total': 0, 'failed_images': 0,
             'started_at': time.time()}
    store.atomic_json(job / 'state.json', state)
    (store.ROOT / 'requests' / jid).unlink(missing_ok=True)
    try:
        if STOP or (store.ROOT / 'cancel' / jid).exists():
            raise InterruptedError('Job cancelled before execution')
        config = image_config()
        state['image'] = config[0]
        manifest = snapshot(jid, job)
        state['total'] = len(manifest['files'])
        if shutil.disk_usage(store.ROOT).free < manifest['bytes'] + RESERVE:
            raise ValueError('Insufficient space for isolated attempt')
        store.atomic_json(job / 'state.json', state)
        shutil.copytree(job / 'uploads', work)
        args = docker_args(jid, attempt, work, config)
        provenance = {'image': config[0], 'inputs': manifest, 'threshold': config[3], 'csv_schema': 1}
        model = os.environ.get('DETECT_MODEL', '')
        if model:
            model_path = store.safe_file(store.MOUNT / 'models', model)
            with model_path.open('rb') as stream:
                provenance['model_sha256'] = hashlib.file_digest(stream, 'sha256').hexdigest()
        store.atomic_json(out / 'provenance.json', provenance)
        execute(args, jid, out / 'detect.log', config[2])
        md = store.safe_file(work, 'md.json')
        if md.stat().st_size > 256 * 1024**2:
            raise ValueError('Detector output exceeds 256 MB limit')
        data = json.loads(md.read_text())
        counters = validate(data, manifest['files'])
        store.atomic_json(out / 'md.json', data)
        convert(data, out / 'detections.csv')
        state.update(counters)
        state['status'] = 'partial' if counters['failed_images'] else 'succeeded'
    except CleanupPending as exc:
        state.update(status='cleanup_pending', error=str(exc))
    except InterruptedError as exc:
        state.update(status='cancelled', error=str(exc))
    except Exception as exc:
        state.update(status='failed', error=f'{type(exc).__name__}: {exc}')
    finally:
        if state['status'] != 'cleanup_pending':
            shutil.rmtree(work, ignore_errors=True)
            (store.ROOT / 'cancel' / jid).unlink(missing_ok=True)
            state['finished_at'] = time.time()
        store.atomic_json(job / 'state.json', state)
    return state


def reconcile():
    """Recover before admitting any job; a daemon outage is never proof of cleanup."""
    ready = True
    for path in (store.ROOT / 'jobs').iterdir():
        try:
            jid = store.job_id(path.name)
            previous = store.state(jid)
            if previous['status'] not in ('running', 'cleanup_pending'):
                continue
            try:
                cleanup(jid)
            except CleanupPending as exc:
                previous.update(status='cleanup_pending', error=str(exc))
                store.atomic_json(path / 'state.json', previous)
                ready = False
                continue
            old_attempt = previous.get('attempt', '')
            if re.fullmatch(r'[0-9a-f]{32}', old_attempt):
                shutil.rmtree(store.ROOT / 'work' / old_attempt, ignore_errors=True)
            (store.ROOT / 'cancel' / jid).unlink(missing_ok=True)
            previous.update(status='failed', error='Worker interrupted; cleanup verified; retry creates a fresh attempt', finished_at=time.time())
            store.atomic_json(path / 'state.json', previous)
        except (ValueError, FileNotFoundError):
            continue
    return ready


def serve():
    store.mounted()
    with (store.ROOT / 'work' / 'worker.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while not STOP:
            store.mounted()
            if not reconcile():
                time.sleep(5)
                continue
            for request in sorted((store.ROOT / 'requests').iterdir()):
                try:
                    jid = store.job_id(request.name)
                except ValueError:
                    continue
                if STOP:
                    break
                if run(jid)['status'] == 'cleanup_pending':
                    break
            time.sleep(1)

if __name__ == '__main__':
    os.umask(0o027)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    serve()
