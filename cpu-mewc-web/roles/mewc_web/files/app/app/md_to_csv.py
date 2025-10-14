import json, sys, csv, pathlib

def main(src, dst):
    p = pathlib.Path(src)
    if not p.exists():
        raise SystemExit("md.json not found")
    data = json.loads(p.read_text())
    images = data.get("images", [])
    cats = data.get("detection_categories", {})
    with open(dst, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["image","category","label","confidence","bbox_x","bbox_y","bbox_w","bbox_h"])
        for im in images:
            rel = im.get("file") or im.get("file_name") or ""
            for det in im.get("detections", []):
                cat = str(det.get("category", ""))
                lbl = cats.get(cat, "")
                conf = det.get("conf") or det.get("confidence")
                bbox = det.get("bbox") or det.get("bbox_xywh") or [None]*4
                w.writerow([rel, cat, lbl, conf, *bbox])

if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]
    pathlib.Path(dst).parent.mkdir(parents=True, exist_ok=True)
    main(src, dst)
