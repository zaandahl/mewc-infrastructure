"""Canonical detector contract: failed images are records, never empty detections."""
import csv
import json
import math
import sys
from pathlib import Path

try:
    from .storage import relative
except ImportError:
    from storage import relative


def validate(data, expected):
    if not isinstance(data, dict) or not isinstance(data.get('images'), list):
        raise ValueError('Missing images array')
    categories = data.get('detection_categories')
    if not isinstance(categories, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in categories.items()):
        raise ValueError('Invalid detection categories')
    seen, failed, empty = set(), 0, 0
    for image in data['images']:
        if not isinstance(image, dict):
            raise ValueError('Invalid image record')
        name = str(relative(image.get('file')))
        if name not in expected or name in seen:
            raise ValueError('Unexpected or duplicate output image')
        seen.add(name)
        detections = image.get('detections')
        if image.get('failure'):
            if detections not in (None, []):
                raise ValueError('Failed image contains detections')
            failed += 1
            continue
        if not isinstance(detections, list):
            raise ValueError('Null detections require explicit image failure')
        if not detections:
            empty += 1
        for det in detections:
            if not isinstance(det, dict) or str(det.get('category')) not in categories:
                raise ValueError('Unknown detection category')
            conf, bbox = det.get('conf'), det.get('bbox')
            if isinstance(conf, bool) or not isinstance(conf, (float, int)) or not math.isfinite(conf) or not 0 <= conf <= 1:
                raise ValueError('Invalid confidence')
            if not isinstance(bbox, list) or len(bbox) != 4 or not all(type(x) in (int, float) and math.isfinite(x) and 0 <= x <= 1 for x in bbox):
                raise ValueError('Invalid bounding box')
            if bbox[0] + bbox[2] > 1.000001 or bbox[1] + bbox[3] > 1.000001:
                raise ValueError('Bounding box outside image')
    if seen != set(expected):
        raise ValueError('Output does not account for every input image')
    return {'processed': len(seen) - failed, 'failed_images': failed, 'empty_images': empty, 'total': len(expected)}


def convert(data, dst):
    with Path(dst).open('w', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['image', 'category', 'label', 'confidence', 'bbox_x', 'bbox_y', 'bbox_w', 'bbox_h'])
        for image in data['images']:
            for det in image.get('detections') or []:
                category = str(det['category'])
                writer.writerow([image['file'], category, data['detection_categories'][category], det['conf'], *det['bbox']])


def main(src, dst):
    data = json.loads(Path(src).read_text())
    validate(data, [image['file'] for image in data['images']])
    convert(data, dst)

if __name__ == '__main__':
    main(*sys.argv[1:])
