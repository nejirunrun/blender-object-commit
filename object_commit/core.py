"""Commit / checkout / stash / delete on Object.ocv."""
import datetime
import uuid as _uuid

import bpy
from bpy.app.translations import pgettext_iface as iface_

from . import snapshot as snap

# Objects whose geometry/transform changed since last commit (by name).
DIRTY = set()
# Objects the depsgraph handler should ignore once (we changed them ourselves).
SUPPRESS_DIRTY = set()

PREVIEW_PREFIX = ".ocv_preview"


class OCVError(Exception):
    pass


# ---------------------------------------------------------------- helpers
def ensure_uuid(obj):
    st = obj.ocv
    if not st.uuid:
        st.uuid = _uuid.uuid4().hex
    return st.uuid


def find_commit(obj, cid):
    for i, c in enumerate(obj.ocv.commits):
        if c.cid == cid:
            return i, c
    return -1, None


def head_commit(obj):
    return find_commit(obj, obj.ocv.head)[1]


def active_commit(obj):
    st = obj.ocv
    if 0 <= st.active_index < len(st.commits):
        return st.commits[st.active_index]
    return None


def is_editable(obj):
    return obj.library is None and obj.override_library is None


class _ObjectMode:
    """Temporarily put `obj` into Object Mode (needed to read/replace data)."""

    def __init__(self, context, obj):
        self.context = context
        self.obj = obj
        self.mode = None

    def __enter__(self):
        if self.obj.mode != "OBJECT":
            self.mode = self.obj.mode
            self.context.view_layer.objects.active = self.obj
            bpy.ops.object.mode_set(mode="OBJECT")
        return self

    def __exit__(self, *exc):
        if self.mode is not None:
            try:
                self.context.view_layer.objects.active = self.obj
                bpy.ops.object.mode_set(mode=self.mode)
            except Exception:
                pass
        return False


def _remove_id(idblock):
    try:
        bpy.data.batch_remove((idblock,))
    except Exception:
        pass


def _snapshot_refs(obj, idblock):
    return sum(1 for c in obj.ocv.commits if c.snapshot == idblock)


# ---------------------------------------------------------------- commit
def commit(context, obj, message, *, is_stash=False, is_auto=False,
           move_head=True):
    if obj.type in {"EMPTY"} and obj.data is None:
        pass  # meta-only commit is fine
    st = obj.ocv
    ensure_uuid(obj)
    cid = st.next_id
    st.next_id += 1

    with _ObjectMode(context, obj):
        meta = snap.capture_meta(obj)
        snapshot = None
        if obj.data is not None:
            snapshot = obj.data.copy()
            snapshot.name = f".ocv_{st.uuid[:8]}_{cid}"
            snapshot.use_fake_user = True

    c = st.commits.add()
    c.cid = cid
    c.parent = st.head
    c.message = message or (iface_("stash") if is_stash else iface_("commit %d") % cid)
    c.timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.snapshot = snapshot
    c.meta = snap.meta_dumps(meta)
    c.is_stash = is_stash
    c.is_auto = is_auto
    if move_head:
        st.head = cid
        DIRTY.discard(obj.name)
    from . import preview
    with preview.suppressed():
        st.active_index = len(st.commits) - 1
    return c


def _same_state(meta_a, meta_b):
    return snap.summarize_diff(meta_a, meta_b) == ["no tracked changes"]


def _working_differs(context, obj):
    """True unless the working state equals HEAD or the latest set-aside
    (then an auto set-aside would only duplicate an existing snapshot)."""
    with _ObjectMode(context, obj):
        work = snap.capture_meta(obj)
    for c in (head_commit(obj), latest_stash(obj)):
        if c is not None and _same_state(snap.meta_loads(c.meta), work):
            return False
    return True


# ---------------------------------------------------------------- verify
def verify(obj, c):
    meta = snap.meta_loads(c.meta)
    if not meta:
        return False, "meta unreadable"
    if c.snapshot is None:
        if meta.get("data_name") is None:
            return True, "meta-only"
        return False, "snapshot datablock missing"
    counts = snap.geometry_counts(c.snapshot)
    for k in ("verts", "edges", "faces"):
        if k in meta.get("counts", {}) and meta["counts"][k] != counts.get(k):
            return False, f"{k} mismatch"
    if meta.get("hash") and snap.geometry_hash(c.snapshot) != meta["hash"]:
        return False, "hash mismatch"
    return True, "ok"


# ---------------------------------------------------------------- checkout
def checkout(context, obj, cid, *, auto_stash=True):
    if not is_editable(obj):
        raise OCVError("linked / overridden object cannot be checked out")
    idx, c = find_commit(obj, cid)
    if c is None:
        raise OCVError(f"commit {cid} not found")
    ok, msg = verify(obj, c)
    if not ok:
        raise OCVError(f"commit {cid} failed verification: {msg}")
    meta = snap.meta_loads(c.meta)
    if c.snapshot is not None and obj.data is not None and \
            not isinstance(c.snapshot, type(obj.data)):
        raise OCVError("snapshot data type differs from object data")

    st = obj.ocv
    if auto_stash and _working_differs(context, obj):
        for old in [x for x in st.commits if x.is_auto]:
            delete_commit(obj, old.cid)
        commit(context, obj, iface_("auto stash before checkout #%d") % cid,
               is_stash=True, is_auto=True, move_head=False)

    with _ObjectMode(context, obj):
        if c.snapshot is not None:
            old = obj.data
            new = c.snapshot.copy()
            new.use_fake_user = False
            # Assigning a fresh copy makes this object single-user by
            # construction; other objects keep `old`.
            obj.data = new
            oldname = old.name
            if old.users == 0 and not old.use_fake_user:
                _remove_id(old)
                new.name = oldname
        snap.apply_meta(obj, meta)

    SUPPRESS_DIRTY.add(obj.name)
    DIRTY.discard(obj.name)
    st.head = cid
    idx, _ = find_commit(obj, cid)
    from . import preview
    with preview.suppressed():
        st.active_index = idx
    return c


# ---------------------------------------------------------------- delete
def delete_commit(obj, cid):
    st = obj.ocv
    idx, c = find_commit(obj, cid)
    if c is None:
        return False
    parent = c.parent
    for other in st.commits:
        if other.parent == cid:
            other.parent = parent
    if st.head == cid:
        st.head = parent
    sb = c.snapshot
    st.commits.remove(idx)
    if sb is not None and _snapshot_refs(obj, sb) == 0:
        # Only our fake user (and possibly a preview object) may hold it.
        for o in bpy.data.objects:
            if o.data == sb and o.name.startswith(PREVIEW_PREFIX):
                from . import preview
                preview.exit(bpy.context)
        sb.use_fake_user = False
        if sb.users == 0:
            _remove_id(sb)
    if st.active_index >= len(st.commits):
        from . import preview
        with preview.suppressed():
            st.active_index = len(st.commits) - 1
    return True


# ---------------------------------------------------------------- stash
def stash(context, obj):
    return commit(context, obj, iface_("stash"), is_stash=True, move_head=False)


def latest_stash(obj):
    for c in reversed(obj.ocv.commits):
        if c.is_stash:
            return c
    return None


def stash_pop(context, obj):
    c = latest_stash(obj)
    if c is None:
        raise OCVError("no stash")
    cid = c.cid
    prev_head = obj.ocv.head
    checkout(context, obj, cid, auto_stash=False)
    obj.ocv.head = prev_head
    delete_commit(obj, cid)
    idx, _ = find_commit(obj, prev_head)
    from . import preview
    with preview.suppressed():
        obj.ocv.active_index = idx


# ---------------------------------------------------------------- stats
def history_stats(obj):
    n = 0
    verts = 0
    seen = set()
    for c in obj.ocv.commits:
        sb = c.snapshot
        if sb is None or sb.name in seen:
            continue
        seen.add(sb.name)
        n += 1
        if isinstance(sb, bpy.types.Mesh):
            verts += len(sb.vertices)
    return n, verts


def working_vs_head_diff(obj):
    h = head_commit(obj)
    if h is None:
        return ["(no HEAD)"]
    return snap.summarize_diff(snap.meta_loads(h.meta), snap.capture_meta(obj))
