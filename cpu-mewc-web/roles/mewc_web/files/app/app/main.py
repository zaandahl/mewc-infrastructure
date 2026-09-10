import fcntl
import json
import os
import shutil
import stat
import uuid
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from . import storage as store

MAX_BYTES = int(os.getenv('MEWC_MAX_UPLOAD_MB', '512')) * 1024**2
EXPANDED = int(os.getenv('MEWC_MAX_EXPANDED_MB', '2048')) * 1024**2
MAX_FILES = int(os.getenv('MEWC_MAX_FILES', '10000'))
DIRECT_FILES = 100
RESERVE = int(os.getenv('MEWC_RESERVE_MB', '2048')) * 1024**2
Image.MAX_IMAGE_PIXELS = 40_000_000
app = FastAPI(title='MEWC private detector')
app.mount('/static', StaticFiles(directory=Path(__file__).parent / 'static'), name='static')
templates = Jinja2Templates(directory=str(Path(__file__).parent / 'templates'))


class BodyLimit(Exception):
    pass


class UploadBoundary:
    """Bound bytes before multipart parsing, including requests without Content-Length."""
    def __init__(self, application):
        self.app = application

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = dict(scope.get('headers', []))
        host = headers.get(b'host', b'').decode('latin-1')
        if urlsplit('http://' + host).hostname not in ('localhost', '127.0.0.1', '::1'):
            return await JSONResponse({'detail': 'Use the localhost SSH tunnel'}, 400)(scope, receive, send)
        if scope['method'] != 'POST':
            return await self.app(scope, receive, send)
        lock = None
        try:
            store.mounted()
            # Prevent drive-by submissions to a localhost SSH tunnel from web pages.
            origin = headers.get(b'origin')
            if headers.get(b'sec-fetch-site') == b'cross-site' or (origin and urlsplit(origin.decode('latin-1')).netloc != host):
                return await JSONResponse({'detail': 'Cross-site request rejected'}, 403)(scope, receive, send)
            if scope['path'] == '/jobs':
                lock = (store.ROOT / 'tmp' / 'upload.lock').open('a')
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return await JSONResponse({'detail': 'Another upload is active'}, 409)(scope, receive, send)
                if shutil.disk_usage(store.ROOT).free < RESERVE + 2 * MAX_BYTES + EXPANDED:
                    return await JSONResponse({'detail': 'Insufficient research-volume space'}, 507)(scope, receive, send)
            total = 0
            async def bounded_receive():
                nonlocal total
                message = await receive()
                total += len(message.get('body', b''))
                if total > MAX_BYTES:
                    raise BodyLimit()
                return message
            await self.app(scope, bounded_receive, send)
        except BodyLimit:
            await JSONResponse({'detail': 'Request exceeds upload limit'}, 413)(scope, receive, send)
        except ValueError as exc:
            await JSONResponse({'detail': str(exc)}, 503)(scope, receive, send)
        finally:
            if lock:
                lock.close()


app.add_middleware(UploadBoundary)


@app.exception_handler(ValueError)
async def invalid(request, exc):
    return JSONResponse({'detail': str(exc)}, status_code=400)


def get_state(jid):
    store.mounted()
    try:
        state = store.state(jid)
        if (store.ROOT / 'requests' / jid).exists() and state['status'] not in ('running', 'cleanup_pending'):
            state['status'] = 'queued'
        return state
    except ValueError:
        raise HTTPException(404, 'Job not found')


def image_check(path):
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(path) as image:
            if image.format not in ('JPEG', 'PNG') or image.width * image.height > Image.MAX_IMAGE_PIXELS:
                raise ValueError('Unsupported or excessively large image')
            image.verify()


def stage(form, base):
    uploads = base / 'uploads'
    uploads.mkdir()
    total, names = 0, set()
    def save(name, source):
        nonlocal total
        rel = store.relative(name)
        if rel.suffix.lower() not in store.ALLOWED:
            raise ValueError('Only JPEG and PNG images are accepted')
        key = str(rel).casefold()
        if key in names or len(names) >= MAX_FILES:
            raise ValueError('Duplicate filename or image-count limit exceeded')
        names.add(key)
        dest = uploads / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open('xb') as output:
            while chunk := source.read(1024 * 1024):
                total += len(chunk)
                if total > EXPANDED or shutil.disk_usage(store.ROOT).free < RESERVE:
                    raise ValueError('Expanded image bytes exceed storage limit')
                output.write(chunk)
        image_check(dest)
    for field, upload in form.multi_items():
        if not isinstance(upload, UploadFile) or not upload.filename:
            continue
        if field == 'files':
            save(upload.filename, upload.file)
        elif field == 'zipfile_upload':
            with zipfile.ZipFile(upload.file) as archive:
                if len(archive.infolist()) > MAX_FILES:
                    raise ValueError('ZIP member-count limit exceeded')
                for member in archive.infolist():
                    store.relative(member.filename.rstrip('/') if member.is_dir() else member.filename)
                    mode = member.external_attr >> 16
                    if stat.S_ISLNK(mode) or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                        raise ValueError('ZIP special files are forbidden')
                    if member.is_dir():
                        continue
                    with archive.open(member) as stream:
                        save(member.filename, stream)
        else:
            raise ValueError('Unknown upload field')
    if not names:
        raise ValueError('Select images or a ZIP archive')
    manifest = sorted(str(p.relative_to(uploads)) for p in uploads.rglob('*') if p.is_file())
    store.atomic_json(base / 'manifest.json', {'files': manifest})


@app.get('/', response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request=request, name='index.html', context={'max_mb': MAX_BYTES // 1024**2, 'expanded_mb': EXPANDED // 1024**2, 'max_files': MAX_FILES})


@app.post('/jobs')
async def create_job(request: Request):
    jid = uuid.uuid4().hex
    base = store.ROOT / 'inbox' / jid
    base.mkdir(mode=0o750)
    try:
        async with request.form(max_files=DIRECT_FILES + 1, max_fields=0, max_part_size=1024) as form:
            await run_in_threadpool(stage, form, base)
    except BaseException as exc:
        shutil.rmtree(base)
        if isinstance(exc, (ValueError, zipfile.BadZipFile, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning)):
            raise HTTPException(400, 'Upload rejected: invalid images, unsafe archive, duplicate names, or storage limit') from exc
        raise
    return RedirectResponse(f'/jobs/{jid}', 303)


@app.get('/jobs/{jid}', response_class=HTMLResponse)
def job_view(request: Request, jid: str):
    state = get_state(jid)
    return templates.TemplateResponse(request=request, name='job.html', context={'job_id': jid, 'state': state, 'count': state.get('total', 0), 'has_md': state['status'] in ('succeeded', 'partial'), 'has_csv': state['status'] in ('succeeded', 'partial')})


@app.post('/jobs/{jid}/start')
def start_job(jid: str):
    state = get_state(jid)
    if state['status'] not in ('uploaded', 'failed', 'cancelled', 'partial', 'succeeded'):
        raise HTTPException(409, 'Job already active')
    try:
        (store.ROOT / 'requests' / jid).touch(exist_ok=False)
    except FileExistsError:
        raise HTTPException(409, 'Job already queued')
    return {'ok': True}


@app.post('/jobs/{jid}/cancel')
def cancel_job(jid: str):
    state = get_state(jid)
    if state['status'] != 'running' and not (store.ROOT / 'requests' / jid).exists():
        raise HTTPException(409, 'No active job to cancel')
    (store.ROOT / 'cancel' / jid).touch(exist_ok=True)
    return {'ok': True}


@app.get('/jobs/{jid}/status')
def status(jid: str):
    state = get_state(jid)
    state['log_tail'] = state.get('error', '')
    return state


def result(jid):
    state = get_state(jid)
    if state['status'] not in ('succeeded', 'partial'):
        raise HTTPException(409, 'Current attempt has no published result')
    return store.ROOT / 'jobs' / jid / 'attempts' / state['attempt']


@app.get('/jobs/{jid}/download/{name}')
def download(jid: str, name: str):
    if name not in ('md.json', 'detections.csv'):
        raise HTTPException(404)
    return FileResponse(result(jid) / name, filename=name)


@app.get('/jobs/{jid}/files/detections.csv')
def download_csv(jid: str):
    return download(jid, 'detections.csv')


@app.get('/jobs/{jid}/summary')
def summary(jid: str):
    data = json.loads((result(jid) / 'md.json').read_text())
    counts = {}
    for image in data['images']:
        for det in image.get('detections') or []:
            label = data['detection_categories'][str(det['category'])]
            counts[label] = counts.get(label, 0) + 1
    return {'status': 'ok', 'total': sum(counts.values()), 'counts': sorted(counts.items(), key=lambda x: -x[1])}


@app.get('/jobs/{jid}/files/raw/{name:path}')
def raw(jid: str, name: str):
    get_state(jid)
    base = store.ROOT / 'jobs' / jid / 'uploads'
    # Only worker-owned immutable snapshots are served.
    try:
        return FileResponse(store.safe_file(base, name))
    except ValueError:
        raise HTTPException(404)


@app.get('/jobs/{jid}/detections')
def detections(jid: str, offset: int = Query(0, ge=0), limit: int = Query(12, ge=1, le=100), min_conf: float = Query(0, ge=0, le=1)):
    data = json.loads((result(jid) / 'md.json').read_text())
    items = []
    for image in data['images']:
        selected = [dict(det, name=data['detection_categories'][str(det['category'])]) for det in image.get('detections') or [] if det['conf'] >= min_conf]
        if selected:
            items.append({'file': image['file'], 'detections': selected})
    return {'items': items[offset:offset + limit], 'total': len(items)}
