"""Browse mode: show the active commit's snapshot in the viewport.

The working object is hidden and a non-selectable temporary object carrying
the snapshot datablock is shown in its place. The working object's data is
never touched.
"""
import bpy

from . import core
from . import snapshot as snap

_state = {"src": None, "pv": None, "cid": None}
_busy = False
_suppress = 0
_watching = False


class suppressed:
    """While active, changes to active_index do not enter browse mode
    (used by commit/restore/delete which move the selection themselves)."""

    def __enter__(self):
        global _suppress
        _suppress += 1

    def __exit__(self, *exc):
        global _suppress
        _suppress -= 1
        return False


def _redraw():
    try:
        for win in bpy.context.window_manager.windows:
            for area in win.screen.areas:
                if area.type == "VIEW_3D":
                    area.tag_redraw()
    except Exception:
        pass


def start_watcher():
    """Run the modal watcher that leaves browse mode on a viewport click."""
    global _watching
    if _watching:
        return
    try:
        if bpy.ops.ocv.browse_watch("INVOKE_DEFAULT") == {"RUNNING_MODAL"}:
            _watching = True
    except Exception:
        pass


def watcher_stopped():
    global _watching
    _watching = False


def is_active():
    return _state["src"] is not None


def source_name():
    return _state["src"]


def _pv_object():
    name = _state["pv"]
    return bpy.data.objects.get(name) if name else None


def _src_object():
    name = _state["src"]
    return bpy.data.objects.get(name) if name else None


def _apply_commit(pv, c):
    if c.snapshot is not None and pv.data is not c.snapshot:
        if isinstance(c.snapshot, type(pv.data)):
            pv.data = c.snapshot
        else:
            return False
    meta = snap.meta_loads(c.meta)
    snap.apply_meta(pv, meta, custom=False)
    _state["cid"] = c.cid
    return True


def enter(context, obj):
    global _busy
    if is_active():
        exit(context)
    c = core.active_commit(obj)
    if c is None or c.snapshot is None:
        raise core.OCVError("selected commit has no geometry to browse")
    if obj.mode != "OBJECT":
        context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode="OBJECT")
    _busy = True
    try:
        pv = bpy.data.objects.new(core.PREVIEW_PREFIX, c.snapshot)
        pv.hide_select = True
        pv.hide_render = True
        for coll in obj.users_collection:
            coll.objects.link(pv)
        if not pv.users_collection:
            context.scene.collection.objects.link(pv)
        _state["src"] = obj.name
        _state["pv"] = pv.name
        _apply_commit(pv, c)
        obj.hide_set(True)
    finally:
        _busy = False
    return pv


def update(context):
    global _busy
    src, pv = _src_object(), _pv_object()
    if src is None or pv is None:
        exit(context)
        return
    c = core.active_commit(src)
    if c is None or c.snapshot is None:
        return
    if c.cid == _state["cid"]:
        return
    _busy = True
    try:
        _apply_commit(pv, c)
    finally:
        _busy = False


def exit(context):
    global _busy
    if not is_active():
        return
    _busy = True
    try:
        pv = _pv_object()
        if pv is not None:
            bpy.data.objects.remove(pv)
        src = _src_object()
        if src is not None:
            try:
                src.hide_set(False)
            except Exception:
                pass
    finally:
        _busy = False
        _state.update(src=None, pv=None, cid=None)


def cleanup_leftovers():
    """After file load / crash: delete stale preview objects."""
    _state.update(src=None, pv=None, cid=None)
    for o in [o for o in bpy.data.objects
              if o.name.startswith(core.PREVIEW_PREFIX)]:
        bpy.data.objects.remove(o)


def on_active_changed(context):
    """List selection changed: switch the preview, entering browse mode if
    needed. Our own operators set the index under `suppressed()`."""
    if _busy or _suppress:
        return
    obj = getattr(context, "object", None)
    if obj is None:
        return
    if is_active():
        if obj.name == _state["src"]:
            update(context)
        return
    c = core.active_commit(obj)
    if c is None or c.snapshot is None or obj.mode != "OBJECT":
        return
    try:
        enter(context, obj)
    except core.OCVError:
        return
    start_watcher()
    _redraw()


def on_depsgraph(context):
    """Exit when the user selects a different object."""
    if not is_active() or _busy:
        return
    try:
        act = context.view_layer.objects.active
    except Exception:
        return
    if act is None or act.name != _state["src"]:
        exit(context)
