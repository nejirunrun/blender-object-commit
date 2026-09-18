import bpy
from bpy.app.handlers import persistent

from . import core, preview


@persistent
def _save_pre(*_):
    preview.exit(bpy.context)
    # Re-assert fake users so Blender never purges a snapshot on save.
    for o in bpy.data.objects:
        for c in o.ocv.commits:
            if c.snapshot is not None and not c.snapshot.use_fake_user:
                c.snapshot.use_fake_user = True


@persistent
def _load_post(*_):
    core.DIRTY.clear()
    core.SUPPRESS_DIRTY.clear()
    preview.cleanup_leftovers()


@persistent
def _depsgraph_update_post(scene, depsgraph):
    for upd in depsgraph.updates:
        idb = upd.id
        if not isinstance(idb, bpy.types.Object):
            continue
        name = idb.name
        if name.startswith(core.PREVIEW_PREFIX):
            continue
        if upd.is_updated_geometry or upd.is_updated_transform:
            if name in core.SUPPRESS_DIRTY:
                core.SUPPRESS_DIRTY.discard(name)
                continue
            if idb.original.ocv.commits:
                core.DIRTY.add(name)
    preview.on_depsgraph(bpy.context)


_handlers = (
    (bpy.app.handlers.save_pre, _save_pre),
    (bpy.app.handlers.load_post, _load_post),
    (bpy.app.handlers.depsgraph_update_post, _depsgraph_update_post),
)


def register():
    for lst, fn in _handlers:
        if fn not in lst:
            lst.append(fn)


def unregister():
    for lst, fn in _handlers:
        if fn in lst:
            lst.remove(fn)
