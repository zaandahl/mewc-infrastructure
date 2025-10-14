import os, uuid, zipfile, shutil, subprocess, json, io
from pathlib import Path
from typing import List, Optional
from datetime import datetime

from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# Config via environment (set by systemd unit)
MOUNT = Path(os.getenv("MEWC_MOUNT", "/mnt/mewc-volume")).resolve()
ALLOWED = set(os.getenv("MEWC_ALLOWED_EXTS", "jpg,jpeg,png").split(","))
MAX_MB = int(os.getenv("MEWC_MAX_UPLOAD_MB", "20480"))  # per request

app = FastAPI(title="MEWC Upload POC")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

def job_dirs(job_id: str):
    base = MOUNT / "jobs" / job_id
    uploads = base / "uploads"
    detect = base / "detect"
    logs = base / "logs"
    for p in (base, uploads, detect, logs):
        p.mkdir(parents=True, exist_ok=True)
    return base, uploads, detect, logs

def validate_name(name: str) -> Path:
    p = Path(name)
    if ".." in p.parts:
        raise ValueError("invalid path")
    return p

def ok_ext(name: str) -> bool:
    return Path(name).suffix.lower().lstrip(".") in ALLOWED

def state_path(job_id: str) -> Path:
    return MOUNT / "jobs" / job_id / "state.json"

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "max_mb": MAX_MB})

@app.post("/jobs")
async def create_job(
    request: Request,
    files: Optional[List[UploadFile]] = File(default=None),
    zipfile_upload: Optional[UploadFile] = File(default=None),
):
    job_id = uuid.uuid4().hex[:12]
    base, uploads, _, _ = job_dirs(job_id)

    has_files = bool(files) and any(getattr(f, "filename", "") for f in files)
    has_zip = bool(zipfile_upload) and bool(getattr(zipfile_upload, "filename", ""))

    if not has_files and not has_zip:
        shutil.rmtree(base, ignore_errors=True)
        return RedirectResponse("/", status_code=303)

    total = 0
    if has_zip:
        zpath = uploads / "upload.zip"
        with zpath.open("wb") as f:
            while True:
                chunk = await zipfile_upload.read(1024 * 1024)
                if not chunk: break
                f.write(chunk)
                total += len(chunk)
                if total > MAX_MB * 1024 * 1024:
                    f.close()
                    zpath.unlink(missing_ok=True)
                    shutil.rmtree(base, ignore_errors=True)
                    return HTMLResponse(f"ZIP too large (> {MAX_MB} MB).", status_code=400)
        if not zipfile.is_zipfile(zpath):
            zpath.unlink(missing_ok=True)
            shutil.rmtree(base, ignore_errors=True)
            return HTMLResponse("Provided file is not a valid ZIP.", status_code=400)
        with zipfile.ZipFile(zpath, "r") as z:
            for info in z.infolist():
                if info.is_dir(): continue
                if not ok_ext(info.filename): continue
                dest = uploads / validate_name(info.filename)
                dest.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, dest.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
        zpath.unlink(missing_ok=True)

    if has_files:
        for uf in files:
            if not getattr(uf, "filename", ""): continue
            rel = validate_name(uf.filename)
            if not ok_ext(rel.name): continue
            dest = uploads / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("wb") as f:
                while True:
                    chunk = await uf.read(1024 * 1024)
                    if not chunk: break
                    f.write(chunk)

    return RedirectResponse(f"/jobs/{job_id}", status_code=303)

@app.get("/jobs/{job_id}", response_class=HTMLResponse)
async def job_view(request: Request, job_id: str):
    base, uploads, detect, logs = job_dirs(job_id)
    count = len([p for p in uploads.rglob("*") if p.is_file() and ok_ext(p.name)])
    st = {}
    sp = state_path(job_id)
    if sp.exists():
        try: st = json.loads(sp.read_text())
        except: st = {}
    md = (detect/"md.json").exists()
    csvf = (detect/"detections.csv").exists()
    return templates.TemplateResponse(
        "job.html",
        {"request": request, "job_id": job_id, "count": count, "state": st, "has_md": md, "has_csv": csvf},
    )

@app.post("/jobs/{job_id}/start")
async def start_job(job_id: str):
    # only stage supported now is 'detect'
    base, uploads, _, _ = job_dirs(job_id)
    if not uploads.exists() or not any(uploads.rglob("*")):
        return JSONResponse({"ok": False, "error": "No uploads found"}, status_code=400)

    # start systemd unit
    r = subprocess.run(
        ["sudo","/bin/systemctl","start",f"mewc-job@{job_id}.service"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    if r.returncode != 0:
        return JSONResponse({"ok": False, "error": r.stderr.strip()}, status_code=500)
    return JSONResponse({"ok": True})

@app.get("/jobs/{job_id}/status")
async def status(job_id: str):
    sp = state_path(job_id)
    st = {}
    if sp.exists():
        try: st = json.loads(sp.read_text())
        except: st = {"status":"unknown"}
    # include small log tail
    log = ""
    lp = MOUNT/"jobs"/job_id/"logs"/"detect.log"
    if lp.exists():
        try:
            with lp.open("rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - 16_384), os.SEEK_SET)
                log = f.read().decode(errors="ignore")
        except: pass
    st["log_tail"] = log[-4000:]
    return JSONResponse(st)

@app.get("/jobs/{job_id}/download/{name}")
async def download(job_id: str, name: str):
    # allow only expected outputs
    allowed = {"md.json":"detect/md.json", "detections.csv":"detect/detections.csv"}
    rel = allowed.get(name)
    if not rel:
        return HTMLResponse("Not found", status_code=404)
    p = MOUNT/"jobs"/job_id/rel
    if not p.exists():
        return HTMLResponse("Not ready", status_code=404)
    return FileResponse(str(p), filename=name)
