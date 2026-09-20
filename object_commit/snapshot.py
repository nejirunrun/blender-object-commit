"""Capture / apply object-level metadata, geometry hash, summary diff.

Geometry itself is stored as a native datablock copy (see core.py); this module
only deals with what lives on the Object (transform, parent, modifiers,
constraints, material slot assignment, custom props, display) plus a few
counts and a vertex-position hash used for verification.
"""
import hashlib
import json

import bpy

META_VERSION = 3

_SKIP_RNA = {
    "rna_type", "name", "is_active", "show_expanded",
    "is_override_data", "persistent_uid", "execution_time", "is_valid",
    "error_location", "error_rotation", "active",
}
_SIMPLE = (bool, int, float, str)

# Object-level properties captured by name (whitelist). Everything not listed
# here, in capture_meta, or inside obj.data is *not* part of a snapshot; keep
# README "含まれないもの" in sync when changing these.
_OBJ_DISPLAY = (
    "display_type", "show_wire", "show_in_front", "color", "show_name",
    "show_axis", "show_texture_space", "show_bounds", "display_bounds_type",
    "show_all_edges", "show_only_shape_key", "use_shape_key_edit_mode",
    "active_material_index", "active_shape_key_index",
    "empty_display_type", "empty_display_size", "empty_image_offset",
    "empty_image_depth", "empty_image_side", "show_empty_image_orthographic",
    "show_empty_image_perspective", "show_empty_image_only_axis_aligned",
    "use_empty_image_alpha", "add_rest_position_attribute",
    "use_grease_pencil_lights", "use_camera_lock_parent",
)
_OBJ_VISIBILITY = (
    "hide_render", "hide_viewport", "hide_select", "pass_index",
    "visible_camera", "visible_diffuse", "visible_glossy",
    "visible_transmission", "visible_volume_scatter", "visible_shadow",
    "is_holdout", "is_shadow_catcher",
)
_OBJ_INSTANCING = (
    "instance_type", "instance_collection", "instance_faces_scale",
    "use_instance_vertices_rotation", "use_instance_faces_scale",
    "show_instancer_for_viewport", "show_instancer_for_render",
)
# Embedded settings structs. Physics that need an operator to exist (field,
# rigid_body, soft_body, cloth, particle systems) are out of scope.
_OBJ_STRUCTS = ("collision", "lineart")


# ---------------------------------------------------------------- ID refs
def _id_ref(idblock):
    return {"__id": [idblock.__class__.__name__, idblock.name]}


def _resolve_id(ref):
    cls, name = ref["__id"]
    for attr in dir(bpy.data):
        coll = getattr(bpy.data, attr, None)
        if not isinstance(coll, bpy.types.bpy_prop_collection):
            continue
        idb = coll.get(name)
        if idb is not None and idb.__class__.__name__ == cls:
            return idb
    return None


def _to_json_value(v):
    if isinstance(v, bpy.types.ID):
        return _id_ref(v)
    if isinstance(v, _SIMPLE):
        return v
    if isinstance(v, (set, frozenset)):
        return {"__set": sorted(v)}
    try:
        return [_to_json_value(x) for x in v]
    except TypeError:
        return None


def _from_json_value(v):
    if isinstance(v, dict):
        if "__id" in v:
            return _resolve_id(v)
        if "__set" in v:
            return set(v["__set"])
    return v


# ---------------------------------------------------------------- RNA structs
def _serialize_rna(struct):
    out = {}
    for p in struct.bl_rna.properties:
        ident = p.identifier
        if ident in _SKIP_RNA or p.type == "COLLECTION":
            continue
        if p.is_readonly and p.type != "POINTER":
            continue
        try:
            v = getattr(struct, ident)
        except Exception:
            continue
        if p.type == "POINTER":
            # Embedded curve structs are read-only pointers whose contents
            # are writable (Bevel custom_profile, Warp/Hook falloff_curve).
            if isinstance(v, bpy.types.CurveProfile):
                out[ident] = {"__curve_profile": _serialize_profile(v)}
            elif isinstance(v, bpy.types.CurveMapping):
                out[ident] = {"__curve_mapping": _serialize_mapping(v)}
            elif isinstance(v, bpy.types.ID) and not p.is_readonly:
                out[ident] = _id_ref(v)
            continue
        jv = _to_json_value(v)
        if jv is not None:
            out[ident] = jv
    return out


def _serialize_idprops(struct):
    out = {}
    try:
        keys = list(struct.keys())
    except TypeError:  # this RNA type has no ID properties
        return out
    for k in keys:
        try:
            v = struct[k]
        except Exception:
            continue
        if isinstance(v, bpy.types.ID):
            out[k] = _id_ref(v)
        elif isinstance(v, _SIMPLE):
            out[k] = v
        elif hasattr(v, "to_list"):
            out[k] = v.to_list()
    return out


def _apply_rna(struct, props):
    # Pointer props (e.g. node_group) first: other props may depend on them.
    keys = sorted(props.keys(),
                  key=lambda k: 0 if isinstance(props[k], dict) else 1)
    for k in keys:
        raw = props[k]
        if isinstance(raw, dict) and ("__curve_profile" in raw
                                      or "__curve_mapping" in raw):
            # Embedded (non-ID) struct: the pointer is read-only, fill it in.
            try:
                target = getattr(struct, k)
                if "__curve_profile" in raw:
                    _apply_profile(target, raw["__curve_profile"])
                else:
                    _apply_mapping(target, raw["__curve_mapping"])
            except Exception:
                pass
            continue
        v = _from_json_value(raw)
        if isinstance(raw, dict) and "__id" in raw and v is None:
            continue
        try:
            setattr(struct, k, v)
        except Exception:
            pass


def _apply_idprops(struct, props):
    for k, v in props.items():
        val = _from_json_value(v)
        if isinstance(v, dict) and "__id" in v and val is None:
            continue
        try:
            struct[k] = val
        except Exception:
            pass


# ------------------------------------------------- embedded curve structs
# CurveProfile (Bevel custom profile) and CurveMapping (falloff curves) are
# not IDs; they live inside the modifier and only their contents are writable.
def _resize_points(points, n, add):
    while len(points) > max(n, 2):
        points.remove(points[1])
    while len(points) < n:
        add(points)


def _serialize_profile(prof):
    d = _serialize_rna(prof)
    d["points"] = [[*pt.location, pt.handle_type_1, pt.handle_type_2]
                   for pt in prof.points]
    return d


def _apply_profile(prof, d):
    pts = d.get("points", [])
    _apply_rna(prof, {k: v for k, v in d.items() if k != "points"})
    _resize_points(prof.points, len(pts), lambda c: c.add(0.5, 0.5))
    # The handle_type setters act on every *selected* point, so select
    # exactly one point at a time.
    for pt in prof.points:
        pt.select = False
    for pt, (x, y, h1, h2) in zip(prof.points, pts):
        pt.location = (x, y)
        pt.select = True
        pt.handle_type_1 = h1
        pt.handle_type_2 = h2
        pt.select = False
    prof.update()


def _serialize_mapping(cm):
    d = _serialize_rna(cm)
    d["curves"] = [[[*pt.location, pt.handle_type] for pt in c.points]
                   for c in cm.curves]
    return d


def _apply_mapping(cm, d):
    _apply_rna(cm, {k: v for k, v in d.items() if k != "curves"})
    for curve, pts in zip(cm.curves, d.get("curves", [])):
        _resize_points(curve.points, len(pts), lambda c: c.new(0.5, 0.5))
        for pt, (x, y, h) in zip(curve.points, pts):
            pt.location = (x, y)
            pt.handle_type = h
    cm.update()


def _serialize_stack(stack):
    return [{
        "name": it.name,
        "type": it.type,
        "props": _serialize_rna(it),
        "idprops": _serialize_idprops(it),
    } for it in stack]


def _apply_stack(stack, items):
    stack.clear()
    for it in items:
        try:
            new = stack.new(it["name"], it["type"])
        except Exception:
            continue
        if new is None:
            continue
        _apply_rna(new, it.get("props", {}))
        _apply_idprops(new, it.get("idprops", {}))


# ---------------------------------------------------------------- geometry
def geometry_hash(data):
    if data is None:
        return ""
    if isinstance(data, bpy.types.Mesh):
        import numpy as np
        n = len(data.vertices)
        co = np.empty(n * 3, dtype=np.float32)
        data.vertices.foreach_get("co", co)
        h = hashlib.blake2b(co.tobytes(), digest_size=8)
        h.update(str(len(data.edges)).encode())
        h.update(str(len(data.polygons)).encode())
        return h.hexdigest()
    return ""


def geometry_counts(data):
    c = {}
    if data is None:
        return c
    if isinstance(data, bpy.types.Mesh):
        c["verts"] = len(data.vertices)
        c["edges"] = len(data.edges)
        c["faces"] = len(data.polygons)
        c["attributes"] = sorted(a.name for a in data.attributes
                                 if not a.name.startswith("."))
        c["shape_keys"] = (len(data.shape_keys.key_blocks)
                           if data.shape_keys else 0)
    elif isinstance(data, bpy.types.Curve):
        c["splines"] = len(data.splines)
    return c


# ---------------------------------------------------------------- capture
def _capture_props(struct, names):
    out = {}
    for n in names:
        try:
            v = getattr(struct, n)
        except AttributeError:
            continue
        if isinstance(v, bpy.types.ID):
            out[n] = _id_ref(v)
        elif v is None:
            out[n] = {"__id": None}
        else:
            jv = _to_json_value(v)
            if jv is not None:
                out[n] = jv
    return out


def _apply_props(struct, props):
    for k, raw in props.items():
        if isinstance(raw, dict) and "__id" in raw:
            v = _resolve_id(raw) if raw["__id"] else None
            if v is None and raw["__id"]:
                continue  # referenced datablock is gone: leave as is
        else:
            v = _from_json_value(raw)
        try:
            setattr(struct, k, v)
        except Exception:
            pass


def capture_meta(obj):
    m = {"ver": META_VERSION, "obj_type": obj.type}
    m["matrix_basis"] = [list(r) for r in obj.matrix_basis]
    m["rotation_mode"] = obj.rotation_mode
    m["delta_location"] = list(obj.delta_location)
    m["delta_rotation_euler"] = list(obj.delta_rotation_euler)
    m["delta_scale"] = list(obj.delta_scale)
    m["parent"] = obj.parent.name if obj.parent else None
    m["parent_type"] = obj.parent_type
    m["parent_bone"] = obj.parent_bone
    m["matrix_parent_inverse"] = [list(r) for r in obj.matrix_parent_inverse]
    m["modifiers"] = _serialize_stack(obj.modifiers)
    m["constraints"] = _serialize_stack(obj.constraints)
    m["slots"] = [{"link": s.link,
                   "material": s.material.name if s.material else None}
                  for s in obj.material_slots]
    m["vertex_groups"] = [vg.name for vg in obj.vertex_groups]
    m["display"] = _capture_props(obj, _OBJ_DISPLAY)
    m["visibility"] = _capture_props(obj, _OBJ_VISIBILITY)
    m["instancing"] = _capture_props(obj, _OBJ_INSTANCING)
    m["structs"] = {}
    for name in _OBJ_STRUCTS:
        st = getattr(obj, name, None)
        if st is not None:
            m["structs"][name] = _serialize_rna(st)
    m["custom"] = {k: v for k, v in _serialize_idprops(obj).items()
                   if k != "ocv"}
    m["counts"] = geometry_counts(obj.data)
    m["hash"] = geometry_hash(obj.data)
    m["data_name"] = obj.data.name if obj.data else None
    return m


def meta_dumps(m):
    return json.dumps(m, ensure_ascii=False, separators=(",", ":"))


def meta_loads(s):
    try:
        return json.loads(s) if s else {}
    except ValueError:
        return {}


# ---------------------------------------------------------------- apply
def apply_meta(obj, m, *, transform=True, stacks=True, slots=True,
               custom=True, display=True, visibility=True):
    from mathutils import Matrix
    if transform:
        try:
            obj.rotation_mode = m.get("rotation_mode", obj.rotation_mode)
            obj.matrix_basis = Matrix(m["matrix_basis"])
            obj.delta_location = m["delta_location"]
            obj.delta_rotation_euler = m["delta_rotation_euler"]
            obj.delta_scale = m["delta_scale"]
        except Exception:
            pass
        pname = m.get("parent")
        parent = bpy.data.objects.get(pname) if pname else None
        if parent is not obj:
            obj.parent = parent
            if parent is not None:
                try:
                    obj.parent_type = m.get("parent_type", "OBJECT")
                    obj.parent_bone = m.get("parent_bone", "")
                except Exception:
                    pass
                try:
                    obj.matrix_parent_inverse = Matrix(
                        m["matrix_parent_inverse"])
                except Exception:
                    pass
    if stacks:
        _apply_stack(obj.modifiers, m.get("modifiers", []))
        _apply_stack(obj.constraints, m.get("constraints", []))
    if slots:
        for i, s in enumerate(m.get("slots", [])):
            if i >= len(obj.material_slots):
                break
            slot = obj.material_slots[i]
            try:
                slot.link = s["link"]
                mat = bpy.data.materials.get(s["material"]) \
                    if s["material"] else None
                slot.material = mat
            except Exception:
                pass
    if display:
        _apply_props(obj, m.get("display", {}))
        _apply_props(obj, m.get("instancing", {}))
        for name, props in m.get("structs", {}).items():
            st = getattr(obj, name, None)
            if st is not None:
                _apply_rna(st, props)
    if visibility:
        _apply_props(obj, m.get("visibility", {}))
    if custom:
        _apply_idprops(obj, m.get("custom", {}))


# ---------------------------------------------------------------- diff
def _matrix_close(a, b, tol=1e-6):
    try:
        return all(abs(x - y) <= tol
                   for ra, rb in zip(a, b) for x, y in zip(ra, rb))
    except Exception:
        return False


def _stack_diff(old, new, label):
    lines = []
    o = {it["name"]: it for it in old}
    n = {it["name"]: it for it in new}
    for name in n:
        if name not in o:
            lines.append(f"+ {label} {name} ({n[name]['type']})")
    for name in o:
        if name not in n:
            lines.append(f"- {label} {name} ({o[name]['type']})")
    for name in n:
        if name in o:
            a, b = o[name], n[name]
            if a["type"] != b["type"]:
                lines.append(f"~ {label} {name}: type {a['type']} > {b['type']}")
                continue
            changed = [k for k in set(a["props"]) | set(b["props"])
                       if a["props"].get(k) != b["props"].get(k)]
            changed += [k for k in set(a["idprops"]) | set(b["idprops"])
                        if a["idprops"].get(k) != b["idprops"].get(k)]
            if changed:
                lines.append(f"~ {label} {name}: " + ", ".join(sorted(changed)))
    if old and new and [it["name"] for it in old] != [it["name"] for it in new] \
            and set(o) == set(n):
        lines.append(f"~ {label} order changed")
    return lines


def summarize_diff(old, new):
    """Lines describing what changed from meta `old` to meta `new`."""
    lines = []
    if not old:
        return ["(first commit)"]
    oc, nc = old.get("counts", {}), new.get("counts", {})
    parts = []
    for k in ("verts", "edges", "faces", "shape_keys", "splines"):
        if k in oc or k in nc:
            a, b = oc.get(k, 0), nc.get(k, 0)
            if a != b:
                parts.append(f"{k}: {a} > {b} ({b - a:+d})")
    lines += parts
    oa, na = set(oc.get("attributes", [])), set(nc.get("attributes", []))
    for x in sorted(na - oa):
        lines.append(f"+ attribute {x}")
    for x in sorted(oa - na):
        lines.append(f"- attribute {x}")
    if old.get("hash") != new.get("hash") and not parts:
        lines.append("geometry edited (same counts)")
    og, ng = set(old.get("vertex_groups", [])), set(new.get("vertex_groups", []))
    for x in sorted(ng - og):
        lines.append(f"+ vgroup {x}")
    for x in sorted(og - ng):
        lines.append(f"- vgroup {x}")
    lines += _stack_diff(old.get("modifiers", []), new.get("modifiers", []),
                         "mod")
    lines += _stack_diff(old.get("constraints", []),
                         new.get("constraints", []), "con")
    if not _matrix_close(old.get("matrix_basis", []),
                         new.get("matrix_basis", [])):
        lines.append("transform changed")
    if old.get("parent") != new.get("parent"):
        lines.append(f"parent: {old.get('parent')} > {new.get('parent')}")
    if old.get("slots") != new.get("slots"):
        lines.append("material slots changed")
    if old.get("custom") != new.get("custom"):
        lines.append("custom properties changed")
    for key in ("display", "visibility", "instancing"):
        a, b = old.get(key, {}), new.get(key, {})
        changed = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
        if changed:
            lines.append(f"~ {key}: " + ", ".join(changed))
    oa, na = old.get("structs", {}), new.get("structs", {})
    for key in sorted(set(oa) | set(na)):
        if oa.get(key) != na.get(key):
            lines.append(f"~ {key} settings changed")
    return lines or ["no tracked changes"]
