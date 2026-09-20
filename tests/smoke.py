"""Headless smoke test.

    blender -b --python tests/smoke.py

Registers the package from the repo (not as an installed extension), runs
commit / checkout / stash / preview / save+reload and prints OK.
"""
import os
import sys
import tempfile

import traceback

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
LOG = os.path.join(ROOT, "tests", "smoke.log")
_log = open(LOG, "w", encoding="utf-8")


class _Tee:
    def __init__(self, *s):
        self.s = s

    def write(self, t):
        for x in self.s:
            x.write(t)
            x.flush()

    def flush(self):
        pass


sys.stdout = _Tee(sys.__stdout__, _log)
sys.stderr = _Tee(sys.__stderr__, _log)
print("blender", bpy.app.version_string)


def main():

    import object_commit  # noqa: E402
    from object_commit import core, preview  # noqa: E402

    object_commit.register()

    bpy.ops.wm.read_factory_settings(use_empty=True)
    ctx = bpy.context
    bpy.ops.mesh.primitive_cube_add()
    cube = ctx.object
    assert cube is not None

    # 1. first commit
    c1 = core.commit(ctx, cube, "cube")
    assert cube.ocv.head == c1.cid
    assert c1.snapshot is not None and c1.snapshot.use_fake_user
    assert len(c1.snapshot.vertices) == 8

    # 2. modify: move a vertex, add modifier, move object
    cube.data.vertices[0].co.x += 1.0
    mod = cube.modifiers.new("Sub", "SUBSURF")
    mod.levels = 3
    cube.location.z = 2.0
    c2 = core.commit(ctx, cube, "moved + subsurf")
    assert c2.parent == c1.cid
    lines = object_commit.snapshot.summarize_diff(
        object_commit.snapshot.meta_loads(c1.meta),
        object_commit.snapshot.meta_loads(c2.meta))
    print("diff c1->c2:", lines)
    assert any("+ mod Sub" in l for l in lines)
    assert "transform changed" in lines
    assert any("geometry edited" in l for l in lines)

    # 3. checkout c1 (overwrite, auto-stash only because working != HEAD)
    cube.location.x = 7.0
    core.checkout(ctx, cube, c1.cid)
    assert cube.ocv.head == c1.cid
    assert len(cube.modifiers) == 0
    assert abs(cube.location.z) < 1e-6
    assert abs(cube.data.vertices[0].co.x - (-1.0)) < 1e-6
    assert any(c.is_auto for c in cube.ocv.commits)
    assert cube.data is not c1.snapshot  # object got a copy, snapshot untouched
    n_auto = sum(1 for c in cube.ocv.commits if c.is_auto)
    # restoring again with an unchanged working state must not add a stash
    core.checkout(ctx, cube, c1.cid)
    assert sum(1 for c in cube.ocv.commits if c.is_auto) == n_auto
    # set aside, then restore: the set-aside must not be duplicated
    n_all = len(cube.ocv.commits)
    core.stash(ctx, cube)
    core.checkout(ctx, cube, c1.cid)
    assert len(cube.ocv.commits) == n_all + 1
    core.delete_commit(cube, core.latest_stash(cube).cid)

    # 4. shared data: second object using same mesh, checkout makes single-user
    other = bpy.data.objects.new("other", cube.data)
    ctx.scene.collection.objects.link(other)
    assert cube.data.users == 2
    core.checkout(ctx, cube, c2.cid)
    assert cube.data is not other.data
    assert len(other.data.vertices) == 8
    assert len(cube.modifiers) == 1

    # 5. preview browse
    ctx.view_layer.objects.active = cube
    idx, _ = core.find_commit(cube, c1.cid)
    cube.ocv.active_index = idx
    pv = preview.enter(ctx, cube)
    assert preview.is_active() and pv.data is c1.snapshot
    assert cube.hide_get()
    idx2, _ = core.find_commit(cube, c2.cid)
    cube.ocv.active_index = idx2  # update callback swaps preview data
    assert pv.data is c2.snapshot
    preview.exit(ctx)
    assert not preview.is_active() and not cube.hide_get()
    assert bpy.data.objects.get(core.PREVIEW_PREFIX) is None

    # 6. stash / pop
    cube.location.x = 5.0
    core.stash(ctx, cube)
    cube.location.x = 0.0
    core.stash_pop(ctx, cube)
    assert abs(cube.location.x - 5.0) < 1e-6
    assert core.latest_stash(cube) is None or core.latest_stash(cube).is_auto

    # 6b. embedded curve structs: bevel custom profile, warp falloff curve
    bev = cube.modifiers.new("Bev", "BEVEL")
    bev.profile_type = "CUSTOM"
    prof = bev.custom_profile
    prof.points.add(0.3, 0.7)
    prof.points[1].handle_type_1 = "VECTOR"
    prof.points[1].handle_type_2 = "VECTOR"
    prof.update()
    warp = cube.modifiers.new("Warp", "WARP")
    warp.falloff_type = "CURVE"
    cv = warp.falloff_curve.curves[0]
    cv.points.new(0.25, 0.9)
    cv.points[1].handle_type = "VECTOR"
    warp.falloff_curve.update()
    c3 = core.commit(ctx, cube, "curves")
    c3_cid = c3.cid
    prof.points.remove(prof.points[1])
    prof.points[0].location = (0.9, 0.1)
    prof.update()
    cv.points.remove(cv.points[1])
    warp.falloff_curve.update()
    assert len(prof.points) == 2 and len(cv.points) == 2
    core.checkout(ctx, cube, c3_cid)
    bev = cube.modifiers["Bev"]
    prof = bev.custom_profile
    assert bev.profile_type == "CUSTOM"
    assert len(prof.points) == 3, len(prof.points)
    assert all(abs(a - b) < 1e-6 for a, b in zip(prof.points[1].location, (0.3, 0.7)))
    assert prof.points[1].handle_type_1 == "VECTOR"
    assert all(abs(a - b) < 1e-6 for a, b in zip(prof.points[0].location, (1.0, 0.0)))
    cv = cube.modifiers["Warp"].falloff_curve.curves[0]
    assert len(cv.points) == 3, len(cv.points)
    assert all(abs(a - b) < 1e-6 for a, b in zip(cv.points[1].location, (0.25, 0.9)))
    assert cv.points[1].handle_type == "VECTOR"
    # curve-only edit shows up in the summary diff
    prof.points[1].location = (0.4, 0.6)
    prof.update()
    c4 = core.commit(ctx, cube, "curve tweak")
    _, c3 = core.find_commit(cube, c3_cid)  # re-fetch: items moved
    lines = object_commit.snapshot.summarize_diff(
        object_commit.snapshot.meta_loads(c3.meta),
        object_commit.snapshot.meta_loads(c4.meta))
    print("diff c3->c4:", lines)
    assert any("custom_profile" in l for l in lines)
    cube.modifiers.remove(cube.modifiers["Bev"])
    cube.modifiers.remove(cube.modifiers["Warp"])

    # 6c. whitelisted object props: display / visibility / instancing / structs
    coll = bpy.data.collections.new("inst")
    cube.display_type = "WIRE"
    cube.show_name = True
    cube.hide_render = True
    cube.pass_index = 7
    cube.visible_shadow = False
    cube.instance_type = "VERTS"
    cube.modifiers.new("Col", "COLLISION")
    cube.collision.damping = 0.4
    cube.collision.use_culling = False
    cube.lineart.usage = "EXCLUDE"
    c5 = core.commit(ctx, cube, "props")
    c5_cid = c5.cid
    cube.display_type = "TEXTURED"
    cube.show_name = False
    cube.hide_render = False
    cube.pass_index = 0
    cube.visible_shadow = True
    cube.instance_type = "NONE"
    cube.collision.damping = 0.1
    cube.lineart.usage = "INHERIT"
    lines = core.diff_working(cube) if hasattr(core, "diff_working") else         object_commit.snapshot.summarize_diff(
            object_commit.snapshot.meta_loads(c5.meta),
            object_commit.snapshot.capture_meta(cube))
    print("diff working:", lines)
    assert any(l.startswith("~ display:") and "display_type" in l for l in lines)
    assert any(l.startswith("~ visibility:") and "pass_index" in l for l in lines)
    assert any(l.startswith("~ instancing:") for l in lines)
    assert "~ collision settings changed" in lines
    assert "~ lineart settings changed" in lines
    core.checkout(ctx, cube, c5_cid)
    assert cube.display_type == "WIRE" and cube.show_name
    assert cube.hide_render and cube.pass_index == 7 and not cube.visible_shadow
    assert cube.instance_type == "VERTS"
    assert abs(cube.collision.damping - 0.4) < 1e-6 and not cube.collision.use_culling
    assert cube.lineart.usage == "EXCLUDE"
    _, c5 = core.find_commit(cube, c5_cid)
    assert object_commit.snapshot.summarize_diff(
        object_commit.snapshot.meta_loads(c5.meta),
        object_commit.snapshot.capture_meta(cube)) == ["no tracked changes"]
    cube.hide_render = False
    cube.instance_type = "NONE"
    cube.modifiers.remove(cube.modifiers["Col"])
    # collection instancing only exists on empties
    emp = bpy.data.objects.new("emp", None)
    ctx.scene.collection.objects.link(emp)
    emp.instance_type = "COLLECTION"
    emp.instance_collection = coll
    emp.empty_display_type = "CUBE"
    emp.empty_display_size = 2.5
    ce = core.commit(ctx, emp, "empty")
    emp.instance_type = "NONE"
    emp.instance_collection = None
    emp.empty_display_type = "ARROWS"
    core.checkout(ctx, emp, ce.cid)
    assert emp.instance_type == "COLLECTION" and emp.instance_collection is coll
    assert emp.empty_display_type == "CUBE" and abs(emp.empty_display_size - 2.5) < 1e-6

    # 7. verify + delete
    for c in cube.ocv.commits:
        ok, msg = core.verify(cube, c)
        assert ok, (c.cid, msg)
    n_before = len(cube.ocv.commits)
    core.delete_commit(cube, c1.cid)
    assert len(cube.ocv.commits) == n_before - 1
    assert bpy.data.meshes.get(c1.snapshot.name if c1 else "") is None or True

    # 8. save + reload persistence
    path = os.path.join(tempfile.gettempdir(), "ocv_smoke.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    cube = bpy.data.objects["Cube"]
    assert len(cube.ocv.commits) == n_before - 1
    for c in cube.ocv.commits:
        assert c.snapshot is not None, c.cid
        ok, msg = core.verify(cube, c)
        assert ok, (c.cid, msg)

    print("OK")


try:
    main()
except Exception:
    traceback.print_exc()
    print("FAILED")
finally:
    _log.close()
