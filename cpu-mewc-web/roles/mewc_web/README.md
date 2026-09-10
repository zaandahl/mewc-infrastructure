# Private single-operator detector service

This optional role installs a detector-only UI for access through an SSH tunnel.
It does not implement authenticated multi-user hosting or the full MEWC pipeline.
The API rejects non-localhost Host headers and cross-origin POST requests; nginx
and Uvicorn bind to loopback. Access it with:

```sh
ssh -L 8080:127.0.0.1:8080 ubuntu@YOUR_TEST_HOST
# Open http://localhost:8080
```

Run shared volume/Docker provisioning before this role. The existing
`/usr/local/sbin/mewc-storage-check` must verify the configured volume identity.
The API, worker and nginx require that mount and stop if its mount unit stops.
The application also checks the mount before accepting jobs or serving data.
The root-owned storage guard alone uses systemd's `+` command prefix and enters
PID 1's mount namespace so it can inspect the host volume without service bind
mounts obscuring its identity;
the API and worker executable commands do not receive that prefix.

An explicit `detect_image` with an `@sha256:` digest is mandatory. The reviewed
6.0.0 detector image is:

```yaml
detect_image: zaandahl/mewc-detect@sha256:d7684e9878f9130c50a3a85e9d10a9ca58ba8f0b10c866aaec45473161473bbf
detect_gpus: "" # set "all" only on a provisioned GPU host
detect_model: "" # retain image's built-in model, or a relative file under models/
detect_confidence: 0.30
```

Image/model compatibility with non-root execution, a read-only container root,
network disabled, and optional GPU passthrough must pass a disposable-host canary.
A model supplied through `detect_model` is mounted read-only at `/code/mewc-model.pt`;
its SHA-256 is recorded. With no override the immutable image records the built-in
model provenance. Existing detector environment names are preserved.

The root-owned source and venv live at `/opt/mewc-web`. `mewc-web` is a non-login
account with no sudo rules or Docker group and no write access to application
code, worker snapshots, published outputs or operator configuration. A separate
non-login `mewc-worker` account has Docker access and is a trusted component.
The worker reads only strict 32-character hexadecimal job IDs from the spool;
image, model and resource settings come exclusively from its systemd unit.

Under `mount_point` (default `/mnt/mewc-volume`):

- `mewc-web/inbox`: web-owned original upload staging, retained after submission.
- `mewc-web/requests` and `cancel`: the narrow job-ID spool.
- `mewc-web/jobs/<id>/uploads`: worker-owned, immutable input snapshot.
- `mewc-web/jobs/<id>/attempts/<attempt>`: results, capped log and provenance.
- `mewc-web/work`: isolated writable attempt copies, deleted after the attempt.
- `mewc-web/tmp` and `mewc-nginx-tmp`: volume-backed multipart/proxy buffers.

Uploads allow at most 100 direct files or a ZIP with at most 10,000 members.
Default request size is 512 MiB; measured expansion is limited to 2 GiB, and a
2 GiB free-space reserve is kept. JPEG/PNG content and dimensions are checked
(up to 40 million pixels per image). Absolute, drive, traversal, symlink, special
file, ambiguous duplicate and unsupported member paths are rejected. Whole
failed uploads are removed. One upload is admitted at a time; before parsing it
must have room for temporary data, expansion and the reserve. No automatic data
retention deletion is performed; the operator must archive/remove completed
jobs when storage admission rejects further uploads.

One persistent worker owns a global advisory lock. Jobs execute serially with
4 CPUs, 8 GiB memory, 512 PIDs and a six-hour timeout by default. Container logs
are capped at 1 MiB and Docker logging is disabled for those containers. The
container root is read-only and `/tmp` is a bounded tmpfs. Input snapshots and
models are retained; detector writes occur in a fresh attempt copy at `/images`.

Success requires a zero container exit code and valid JSON accounting for every
input exactly once. A valid empty detections list means an image was processed
without detections. Explicit image failure records produce `partial`, count as
failed images, and do not inflate `processed`. Missing, malformed, stale,
duplicate or incomplete output produces `failed`. Canonical CSV columns remain
`image,category,label,confidence,bbox_x,bbox_y,bbox_w,bbox_h`; confidence zero is
preserved. Partial jobs retain JSON failures; failed-image information is not
invented as detection rows in CSV. Retries create a new attempt, and downloads
only expose the current successfully validated attempt.

Cancellation, timeout, process failure and service shutdown remove the named
container and verify its absence through a successful Docker daemon query. If
removal cannot be verified, the job remains `cleanup_pending`, its attempt copy
is retained, and no new job runs. The worker retries cleanup, including after
restart, before declaring failure and permitting an explicit fresh retry.
Queued retries hide earlier results immediately, including on page reload. Stop/restart the
worker using `systemctl` as an operator; the web service never executes systemctl.
Inspect `systemctl status mewc-web mewc-worker nginx` and the attempt's
`detect.log` for diagnostics. A queued job can be cancelled from its page.

The previous `/mnt/mewc-volume/jobs` proof-of-concept data is not imported or
modified. It does not have the integrity/provenance guarantees of new attempts.
Keep it for manual archival review rather than presenting it as newly validated.

Tests: install the locked `files/app/requirements.txt` plus pytest and httpx in
an isolated Python 3.12 environment, then run `pytest tests/test_web.py` from the
repository root. These tests use real ASGI multipart requests and fake detector
processes; a passing suite does not establish actual image/GPU compatibility.
