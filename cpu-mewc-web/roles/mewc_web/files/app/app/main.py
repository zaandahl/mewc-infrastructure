import os, uuid, zipfile, shutil, subprocess, json, io, csv
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
DATA_ROOT = Path("/mnt/mewc-volume")

app = FastAPI(title="MEWC Upload POC")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

def job_dir(job_id: str) -> Path:
    return DATA_ROOT / "jobs" / job_id

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

# --- helpers for md.json and safe file serving ---

def find_md(job_id: str) -> Optional[Path]:
    """Return canonical detect/md.json; if found elsewhere, copy it there."""
    d = job_dir(job_id)
    candidates = [
        d / "detect" / "md.json",
        d / "uploads" / "md.json",
        d / "uploads" / "images" / "md.json",
    ]
    for p in candidates:
        if p.exists():
            canon = d / "detect" / "md.json"
            canon.parent.mkdir(parents=True, exist_ok=True)
            if p != canon:
                try:
                    canon.write_bytes(p.read_bytes())
                except Exception:
                    pass
            return canon
    return None

def safe_upload_path(job_id: str, rel: str) -> Optional[Path]:
    """Resolve a path under the job's uploads dir without traversal."""
    base = job_dir(job_id) / "uploads"
    try:
        p = (base / rel).resolve()
        base_r = base.resolve()
    except Exception:
        return None
    if str(p).startswith(str(base_r)) and p.exists():
        return p
    return None

# --- routes ---

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
    md = bool(find_md(job_id))
    csvf = (detect/"detections.csv").exists()
    return templates.TemplateResponse(
        "job.html",
        {"request": request, "job_id": job_id, "count": count, "state": st, "has_md": md, "has_csv": csvf},
    )

@app.post("/jobs/{job_id}/start")
async def start_job(job_id: str):
    base, uploads, _, _ = job_dirs(job_id)
    if not uploads.exists() or not any(uploads.rglob("*")):
        return JSONResponse({"ok": False, "error": "No uploads found"}, status_code=400)

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
    allowed = {"md.json":"detect/md.json", "detections.csv":"detect/detections.csv"}
    rel = allowed.get(name)
    if not rel:
        return HTMLResponse("Not found", status_code=404)
    p = MOUNT/"jobs"/job_id/rel
    if not p.exists():
        return HTMLResponse("Not ready", status_code=404)
    return FileResponse(str(p), filename=name)

# --- CSV + summary (robust to md.json location) ---

@app.get("/jobs/{job_id}/files/detections.csv")
def download_csv(job_id: str):
    ddir = job_dir(job_id) / "detect"
    csv_path = ddir / "detections.csv"
    if csv_path.exists():
        return FileResponse(csv_path, filename="detections.csv", media_type="text/csv")
    md_path = find_md(job_id)
    if not md_path:
        return JSONResponse({"error":"No outputs yet"}, status_code=404)
    tmp_csv = ddir / "_tmp_detections.csv"
    data = json.loads(md_path.read_text())
    cats = data.get("detection_categories", {})
    with tmp_csv.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["file","category","category_name","conf","bbox_x","bbox_y","bbox_w","bbox_h"])
        for im in data.get("images", []):
            for d in im.get("detections", []):
                cat = str(d.get("category",""))
                name = cats.get(cat, cat)
                bbox = d.get("bbox",[None,None,None,None])
                w.writerow([im.get("file",""), cat, name, d.get("conf",""), *bbox])
    return FileResponse(tmp_csv, filename="detections.csv", media_type="text/csv")

@app.get("/jobs/{job_id}/summary")
def job_summary(job_id: str):
    ddir = job_dir(job_id) / "detect"
    csv_path = ddir / "detections.csv"
    counts, total = {}, 0
    if csv_path.exists():
        with csv_path.open() as f:
            for row in csv.DictReader(f):
                name = (row.get("category_name") or row.get("category") or "unknown").strip()
                counts[name] = counts.get(name, 0) + 1; total += 1
    else:
        md_path = find_md(job_id)
        if not md_path:
            return JSONResponse({"status":"pending","total":0,"counts":[]}, status_code=202)
        data = json.loads(md_path.read_text()); cats = data.get("detection_categories", {})
        for im in data.get("images", []):
            for d in im.get("detections", []):
                name = cats.get(str(d.get("category","")), "unknown")
                counts[name] = counts.get(name, 0) + 1; total += 1
    items = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    return {"status":"ok","total": total, "counts": items}

# --- raw file serving + detections paging for bbox gallery ---

@app.get("/jobs/{job_id}/files/raw/{relpath:path}")
def get_raw(job_id: str, relpath: str):
    p = safe_upload_path(job_id, relpath)
    if not p:
        return JSONResponse({"error":"not found"}, status_code=404)
    return FileResponse(p)

@app.get("/jobs/{job_id}/detections")
def list_detections(job_id: str, offset: int = 0, limit: int = 12, min_conf: float = 0.0):
    md = find_md(job_id)
    if not md:
        return JSONResponse({"items": [], "total": 0}, status_code=202)
    data = json.loads(md.read_text())
    cats = data.get("detection_categories", {})
    items = []
    for im in data.get("images", []):
        file_rel = im.get("file", "")
        if not file_rel:
            continue
        dets = []
        for d in im.get("detections", []):
            try:
                conf = float(d.get("conf", 0))
            except Exception:
                conf = 0.0
            if conf < min_conf:
                continue
            dets.append({
                "bbox": d.get("bbox", [0,0,0,0]),
                "conf": conf,
                "category": str(d.get("category","")),
                "name": cats.get(str(d.get("category","")), "")
            })
        if dets:
            items.append({"file": file_rel, "detections": dets})
    total = len(items)
    return {"items": items[offset:offset+limit], "total": total}
