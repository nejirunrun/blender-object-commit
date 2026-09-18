"""Capture / apply object-level metadata, geometry hash, summary diff.

Geometry itself is stored as a native datablock copy (see core.py); this module
only deals with what lives on the Object (transform, parent, modifiers,
constraints, material slot assignment, custom props, display) plus a few
counts and a vertex-position hash used for verification.
"""
import hashlib
import json

import bpy

META_VERSION = 1

_SKIP_RNA = {
    "rna_type", "name", "type", "is_active", "show_expanded",
    "is_override_data", "persistent_uid", "execution_time", "is_valid",
    "error_location", "error_rotation", "active",
}
_SIMPLE = (bool, int, float, str)


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
        if ident in _SKIP_RNA or p.is_readonly or p.type == "COLLECTION":
            continue
        try:
            v = getattr(struct, ident)
        except Exception:
            continue
        if p.type == "POINTER":
            if isinstance(v, bpy.types.ID):
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
        v = _from_json_value(props[k])
        if isinstance(props[k], dict) and "__id" in props[k] and v is None:
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
    m["display"] = {
        "display_type": obj.display_type,
        "show_wire": obj.show_wire,
        "show_in_front": obj.show_in_front,
        "color": list(obj.color),
    }
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
               custom=True, display=True):
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
        d = m.get("display", {})
        for k, v in d.items():
            try:
                setattr(obj, k, v)
            except Exception:
                pass
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
    return lines or ["no tracked changes"]
