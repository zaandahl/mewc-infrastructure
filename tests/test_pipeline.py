"""Orchestration failure tests; no Docker, ML runtime, cloud, or private data."""
import json
import shutil
from pathlib import Path
import pytest
from mewc_pipeline import runner as r


@pytest.fixture
def cfg(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'nested').mkdir()
    (source / 'nested/a.jpg').write_bytes(b'fixture-jpeg')
    models = tmp_path / 'assets'
    models.mkdir()
    for name in ('detector', 'classifier', 'class_map'):
        (models / name).write_bytes(name.encode())
    declaration = dict(schema_version=1, architecture='ENB0', class_order=['animal'], class_ids=[0],
                       model_sha256=r.sha(models / 'classifier'), class_map_sha256=r.sha(models / 'class_map'),
                       input_shape=[None, 224, 224, 3], preprocessing={'fixture': True})
    r.atomic(models / 'manifest', declaration)
    data = dict(schema_version=1, storage_mount=str(tmp_path), storage_uuid='12345678-1234-1234-1234-123456789abc', run_id='fixture-run', input_dir=str(source), run_dir=str(tmp_path / 'run'),
                images={stage: 'sha256:' + 'a'*64 for stage in r.STAGES},
                model={key: str(models / key) for key in ('detector', 'classifier', 'class_map', 'manifest')}, gpu=False)
    path = tmp_path / 'config.json'
    r.atomic(path, data)
    return r.config(path)


class FakePipeline(r.Pipeline):
    def storage(self):
        pass

    def __init__(self, cfg, empty=False):
        super().__init__(cfg)
        self.calls = []
        self.empty = empty
        self.fail = None

    def cleanup(self, name):
        pass

    def preflight(self):
        return r.model_contract(self.cfg), r.image_files(Path(self.cfg['input_dir']))

    def container(self, stage, attempt, mounts, env, extra=None):
        if extra:
            return
        self.calls.append(stage)
        if self.fail == stage:
            raise r.Invalid('injected container interruption')
        source = 'originals/nested/a.jpg'
        crop = 'originals/nested/a-0.jpg'
        if stage == 'detect':
            r.atomic(attempt / 'md_out.json', {'images': [{'file': 'nested/a.jpg', 'detections': [] if self.empty else
                       [dict(category='1', conf=.8, bbox=[.1, .1, .3, .4])]}],
                       'detection_categories': {'1': 'animal', '2': 'person', '3': 'vehicle'}})
        elif stage == 'snip':
            (attempt / 'snips').mkdir()
            if not self.empty:
                path = attempt / 'snips' / crop
                path.parent.mkdir(parents=True)
                path.write_bytes(b'crop')
            r.atomic(attempt / 'snips/crop_manifest.json', dict(schema_version=1, complete=True,
                     policy=r.POLICY, selection='animal', errors=[], omissions=[],
                     crops=[] if self.empty else [dict(crop_id=crop, crop_file=crop, source_file=source, detection_index=0)],
                     images=[dict(source_file=source, status='complete')]))
        elif stage == 'predict':
            (attempt / 'mewc_out.pkl').write_bytes(b'fixture-only-pickle')
            (attempt / 'mewc_out.csv').write_text('crop_id,rand_name,source_file,detection_index,class_index,class_id,class_name,prob,class_rank\n' +
                ('' if self.empty else f'{crop},{crop},{source},0,0,0,animal,1.0,1\n'))
            (attempt / 'prediction_scores.npz').write_bytes(b'fixture-scores')
            paths = self.paths(r.read(self.manifest_path))
            r.atomic(attempt / 'prediction_manifest.json', dict(schema_version=1, complete=True,
                     crop_manifest_sha256=r.sha(paths['snip'] / 'snips/crop_manifest.json'),
                     model_manifest_sha256=r.sha(self.model_path('manifest')),
                     outputs={name: r.sha(attempt/name) for name in ('mewc_out.csv', 'mewc_out.pkl', 'prediction_scores.npz')}))
        elif stage == 'exif':
            path = attempt / 'camelot' / source
            path.parent.mkdir(parents=True)
            shutil.copy2(self.root / 'inputs/nested/a.jpg', path)
            r.atomic(attempt / 'metadata/metadata.json', dict(schema_version=1, complete=True, errors=[],
                     images=[dict(source_file=source, status='no_animal' if self.empty else 'exported',
                                  source_sha256=r.sha(self.root / 'inputs/nested/a.jpg'))]))
        elif stage == 'box':
            path = attempt / 'boxed/animal' / source
            path.parent.mkdir(parents=True)
            path.write_bytes(b'boxed')
            r.atomic(attempt / 'boxed/box_report.json', dict(schema_version=1, complete=True, errors=[],
                     images=[dict(source_file=source, status='copied', output_file=path.relative_to(attempt).as_posix())]))
        (self.root / 'logs').mkdir(exist_ok=True)


def test_complete_run_reuses_only_verified_outputs(cfg):
    pipeline = FakePipeline(cfg)
    assert pipeline.run()['complete']
    assert pipeline.calls == list(r.STAGES)
    assert pipeline.run()['complete']
    assert pipeline.calls == list(r.STAGES)
    paths = pipeline.paths(r.read(pipeline.manifest_path))
    (paths['snip'] / 'snips/originals/nested/a-0.jpg').write_bytes(b'changed')
    assert pipeline.run()['complete']
    assert pipeline.calls == list(r.STAGES) + list(r.STAGES[1:])


def test_interruption_resumes_at_failed_stage_and_preserves_attempt(cfg):
    pipeline = FakePipeline(cfg)
    pipeline.fail = 'predict'
    with pytest.raises(r.Invalid, match='interruption'):
        pipeline.run()
    previous = r.read(pipeline.manifest_path)['stages']['predict']['directory']
    pipeline.fail = None
    assert pipeline.run()['complete']
    assert (pipeline.root / previous).is_dir()
    assert pipeline.calls == ['detect', 'snip', 'predict', 'predict', 'exif', 'box']


def test_all_blank_is_explicit_success_empty(cfg):
    result = FakePipeline(cfg, empty=True).run()
    assert result['state'] == 'success-empty' and result['crops'] == 0 and result['images'] == 1


def test_changed_source_refuses_same_run_identity(cfg):
    pipeline = FakePipeline(cfg)
    pipeline.run()
    (Path(cfg['input_dir']) / 'nested/a.jpg').write_bytes(b'changed')
    with pytest.raises(r.Invalid, match='Run identity changed'):
        pipeline.run()


def test_modified_model_snapshot_cannot_verify(cfg):
    pipeline = FakePipeline(cfg)
    pipeline.run()
    pipeline.model_path('classifier').write_bytes(b'changed')
    with pytest.raises(r.Invalid, match='model snapshot changed'):
        pipeline.verify()


def test_reject_unregistered_nonempty_run_directory(cfg):
    root = Path(cfg['run_dir']); root.mkdir()
    (root / 'valuable.txt').write_text('preserve')
    with pytest.raises(r.Invalid, match='must be empty'):
        FakePipeline(cfg)
    assert (root / 'valuable.txt').read_text() == 'preserve'


@pytest.mark.parametrize('damage', ['missing', 'failure', 'duplicate', 'nan'])
def test_detector_identity_and_failure_accounting(tmp_path, damage):
    row = dict(file='x.jpg', detections=[])
    images = [row]
    if damage == 'missing': images = []
    elif damage == 'failure': row['failure'] = 'unreadable'
    elif damage == 'duplicate': images.append(dict(row))
    else: row['detections'] = [dict(category='1', conf=float('nan'), bbox=[.1,.1,.5,.5])]
    path = tmp_path/'md.json'; path.write_text(json.dumps({'images': images}))
    with pytest.raises(r.Invalid):
        r.validate_detection(path, {'x.jpg': {}})


def test_crop_missing_identity_not_hidden_by_equal_counts(cfg):
    pipeline = FakePipeline(cfg); pipeline.run()
    paths = pipeline.paths(r.read(pipeline.manifest_path))
    doc_path = paths['snip'] / 'snips/crop_manifest.json'
    doc = r.read(doc_path); doc['crops'][0]['detection_index'] = 1; r.atomic(doc_path, doc)
    with pytest.raises(r.Invalid, match='crop identity'):
        r.validate_crops(paths['snip'], r.read(paths['detect']/'md_out.json'), {'originals/nested/a.jpg': {}})


def test_export_verified_and_transfer_damage_detected(cfg, tmp_path):
    pipeline = FakePipeline(cfg); pipeline.run()
    target = tmp_path / 'export'
    assert pipeline.export(target)['complete']
    assert r.main(['verify-export', '--destination', str(target)]) == 0
    (target/'mewc_out.csv').write_bytes(b'damaged')
    assert r.main(['verify-export', '--destination', str(target)]) == 1


@pytest.mark.parametrize('kind', ['foreign', 'symlink'])
def test_export_rejects_unowned_partial(cfg, tmp_path, kind):
    pipeline = FakePipeline(cfg); pipeline.run()
    target = tmp_path/'export'; partial = tmp_path/'export.partial'
    if kind == 'symlink': partial.symlink_to(Path(cfg['input_dir']), target_is_directory=True)
    else:
        partial.mkdir(); (partial/'foreign').write_text('preserve')
    with pytest.raises(r.Invalid): pipeline.export(target)


def test_export_corrupted_copy_is_not_self_certified(cfg, tmp_path, monkeypatch):
    pipeline = FakePipeline(cfg); pipeline.run()
    original = shutil.copy2
    def corrupt(source, target, *args, **kwargs):
        result = original(source, target, *args, **kwargs)
        if Path(target).name == 'mewc_out.csv': Path(target).write_text('corruption')
        return result
    monkeypatch.setattr(shutil, 'copy2', corrupt)
    with pytest.raises(r.Invalid, match='Export copy differs'):
        pipeline.export(tmp_path/'export')
    assert not (tmp_path/'export').exists()


def test_snapshot_rejects_symlink_partial(tmp_path):
    source=tmp_path/'src'; source.mkdir(); (source/'a.jpg').write_bytes(b'a')
    dest=tmp_path/'dst'; dest.mkdir()
    victim=tmp_path/'victim'; victim.write_bytes(b'preserve')
    (dest/'.a.jpg.partial').symlink_to(victim)
    with pytest.raises(r.Invalid, match='symlinks'):
        r.snapshot(source, dest, r.inventory(source))
    assert victim.read_bytes() == b'preserve'


def test_timestamp_only_input_mutation_is_not_invisible(cfg):
    import os
    pipeline = FakePipeline(cfg); pipeline.run()
    source = pipeline.root/'inputs/nested/a.jpg'
    os.utime(source, ns=(source.stat().st_atime_ns, source.stat().st_mtime_ns + 1000000000))
    with pytest.raises(r.Invalid, match='Input snapshot hash mismatch'):
        pipeline.verify()


def test_resume_reconciles_all_stages_before_rebuilding(cfg):
    pipeline = FakePipeline(cfg)
    pipeline.run()
    manifest = r.read(pipeline.manifest_path)
    manifest['stages']['predict']['state'] = 'running'
    r.atomic(pipeline.manifest_path, manifest)
    (pipeline.root / manifest['stages']['detect']['directory'] / 'md_out.json').write_text('damaged')
    events = []
    pipeline.cleanup = lambda name: events.append(('cleanup', name.rsplit('-', 1)[-1]))
    original = pipeline.container
    def container(stage, *args, **kwargs):
        events.append(('container', stage))
        return original(stage, *args, **kwargs)
    pipeline.container = container
    pipeline.run()
    assert events[:5] == [('cleanup', stage) for stage in r.STAGES]
    assert events[5] == ('container', 'detect')


def test_storage_guard_rejects_replacement_filesystem(cfg, monkeypatch):
    import subprocess
    pipeline = object.__new__(r.Pipeline)
    pipeline.cfg = cfg
    pipeline.root = Path(cfg['run_dir'])
    original_stat = Path.stat
    class Stat:
        st_dev = 999
        def __getattr__(self, name):
            return getattr(original_stat(Path("/")), name)
    monkeypatch.setattr(Path, 'is_mount', lambda path: True)
    monkeypatch.setattr(Path, 'stat', lambda path, *a, **k: Stat() if str(path) == '/' else original_stat(path, *a, **k))
    monkeypatch.setattr(r.subprocess, 'run', lambda *a, **k: subprocess.CompletedProcess(a, 0, '87654321-1234-1234-1234-123456789abc\n'))
    with pytest.raises(r.Invalid, match='UUID differs'):
        pipeline.storage()
    assert not pipeline.root.exists()


def test_verify_requires_frozen_runner_sources(cfg):
    pipeline = FakePipeline(cfg); pipeline.run()
    manifest = r.read(pipeline.manifest_path)
    manifest['identity']['runner_sources']['runner.py'] = '0' * 64
    r.atomic(pipeline.manifest_path, manifest)
    with pytest.raises(r.Invalid, match='Runner sources differ'):
        pipeline.verify()


def test_storage_loss_before_stage_does_not_create_attempts(cfg):
    pipeline = FakePipeline(cfg)
    original = pipeline.stage
    def lost_storage():
        raise r.Invalid('injected storage loss')
    def stage():
        result = original()
        pipeline.storage = lost_storage
        return result
    pipeline.stage = stage
    with pytest.raises(r.Invalid, match='storage loss'):
        pipeline.run()
    assert not (pipeline.root / 'work').exists()
