import bpy
from bpy.app.translations import pgettext_iface as iface_

from . import core, preview
from . import snapshot as snap

# (obj name, cid, parent) -> diff lines against parent
_diff_cache = {}
# obj name -> lines of working vs current point
_working_diff = {}

_ICONS = None


def _icon(name):
    """Return `name` if this Blender has that icon, else 'NONE'.

    A bad icon name raises inside draw() and silently truncates the panel.
    """
    global _ICONS
    if _ICONS is None:
        fn = bpy.types.UILayout.bl_rna.functions["label"]
        _ICONS = {e.identifier for e in fn.parameters["icon"].enum_items}
    return name if name in _ICONS else "NONE"


def invalidate():
    _diff_cache.clear()
    _working_diff.clear()


def set_working_diff(name, lines):
    _working_diff[name] = lines


def commit_diff_lines(obj, c):
    key = (obj.name, c.cid, c.parent)
    if key not in _diff_cache:
        _, p = core.find_commit(obj, c.parent)
        old = snap.meta_loads(p.meta) if p else {}
        _diff_cache[key] = snap.summarize_diff(old, snap.meta_loads(c.meta))
    return _diff_cache[key]


class OCV_UL_commits(bpy.types.UIList):
    bl_idname = "OCV_UL_commits"

    def draw_item(self, context, layout, data, item, icon, active_data,
                  active_propname, index):
        obj = context.object
        row = layout.row(align=True)
        if item.cid == data.head:
            row.label(text="", icon=_icon("KEYTYPE_KEYFRAME_VEC"))
        else:
            row.label(text="", icon=_icon("BLANK1"))
        text = f"#{item.cid} {item.message}"
        if item.is_auto:
            text = iface_("[auto]") + " " + text
        elif item.is_stash:
            text = iface_("[stash]") + " " + text
        if item.tag:
            text += f"  <{item.tag}>"
        row.label(text=text, translate=False)
        if item.snapshot is None and obj is not None and obj.data is not None:
            row.label(text="", icon=_icon("ERROR"))
        row.label(text=item.timestamp[5:16], translate=False)

    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        n = len(items)
        flags = [self.bitflag_filter_item] * n
        order = [n - 1 - i for i in range(n)]  # newest first
        return flags, order


class OCV_PT_panel(bpy.types.Panel):
    bl_label = "Object Commits"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Commits"

    def draw(self, context):
        layout = self.layout
        obj = context.object
        if obj is None:
            layout.label(text="No active object")
            return
        st = obj.ocv
        browsing = preview.is_active()

        # --- operations
        col = layout.column(align=True)
        row = col.row(align=True)
        row.prop(st, "message", text="")
        row.operator("ocv.commit", text="", icon=_icon("ADD"))
        row = col.row(align=True)
        row.operator("ocv.checkout", icon=_icon("CHECKMARK"))
        row.operator("ocv.stash", icon=_icon("PACKAGE"))
        row.operator("ocv.stash_pop", icon=_icon("LOOP_BACK"))
        row.operator("ocv.delete", text="", icon=_icon("TRASH"))

        # --- state line
        box = layout.box()
        row = box.row()
        head = core.head_commit(obj)
        headtxt = iface_("HEAD #%d") % head.cid if head else iface_("HEAD: none")
        dirty = obj.name in core.DIRTY
        row.label(text=headtxt, icon=_icon("KEYTYPE_KEYFRAME_VEC"),
                  translate=False)
        row.label(text="modified" if dirty else "clean",
                  icon=_icon("RADIOBUT_ON" if dirty else "RADIOBUT_OFF"))
        if browsing:
            box.label(text="Browsing: the viewport shows the selected commit",
                      icon=_icon("INFO"))
        if not core.is_editable(obj):
            box.label(text="Linked / override: checkout disabled",
                      icon=_icon("LINKED"))
        if obj.data is not None and obj.data.users > 1:
            box.label(text=iface_("Data shared by %d objects; checkout makes "
                             "this one single-user") % obj.data.users,
                      icon=_icon("ERROR"), translate=False)

        # --- list
        layout.template_list("OCV_UL_commits", "", st, "commits", st,
                             "active_index", rows=6)
        c = core.active_commit(obj)
        if c is not None:
            row = layout.row(align=True)
            row.prop(c, "tag", text="Tag")
            row.prop(c, "message", text="")

            # --- what changed (vs parent)
            box = layout.box()
            ptxt = f"#{c.parent}" if c.parent >= 0 else iface_("root")
            box.label(text=iface_("Diff #%d vs %s") % (c.cid, ptxt),
                      icon=_icon("ARROW_LEFTRIGHT"), translate=False)
            lines = box.column(align=True)
            for line in commit_diff_lines(obj, c):
                lines.label(text=line)

        # --- working vs current point
        box = layout.box()
        row = box.row()
        row.label(text="Diff vs HEAD", icon=_icon("ARROW_LEFTRIGHT"))
        row.operator("ocv.diff_working", text="", icon=_icon("FILE_REFRESH"))
        lines = box.column(align=True)
        for line in _working_diff.get(obj.name, []):
            lines.label(text=line)

        # --- stats
        n, verts = core.history_stats(obj)
        row = layout.row()
        row.label(text=iface_("%d commits, %d snapshots, %s verts")
                  % (len(st.commits), n, f"{verts:,}"), translate=False)
        row.operator("ocv.verify", text="", icon=_icon("VIEWZOOM"))


classes = (OCV_UL_commits, OCV_PT_panel)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
