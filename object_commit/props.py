"""PropertyGroups stored on Object (history travels with the object)."""
import bpy
from bpy.props import (BoolProperty, CollectionProperty, IntProperty,
                       PointerProperty, StringProperty)

SCHEMA_VERSION = 1


def _active_index_update(self, context):
    # Imported lazily to avoid an import cycle at register time.
    from . import preview
    preview.on_active_changed(context)


class OCV_Commit(bpy.types.PropertyGroup):
    cid: IntProperty(name="ID", default=0)
    parent: IntProperty(name="Parent", default=-1)
    message: StringProperty(name="Message", default="")
    tag: StringProperty(name="Tag", default="")
    timestamp: StringProperty(name="Time", default="")
    snapshot: PointerProperty(name="Snapshot", type=bpy.types.ID)
    meta: StringProperty(name="Meta JSON", default="{}")
    is_stash: BoolProperty(name="Stash", default=False)
    is_auto: BoolProperty(name="Auto stash", default=False)


class OCV_ObjectState(bpy.types.PropertyGroup):
    schema: IntProperty(default=SCHEMA_VERSION)
    uuid: StringProperty(default="")
    next_id: IntProperty(default=1)
    head: IntProperty(default=-1)
    commits: CollectionProperty(type=OCV_Commit)
    active_index: IntProperty(default=-1, update=_active_index_update)
    message: StringProperty(name="Message", default="",
                            description="Message for the next commit")


classes = (OCV_Commit, OCV_ObjectState)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Object.ocv = PointerProperty(type=OCV_ObjectState)


def unregister():
    del bpy.types.Object.ocv
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
