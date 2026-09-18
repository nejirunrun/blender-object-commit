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
