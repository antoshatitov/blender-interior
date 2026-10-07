"""Blender-background regression fixtures for scene_checks; never run in a live scene.

Invoke run(output_directory). Creates disposable fixtures only in this process.
"""
import importlib.util
import json
from pathlib import Path
import bpy
from mathutils import Vector


def run(output_directory):
    if not bpy.app.background:
        raise RuntimeError("Regression fixtures must run in a disposable background process")
    spec = importlib.util.spec_from_file_location("bi_qa", str(Path(__file__).with_name("scene_checks.py")))
    qa = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(qa)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    s = bpy.context.scene
    out = Path(output_directory)
    out.mkdir(parents=True, exist_ok=True)
    passed = []

    def verify(name, condition):
        assert condition, name
        passed.append(name)

    def mesh(name, vertices, faces, edges=()):
        data = bpy.data.meshes.new(name)
        data.from_pydata(vertices, edges, faces)
        data.update()
        obj = bpy.data.objects.new(name, data)
        s.collection.objects.link(obj)
        return obj

    def cube(name, centre=(0, 0, 0), dimensions=(1, 1, 1)):
        x, y, z = [d / 2 for d in dimensions]
        obj = mesh(name, [(-x,-y,-z),(-x,-y,z),(-x,y,-z),(-x,y,z),
                          (x,-y,-z),(x,-y,z),(x,y,-z),(x,y,z)],
                   [(0,4,6,2),(1,3,7,5),(0,1,5,4),(2,6,7,3),(0,2,3,1),(4,5,7,6)])
        obj.location = centre
        obj["bi_surface"] = "solid"
        return obj

    solid = cube("protected_structure", (8, 8, 0))
    mat = bpy.data.materials.new("shared_finish")
    mat.use_nodes = True
    solid.data.materials.append(mat)
    curve = bpy.data.curves.new("curve_fixture", "CURVE")
    curve.dimensions = "3D"; curve.bevel_depth = .02; curve.use_fill_caps = True
    spline = curve.splines.new("POLY"); spline.points.add(1)
    spline.points[0].co = (0, 0, 0, 1); spline.points[1].co = (0, 0, 1, 1)
    curve_obj = bpy.data.objects.new("protected_curve", curve)
    s.collection.objects.link(curve_obj)
    bpy.context.view_layer.update()
    raw_curve_keys = set(curve.keys())
    baseline = qa.snapshot([solid.name, curve_obj.name])
    verify("snapshot_does_not_create_curve_settings", set(curve.keys()) == raw_curve_keys)
    verify("unchanged_snapshot", qa.compare_snapshot(baseline)["ok"])
    verify("repeated_snapshot_is_stable", qa.snapshot([solid.name, curve_obj.name]) == baseline)

    def lazy_extension_get(data):
        data["review_getter_side_effect"] = True
        return 1.0

    bpy.types.Curve.review_lazy_extension = bpy.props.FloatProperty(get=lazy_extension_get)
    try:
        raw_curve_keys = set(curve.keys())
        qa.snapshot([curve_obj.name])
        verify("snapshot_skips_lazy_extension_getters", set(curve.keys()) == raw_curve_keys)
    finally:
        del bpy.types.Curve.review_lazy_extension

    principal = mat.node_tree.nodes.get("Principled BSDF")
    for node_type, input_name, changed_value in (
        ("ShaderNodeValue", "Roughness", .123),
        ("ShaderNodeRGB", "Base Color", (.1, .2, .3, 1)),
    ):
        node = mat.node_tree.nodes.new(node_type)
        mat.node_tree.links.new(node.outputs[0], principal.inputs[input_name])
        original = node.outputs[0].default_value
        original = tuple(original) if node_type == "ShaderNodeRGB" else original
        material_before = qa.snapshot([solid.name])
        node.outputs[0].default_value = changed_value
        verify(node_type + "_output_change_detected", not qa.compare_snapshot(material_before)["ok"])
        node.outputs[0].default_value = original
        mat.node_tree.nodes.remove(node)
    baseline = qa.snapshot([solid.name, curve_obj.name])
    mat.node_tree.nodes.get("Principled BSDF").inputs["Roughness"].default_value = .123
    verify("shared_material_change_detected", not qa.compare_snapshot(baseline)["ok"])
    mat.node_tree.nodes.get("Principled BSDF").inputs["Roughness"].default_value = .5
    baseline = qa.snapshot([solid.name, curve_obj.name])
    curve.splines[0].points[1].co.z = 1.2
    verify("curve_change_detected", not qa.compare_snapshot(baseline)["ok"])
    curve.splines[0].points[1].co.z = 1
    bpy.context.view_layer.update()
    baseline = qa.snapshot([solid.name, curve_obj.name])
    solid.hide_render = True
    verify("visibility_change_detected", not qa.compare_snapshot(baseline)["ok"])
    solid.hide_render = False
    baseline = qa.snapshot([solid.name, curve_obj.name])
    solid.hide_set(True)
    verify("per_layer_visibility_detected", not qa.compare_snapshot(baseline)["ok"])
    solid.hide_set(False)
    ramp = mat.node_tree.nodes.new('ShaderNodeValToRGB')
    baseline_ramp = qa.snapshot([solid.name])
    ramp.color_ramp.elements[0].color = (.1,.2,.3,1)
    verify("color_ramp_change_detected", not qa.compare_snapshot(baseline_ramp)["ok"])
    mat.node_tree.nodes.remove(ramp)
    baseline = qa.snapshot([solid.name, curve_obj.name])

    plane = mesh("intentional_open_panel", [(0,0,0),(1,0,0),(1,1,0),(0,1,0)], [(0,1,2,3)])
    plane["bi_surface"] = "open"
    duplicate = mesh("overlapping_panel", [(.2,.2,0),(.8,.2,0),(.8,.8,0),(.2,.8,0)], [(0,1,2,3)])
    adjacent = mesh("edge_contact_panel", [(1,0,0),(2,0,0),(2,1,0),(1,1,0)], [(0,1,2,3)])
    vertical = mesh("crossing_panel", [(.5,.1,-.5),(.5,.9,-.5),(.5,.9,.5),(.5,.1,.5)], [(0,1,2,3)])
    degenerate = mesh("degenerate_fixture", [(0,0,3),(1,0,3),(2,0,3)], [(0,1,2)])
    wire = mesh("wire_fixture", [(0,0,4),(1,0,4)], [], [(0,1)])
    broken = mesh("open_solid_fixture", [(3,0,0),(4,0,0),(4,1,0),(3,1,0)], [(0,1,2,3)])
    broken["bi_surface"] = "solid"
    bpy.context.view_layer.update()
    mesh_count = len(bpy.data.meshes)
    audit = qa.audit_scene()
    rows = {r["object"]: r for r in audit["rows"]}
    verify("no_audit_errors", not audit["errors"])
    verify("solid_cube_valid", not rows[solid.name]["confirmed"])
    verify("intentional_open_surface_not_repaired", not rows[plane.name]["confirmed"])
    verify("degenerate_face_detected", rows[degenerate.name]["degenerate_faces"] > 0)
    verify("wire_edge_detected", rows[wire.name]["wire_edges"] == 1)
    verify("open_solid_detected", "solid_surface_not_closed" in rows[broken.name]["confirmed"])
    verify("audit_does_not_create_datablocks", len(bpy.data.meshes) == mesh_count)
    verify("coplanar_overlap_detected", qa.inspect_pair(plane, duplicate)["coplanar_overlap_triangle_pairs"] > 0)
    verify("shared_edge_is_not_coplanar_overlap", qa.inspect_pair(plane, adjacent)["coplanar_overlap_triangle_pairs"] == 0)
    verify("crossing_surfaces_detected", qa.inspect_pair(plane, vertical)["crossing_surface_candidates"] > 0)
    seam = mesh("crossing_at_mesh_seam", [(.5,.1,-.5),(.5,.9,-.5),(.5,.1,0),
                                         (.5,.9,0),(.5,.1,.5),(.5,.9,.5)],
                [(0,1,3,2),(2,3,5,4)])
    bpy.context.view_layer.update()
    seam_result = qa.inspect_pair(plane, seam)
    verify("seam_crossing_requires_further_check", not seam_result["truncated"] and
           seam_result["ambiguous_contact_triangle_pairs"] > 0)
    verify("pair_budget_explicit", qa.pair_candidates(max_pairs=1)["truncated"])

    wall = cube("protected_boolean_wall", (12, 0, 0), (4, 1, 3))
    cutter = cube("allowed_boolean_cutter", (12, 0, 0), (1, 2, 1))
    boolean = wall.modifiers.new("opening", "BOOLEAN")
    boolean.operation = "DIFFERENCE"; boolean.object = cutter
    bpy.context.view_layer.update()
    wall_before = qa.snapshot([wall.name])
    mesh_count = len(bpy.data.meshes)
    cutter.location.x += .5
    bpy.context.view_layer.update()
    wall_compare = qa.compare_snapshot(wall_before)
    verify("boolean_dependency_change_detected", not wall_compare["ok"] and
           "evaluated_geometry" in wall_compare["changed"][0]["components"])
    cutter.location.x -= .5
    bpy.context.view_layer.update()
    verify("boolean_dependency_restored", qa.compare_snapshot(wall_before)["ok"])
    verify("evaluated_snapshot_does_not_leak_meshes", len(bpy.data.meshes) == mesh_count)
    # ID references are not recursively traversed, so cyclic modifier graphs
    # cannot recurse indefinitely inside the snapshot serializer.
    cycle_a = cube("cycle_dependency_a", (15, 0, 0))
    cycle_b = cube("cycle_dependency_b", (17, 0, 0))
    for a, b in ((cycle_a, cycle_b), (cycle_b, cycle_a)):
        modifier = a.modifiers.new("cycle_reference", "BOOLEAN")
        modifier.object = b
        modifier.show_viewport = False
    verify("cyclic_id_references_snapshot_stable", qa.compare_snapshot(qa.snapshot([cycle_a.name, cycle_b.name]))["ok"])

    door = cube("door", (4, 4, 1), (1, .04, 2))
    handle = cube("attached_handle", (4.25, 4.12, 1), (.1, .2, .1))
    floating = cube("floating_handle", (4.25, 4.4, 1), (.1, .2, .1))
    bpy.context.view_layer.update()
    world = handle.matrix_world.copy()
    handle.parent = door; handle.matrix_world = world
    bpy.context.view_layer.update()
    verify("attached_contact_passes", qa.check_contact(handle, door)["ok"])
    verify("floating_contact_detected", not qa.check_contact(floating, door)["ok"])
    old = door.matrix_basis.copy()
    verify("attached_motion_passes", qa.check_motion(door, [handle])["ok"])
    verify("unparented_motion_detected", not qa.check_motion(door, [floating])["ok"])
    verify("motion_restores_transform", max(abs(old[i][j]-door.matrix_basis[i][j]) for i in range(4) for j in range(4)) < 1e-7)
    fixed = bpy.data.objects.new("fixed_rotation", None)
    s.collection.objects.link(fixed)
    constraint = door.constraints.new("COPY_ROTATION")
    constraint.target = fixed
    bpy.context.view_layer.update()
    blocked = qa.check_motion(door, [floating])
    verify("constrained_root_reports_blocked", not blocked["ok"] and blocked["status"] == "blocked")
    verify("blocked_motion_restores_transform", max(abs(old[i][j]-door.matrix_basis[i][j]) for i in range(4) for j in range(4)) < 1e-7)
    door.constraints.remove(constraint)
    bpy.context.view_layer.update()
    verify("zero_rotation_is_not_a_pass", qa.check_motion(door, [floating], angle_degrees=0)["status"] == "blocked")
    before_gap = qa.check_contact(floating, door)["gap_m"]
    s.unit_settings.scale_length = .01
    verify("distances_respect_scene_units", abs(qa.check_contact(floating, door)["gap_m"] - before_gap * .01) < 1e-7)
    s.unit_settings.scale_length = 1

    missing = bpy.data.images.new("missing_texture_fixture", width=1, height=1)
    missing.source = "FILE"; missing.filepath = str(out / "nonexistent_texture.png")
    verify("missing_texture_reported", any(r["name"] == missing.name for r in qa.missing_files()["missing"]))
    verify("protected_objects_preserved", qa.compare_snapshot(baseline)["ok"])
    bpy.context.view_layer.update()
    saved_baseline = qa.snapshot()
    qa.write_json(out / "snapshot_before_save.json", saved_baseline)
    qa.write_json(out / "fixture_audit.json", audit)
    bpy.ops.wm.save_as_mainfile(filepath=str(out / "fixtures.blend"))
    result = {"passed": passed, "count": len(passed), "blender": bpy.app.version_string,
              "saved_fixture": str(out / "fixtures.blend"), "rendered": False}
    qa.write_json(out / "self_test.json", result)
    return result
