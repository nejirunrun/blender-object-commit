"""Object Commit: per-object git-like restore points stored in the .blend."""
from . import translations, props, ops, ui, handlers

_modules = (translations, props, ops, ui, handlers)


def register():
    for m in _modules:
        m.register()


def unregister():
    from . import preview
    import bpy
    try:
        preview.exit(bpy.context)
    except Exception:
        pass
    for m in reversed(_modules):
        m.unregister()
