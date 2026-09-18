import bpy
from bpy.app.translations import pgettext_iface as iface_
from bpy.props import IntProperty

from . import core, preview, ui


def _obj(context):
    return context.object


def _has_commits(context):
    o = _obj(context)
    return o is not None and len(o.ocv.commits) > 0


def _run(self, fn):
    try:
        fn()
    except core.OCVError as e:
        self.report({"ERROR"}, iface_(str(e)))
        return {"CANCELLED"}
    ui.invalidate()
    return {"FINISHED"}


class OCV_OT_commit(bpy.types.Operator):
    bl_idname = "ocv.commit"
    bl_label = "Commit"
    bl_description = "Store a restore point for the active object"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        o = _obj(context)
        return o is not None and not preview.is_active()

    def execute(self, context):
        o = _obj(context)
        msg = o.ocv.message.strip()

        def do():
            core.commit(context, o, msg)
            o.ocv.message = ""
        return _run(self, do)


class OCV_OT_checkout(bpy.types.Operator):
    bl_idname = "ocv.checkout"
    bl_label = "Checkout"
    bl_description = ("Overwrite the object with the selected commit; the "
                      "working state is stashed first if it differs from HEAD")
    bl_options = {"REGISTER", "UNDO"}
    cid: IntProperty(default=-1)

    @classmethod
    def poll(cls, context):
        return _has_commits(context) and core.is_editable(_obj(context))

    def execute(self, context):
        o = _obj(context)
        cid = self.cid
        if cid < 0:
            c = core.active_commit(o)
            if c is None:
                self.report({"ERROR"}, iface_("No commit selected"))
                return {"CANCELLED"}
            cid = c.cid

        def do():
            preview.exit(context)
            core.checkout(context, o, cid)
        return _run(self, do)


class OCV_OT_stash(bpy.types.Operator):
    bl_idname = "ocv.stash"
    bl_label = "Stash"
    bl_description = "Store the working state without moving HEAD"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _obj(context) is not None and not preview.is_active()

    def execute(self, context):
        o = _obj(context)
        return _run(self, lambda: core.stash(context, o))


class OCV_OT_stash_pop(bpy.types.Operator):
    bl_idname = "ocv.stash_pop"
    bl_label = "Pop"
    bl_description = "Restore the latest stash and remove it"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        o = _obj(context)
        return o is not None and core.latest_stash(o) is not None \
            and core.is_editable(o)

    def execute(self, context):
        o = _obj(context)

        def do():
            preview.exit(context)
            core.stash_pop(context, o)
        return _run(self, do)


class OCV_OT_delete(bpy.types.Operator):
    bl_idname = "ocv.delete"
    bl_label = "Delete Commit"
    bl_description = ("Delete the selected commit (later commits link to the "
                      "previous one)")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _has_commits(context)

    def execute(self, context):
        o = _obj(context)
        c = core.active_commit(o)
        if c is None:
            return {"CANCELLED"}
        cid = c.cid

        def do():
            if preview.is_active():
                preview.exit(context)
            core.delete_commit(o, cid)
        return _run(self, do)


class OCV_OT_preview_toggle(bpy.types.Operator):
    bl_idname = "ocv.preview_toggle"
    bl_label = "Browse"
    bl_description = ("Show the selected commit in the viewport instead of "
                      "the working object. Selecting in the list also enters "
                      "this mode; a click in the viewport or Esc leaves it")
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _has_commits(context) or preview.is_active()

    def execute(self, context):
        o = _obj(context)

        def do():
            if preview.is_active():
                preview.exit(context)
            else:
                preview.enter(context, o)
                preview.start_watcher()
        return _run(self, do)


def _mouse_in_viewport(context, event):
    """True if the mouse is over a 3D viewport's main region and not over
    its sidebar / toolbar / headers (which overlap the main region)."""
    x, y = event.mouse_x, event.mouse_y
    for area in context.window.screen.areas:
        if area.type != "VIEW_3D":
            continue
        if not (area.x <= x < area.x + area.width and
                area.y <= y < area.y + area.height):
            continue
        for r in area.regions:
            if r.type in {"UI", "TOOLS", "HEADER", "TOOL_HEADER", "FOOTER"} \
                    and r.width > 1 and r.height > 1 \
                    and r.x <= x < r.x + r.width \
                    and r.y <= y < r.y + r.height:
                return False
        return True
    return False


class OCV_OT_browse_watch(bpy.types.Operator):
    """Modal watcher: leave browse mode on a viewport click or Esc."""
    bl_idname = "ocv.browse_watch"
    bl_label = "Browse Watcher"
    bl_options = {"INTERNAL"}

    def invoke(self, context, event):
        if not preview.is_active():
            return {"CANCELLED"}
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if not preview.is_active():
            preview.watcher_stopped()
            return {"FINISHED"}
        if event.type == "ESC" and event.value == "PRESS":
            preview.exit(context)
            preview.watcher_stopped()
            return {"FINISHED"}
        if event.type == "LEFTMOUSE" and event.value == "PRESS" \
                and _mouse_in_viewport(context, event):
            preview.exit(context)
            preview.watcher_stopped()
            return {"PASS_THROUGH"}
        return {"PASS_THROUGH"}


class OCV_OT_verify(bpy.types.Operator):
    bl_idname = "ocv.verify"
    bl_label = "Verify"
    bl_description = "Check every commit's geometry against its stored hash"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return _has_commits(context)

    def execute(self, context):
        o = _obj(context)
        bad = []
        for c in o.ocv.commits:
            ok, msg = core.verify(o, c)
            if not ok:
                bad.append(f"#{c.cid}: {msg}")
        if bad:
            self.report({"WARNING"}, "; ".join(bad))
        else:
            self.report({"INFO"}, iface_("%d commits verified") % len(o.ocv.commits))
        return {"FINISHED"}


class OCV_OT_diff_working(bpy.types.Operator):
    bl_idname = "ocv.diff_working"
    bl_label = "Diff vs HEAD"
    bl_description = "Compare the working object with HEAD (computes a hash)"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return _has_commits(context) and not preview.is_active()

    def execute(self, context):
        o = _obj(context)
        lines = core.working_vs_head_diff(o)
        ui.set_working_diff(o.name, lines)
        if lines == ["no tracked changes"]:
            core.DIRTY.discard(o.name)
        return {"FINISHED"}


classes = (OCV_OT_commit, OCV_OT_checkout, OCV_OT_stash, OCV_OT_stash_pop,
           OCV_OT_delete, OCV_OT_preview_toggle, OCV_OT_browse_watch,
           OCV_OT_verify,
           OCV_OT_diff_working)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
