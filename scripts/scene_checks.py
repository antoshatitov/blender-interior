"""Read-only Blender QA helpers; motion checks run only in disposable background files.

All distances in public results are metres. AABB and BVH findings are candidates,
not instructions to repair. No automatic mesh repair or global scene cleanup.
"""
import hashlib
import json
import math
from pathlib import Path

import bpy
import bmesh
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

FORMAT_VERSION = 1
GEOMETRY_TYPES = {"MESH", "CURVE", "SURFACE", "FONT"}


def write_json(path, data):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def _atom(value, depth=0):
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, bpy.types.ID):
        return {"id_type": value.bl_rna.identifier, "name": value.name_full,
                "library": value.library.filepath if value.library else None}
    if depth > 5:
        return getattr(getattr(value, "bl_rna", None), "identifier", type(value).__name__)
    if hasattr(value, "to_dict"):
        return _atom(value.to_dict(), depth + 1)
    if isinstance(value, dict):
        return {str(k): _atom(v, depth + 1) for k, v in sorted(value.items())}
    if isinstance(value, set):
        return sorted(_atom(v, depth + 1) for v in value)
    if hasattr(value, "bl_rna"):
        return _rna(value, depth=depth + 1)
    try:
        return [_atom(v, depth + 1) for v in value]
    except TypeError:
        return type(value).__name__


def _rna(value, skip=(), depth=0):
    out = {}
    skip = set(skip) | {"rna_type", "id_data"}
    for prop in value.bl_rna.properties:
        key = prop.identifier
        if key in skip or prop.is_hidden:
            continue
        try:
            out[key] = _atom(getattr(value, key), depth + 1)
        except (AttributeError, RuntimeError, TypeError):
            continue
    return out


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _custom(value):
    return {key: _atom(value[key]) for key in sorted(value.keys()) if key != "_RNA_UI"}


def _tree(tree, seen=None):
    if tree is None:
        return None
    seen = set() if seen is None else seen
    if tree.as_pointer() in seen:
        return {"recursive_group": tree.name}
    seen = seen | {tree.as_pointer()}
    nodes = []
    for node in tree.nodes:
        row = {"name": node.name, "type": node.bl_idname,
               "props": _rna(node, skip={"inputs", "outputs", "internal_links", "dimensions",
                                          "location", "width", "height", "parent", "select"}),
               "inputs": [{"identifier": s.identifier, "default": _atom(s.default_value)}
                          for s in node.inputs if hasattr(s, "default_value")]}
        if hasattr(node, "node_tree") and node.node_tree:
            row["group"] = _tree(node.node_tree, seen)
        if hasattr(node, "color_ramp"):
            row["color_ramp"] = {"props": _rna(node.color_ramp, skip={"elements"}),
                                 "elements": [_rna(e) for e in node.color_ramp.elements]}
        if hasattr(node, "image") and node.image:
            row["image_source"] = {"filepath": node.image.filepath, "source": node.image.source,
                                    "packed": bool(node.image.packed_file)}
        nodes.append(row)
    links = sorted((l.from_node.name, l.from_socket.identifier,
                    l.to_node.name, l.to_socket.identifier, l.is_muted) for l in tree.links)
    return {"nodes": sorted(nodes, key=lambda n: n["name"]), "links": links}


def _material(material):
    if material is None:
        return None
    return {"name": material.name_full,
            "props": _rna(material, skip={"node_tree", "preview", "animation_data", "users",
                                           "is_updated", "is_updated_data", "session_uid"}),
            "tree": _tree(material.node_tree), "custom": _custom(material)}


def _data(data):
    if data is None:
        return None
    row = {"type": data.bl_rna.identifier, "name": data.name_full, "custom": _custom(data),
           "library": data.library.filepath if data.library else None}
    if isinstance(data, bpy.types.Mesh):
        row.update(vertices=[tuple(v.co) for v in data.vertices],
                   edges=[tuple(e.vertices) for e in data.edges],
                   faces=[(tuple(f.vertices), f.material_index, f.use_smooth) for f in data.polygons],
                   uv=[(l.name, [tuple(p.uv) for p in l.data]) for l in data.uv_layers],
                   attributes=[(a.name, a.domain, a.data_type, [_rna(v) for v in a.data])
                               for a in data.attributes if not a.name.startswith(".select")])
        if data.shape_keys:
            row["shape_keys"] = [(k.name, k.value, k.slider_min, k.slider_max,
                                  [tuple(p.co) for p in k.data]) for k in data.shape_keys.key_blocks]
    elif isinstance(data, bpy.types.Curve):
        row["props"] = _rna(data, skip={"splines", "materials", "animation_data", "users",
                                        "is_updated", "is_updated_data", "session_uid"})
        row["splines"] = [{"props": _rna(s, skip={"points", "bezier_points"}),
                            "points": [_rna(p) for p in s.points],
                            "bezier_points": [_rna(p) for p in s.bezier_points]}
                           for s in data.splines]
    else:
        row["props"] = _rna(data, skip={"materials", "animation_data", "users", "preview",
                                        "is_updated", "is_updated_data", "session_uid"})
    return row


def _collection_paths(scene):
    paths = {}

    def visit(collection, parents):
        state = {"name": collection.name, "hide_viewport": collection.hide_viewport,
                 "hide_render": collection.hide_render, "hide_select": collection.hide_select}
        path = parents + [state]
        paths.setdefault(collection.name, []).append(path)
        for child in collection.children:
            visit(child, path)
    visit(scene.collection, [])
    layer_flags = {}
    for layer in scene.view_layers:
        def walk(lc, ancestors):
            state = {"name": lc.name, "exclude": lc.exclude,
                     "hide_viewport": lc.hide_viewport, "holdout": lc.holdout,
                     "indirect_only": lc.indirect_only}
            path = ancestors + [state]
            layer_flags.setdefault(lc.collection.name, {}).setdefault(layer.name, []).append(path)
            for child in lc.children:
                walk(child, path)
        walk(layer.layer_collection, [])
    return paths, layer_flags


def snapshot(names=None):
    """Hash source datablocks, shader graphs and protected object state without mutation."""
    scene = bpy.context.scene
    selected = list(scene.objects) if names is None else [scene.objects[n] for n in names]
    paths, layers = _collection_paths(scene)
    data_cache, material_cache, objects = {}, {}, {}
    for obj in selected:
        if obj.data:
            key = obj.data.as_pointer()
            if key not in data_cache:
                data_cache[key] = _hash(_data(obj.data))
            data_hash = data_cache[key]
        else:
            data_hash = None
        materials = []
        for slot in obj.material_slots:
            mat = slot.material
            if mat and mat.as_pointer() not in material_cache:
                material_cache[mat.as_pointer()] = _hash(_material(mat))
            materials.append((slot.link, mat.name_full if mat else None,
                              material_cache[mat.as_pointer()] if mat else None))
        state = {"type": obj.type, "matrix_world": _atom(obj.matrix_world),
                 "matrix_basis": _atom(obj.matrix_basis), "parent": _atom(obj.parent),
                 "parent_type": obj.parent_type, "parent_bone": obj.parent_bone,
                 "parent_inverse": _atom(obj.matrix_parent_inverse), "data": data_hash,
                 "materials": materials, "custom": _custom(obj),
                 "visibility": {k: getattr(obj, k) for k in
                                ("hide_viewport", "hide_render", "hide_select", "display_type")},
                 "hidden_in_view_layers": {layer.name: obj.hide_get(view_layer=layer)
                                           for layer in scene.view_layers if obj.name in layer.objects},
                 "modifiers": [_rna(m) for m in obj.modifiers],
                 "constraints": [_rna(c) for c in obj.constraints],
                 "collections": [(c.name, paths.get(c.name), layers.get(c.name))
                                 for c in sorted(obj.users_collection, key=lambda c: c.name)]}
        objects[obj.name] = {"hash": _hash(state),
                             "components": {k: _hash(v) for k, v in state.items()}}
    return {"format_version": FORMAT_VERSION, "scene": scene.name,
            "units": {"system": scene.unit_settings.system,
                      "scale_length": scene.unit_settings.scale_length}, "objects": objects}


def compare_snapshot(before):
    if before.get("format_version") != FORMAT_VERSION:
        raise ValueError("Unsupported snapshot format")
    names = before["objects"]
    existing = [n for n in names if n in bpy.context.scene.objects]
    now = snapshot(existing)
    changed = []
    for name in existing:
        if now["objects"][name]["hash"] != names[name]["hash"]:
            old = names[name]["components"]
            changed.append({"object": name, "components": [k for k, v in
                            now["objects"][name]["components"].items() if old.get(k) != v]})
    missing = sorted(set(names) - set(existing))
    units_changed = now["units"] != before["units"]
    scene_changed = now["scene"] != before["scene"]
    return {"ok": not changed and not missing and not units_changed and not scene_changed,
            "checked": len(existing), "changed": changed, "missing": missing,
            "units_changed": units_changed, "scene_changed": scene_changed}


def _metres():
    return bpy.context.scene.unit_settings.scale_length or 1.0


def _object(value):
    return bpy.context.scene.objects[value] if isinstance(value, str) else value


def _geometry(value):
    obj = _object(value)
    if obj.type not in GEOMETRY_TYPES:
        raise ValueError(f"Object has no supported surface: {obj.name}")
    ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = ev.to_mesh()
    if mesh is None:
        raise ValueError(f"Cannot evaluate mesh: {obj.name}")
    try:
        mesh.calc_loop_triangles()
        vertices = [ev.matrix_world @ v.co for v in mesh.vertices]
        faces = [tuple(p.vertices) for p in mesh.polygons]
        triangles = [tuple(t.vertices) for t in mesh.loop_triangles]
    finally:
        ev.to_mesh_clear()
    return obj, vertices, faces, triangles


def _bounds(obj):
    ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    points = [ev.matrix_world @ Vector(c) for c in ev.bound_box]
    return Vector([min(p[i] for p in points) for i in range(3)]), \
        Vector([max(p[i] for p in points) for i in range(3)])


def audit_scene(names=None, epsilon_m=1e-6):
    if epsilon_m <= 0:
        raise ValueError("epsilon_m must be positive")
    objects = list(bpy.context.scene.objects) if names is None else [_object(n) for n in names]
    rows, errors = [], []
    scale = _metres()
    for obj in objects:
        if obj.type not in GEOMETRY_TYPES:
            continue
        bm = bmesh.new()
        try:
            ev = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
            mesh = ev.to_mesh()
            if mesh is None:
                raise ValueError("Cannot evaluate mesh")
            try:
                bm.from_mesh(mesh)
                bm.transform(ev.matrix_world)
            finally:
                ev.to_mesh_clear()
            vertices = [v.co.copy() for v in bm.verts]
            faces = list(bm.faces)
            bm.normal_update()
            boundary = sum(e.is_boundary for e in bm.edges)
            wire = sum(e.is_wire for e in bm.edges)
            complex_edges = sum(len(e.link_faces) > 2 for e in bm.edges)
            degenerate = sum(f.calc_area() * scale * scale <= epsilon_m ** 2 for f in bm.faces)
            zero_edges = sum(e.calc_length() * scale <= epsilon_m for e in bm.edges)
            non_contiguous = sum(e.is_manifold and not e.is_contiguous for e in bm.edges)
            kd = KDTree(len(vertices))
            for i, p in enumerate(vertices):
                kd.insert(p, i)
            kd.balance()
            coincident = sum(1 for i, p in enumerate(vertices)
                             for _, j, _ in kd.find_range(p, epsilon_m / scale) if j > i)
            closed = bool(faces) and not (boundary or wire or complex_edges or non_contiguous)
            volume = bm.calc_volume(signed=True) * scale ** 3 if closed else None
            expected = obj.get("bi_surface", "unknown")
            confirmed = []
            if degenerate:
                confirmed.append("degenerate_faces")
            if wire:
                confirmed.append("wire_edges")
            if expected == "solid" and (boundary or complex_edges):
                confirmed.append("solid_surface_not_closed")
            candidates = []
            if boundary and expected != "solid" and expected != "open":
                candidates.append("boundary_edges_need_role")
            if complex_edges or non_contiguous:
                candidates.append("non_manifold_or_winding")
            if coincident or zero_edges:
                candidates.append("coincident_vertices_or_short_edges")
            if volume is not None and volume < 0:
                candidates.append("negative_signed_volume")
            rows.append({"object": obj.name, "expected_surface": expected,
                         "vertices": len(vertices), "faces": len(faces),
                         "boundary_edges": boundary, "wire_edges": wire,
                         "edges_over_two_faces": complex_edges, "inconsistent_winding_edges": non_contiguous,
                         "degenerate_faces": degenerate, "short_edges": zero_edges,
                         "coincident_vertex_pairs": coincident, "signed_volume_m3": volume,
                         "confirmed": confirmed, "candidates": candidates})
        except Exception as exc:
            errors.append({"object": obj.name, "error": f"{type(exc).__name__}: {exc}"})
        finally:
            bm.free()
    return {"format_version": FORMAT_VERSION, "checked": len(rows), "rows": rows,
            "errors": errors, "confirmed_objects": sum(bool(r["confirmed"]) for r in rows),
            "candidate_objects": sum(bool(r["candidates"]) for r in rows),
            "visual_qa": "not_performed_by_script"}


def pair_candidates(names=None, max_pairs=2000, epsilon_m=1e-6):
    if max_pairs < 1:
        raise ValueError("max_pairs must be positive")
    objects = list(bpy.context.scene.objects) if names is None else [_object(n) for n in names]
    rows = sorted([(o.name, *_bounds(o)) for o in objects if o.type in GEOMETRY_TYPES],
                  key=lambda r: r[1].x)
    epsilon = epsilon_m / _metres()
    pairs = []
    for i, (name, lo, hi) in enumerate(rows):
        for other, a, b in rows[i + 1:]:
            if a.x > hi.x + epsilon:
                break
            if all(lo[k] <= b[k] + epsilon and a[k] <= hi[k] + epsilon for k in (1, 2)):
                if len(pairs) == max_pairs:
                    return {"pairs": pairs, "truncated": True, "broad_phase_only": True}
                pairs.append([name, other])
    return {"pairs": pairs, "truncated": False, "broad_phase_only": True}


def _cross2(a, b):
    return a[0] * b[1] - a[1] * b[0]


def _projected_overlap_area(first, second, normal):
    axis = max(range(3), key=lambda i: abs(normal[i]))
    keep = [i for i in range(3) if i != axis]
    poly = [Vector((p[keep[0]], p[keep[1]])) for p in first]
    clip = [Vector((p[keep[0]], p[keep[1]])) for p in second]
    if _cross2(clip[1] - clip[0], clip[2] - clip[0]) < 0:
        clip.reverse()
    for i in range(3):
        edge_a, edge_b = clip[i], clip[(i + 1) % 3]
        out = []
        if not poly:
            return 0.0
        for j, current in enumerate(poly):
            previous = poly[j - 1]
            dc = _cross2(edge_b - edge_a, current - edge_a)
            dp = _cross2(edge_b - edge_a, previous - edge_a)
            if (dc >= 0) != (dp >= 0):
                out.append(previous + (current - previous) * (dp / (dp - dc)))
            if dc >= 0:
                out.append(current)
        poly = out
    area = abs(sum(_cross2(poly[i], poly[(i + 1) % len(poly)])
                   for i in range(len(poly)))) / 2 if poly else 0.0
    return area / abs(normal[axis])


def inspect_pair(a, b, epsilon_m=1e-6, max_triangle_pairs=10000):
    if epsilon_m <= 0 or max_triangle_pairs < 1:
        raise ValueError("Positive tolerance and triangle budget required")
    _, va, _, ta = _geometry(a)
    _, vb, _, tb = _geometry(b)
    scale = _metres()
    epsilon = epsilon_m / scale
    if not ta or not tb:
        return {"a": _object(a).name, "b": _object(b).name, "error": "empty_surface"}
    ba = BVHTree.FromPolygons(va, ta, all_triangles=True, epsilon=epsilon)
    bb = BVHTree.FromPolygons(vb, tb, all_triangles=True, epsilon=epsilon)
    hits = set(ba.overlap(bb))
    # Blender BVH can omit exactly coplanar triangles. Add triangle AABB
    # candidates and verify projected overlap rather than relying on BVH alone.
    candidate_pairs = set(sorted(hits)[:max_triangle_pairs])
    truncated = len(hits) > max_triangle_pairs
    bounds_b = []
    for j, tri in enumerate(tb):
        vv = [vb[k] for k in tri]
        bounds_b.append((j, [min(v[k] for v in vv) for k in range(3)],
                         [max(v[k] for v in vv) for k in range(3)]))
    bounds_b.sort(key=lambda row: row[1][0])
    for i, tri in enumerate(ta):
        vv = [va[k] for k in tri]
        lo = [min(v[k] for v in vv) for k in range(3)]
        hi = [max(v[k] for v in vv) for k in range(3)]
        for j, low, high in bounds_b:
            if low[0] > hi[0] + epsilon:
                break
            if any(high[k] < lo[k] - epsilon or low[k] > hi[k] + epsilon for k in range(3)):
                continue
            if (i, j) not in candidate_pairs:
                if len(candidate_pairs) == max_triangle_pairs:
                    truncated = True
                    break
                candidate_pairs.add((i, j))
        if truncated:
            break
    coplanar, crossing, touching, area = 0, 0, 0, 0.0
    for i, j in sorted(candidate_pairs):
        first, second = [va[k] for k in ta[i]], [vb[k] for k in tb[j]]
        na = (first[1] - first[0]).cross(first[2] - first[0])
        nb = (second[1] - second[0]).cross(second[2] - second[0])
        if na.length <= epsilon ** 2 or nb.length <= epsilon ** 2:
            continue
        na.normalize(); nb.normalize()
        da = [(v - first[0]).dot(na) for v in second]
        db = [(v - second[0]).dot(nb) for v in first]
        if abs(na.dot(nb)) > 1 - 1e-6 and max(abs(d) for d in da) <= epsilon:
            overlap = _projected_overlap_area(first, second, na) * scale ** 2
            if overlap > epsilon_m ** 2:
                coplanar += 1
                area += overlap
        elif (i, j) in hits and min(da) < -epsilon and max(da) > epsilon and min(db) < -epsilon and max(db) > epsilon:
            crossing += 1
        elif (i, j) in hits:
            touching += 1
    return {"a": _object(a).name, "b": _object(b).name,
            "coplanar_overlap_triangle_pairs": coplanar, "coplanar_overlap_area_m2": area,
            "crossing_surface_candidates": crossing, "touching_triangle_pairs": touching,
            "truncated": truncated,
            "containment_checked": False, "interpretation": "needs_assembly_and_visual_confirmation"}


def check_contact(a, b, max_gap_m=0.001, anchor_a_world=None):
    if max_gap_m < 0:
        raise ValueError("max_gap_m must be nonnegative")
    oa, va, _, ta = _geometry(a)
    ob, vb, _, tb = _geometry(b)
    if not ta or not tb:
        return {"a": oa.name, "b": ob.name, "ok": False, "error": "empty_surface"}
    ba = BVHTree.FromPolygons(va, ta, all_triangles=True)
    bb = BVHTree.FromPolygons(vb, tb, all_triangles=True)
    if anchor_a_world is not None:
        anchor = Vector(anchor_a_world)
        on_a = ba.find_nearest(anchor)[3] * _metres()
        if on_a > max(max_gap_m, 1e-6):
            raise ValueError("Contact anchor must lie on the first object's expected surface")
        distance = bb.find_nearest(anchor)[3] * _metres()
        method = "specified_anchor_to_surface"
    else:
        distance = min([bb.find_nearest(v)[3] for v in va] +
                       [ba.find_nearest(v)[3] for v in vb]) * _metres()
        method = "vertex_surface_upper_bound"
    return {"a": oa.name, "b": ob.name, "gap_m": distance, "max_gap_m": max_gap_m,
            "ok": distance <= max_gap_m, "method": method,
            "note": "Verify the expected joint, not an unrelated contact elsewhere"}


def check_motion(root, members, axis="Z", angle_degrees=45, tolerance=1e-5):
    if not bpy.app.background:
        raise RuntimeError("Motion tests require a disposable background copy")
    root = _object(root)
    members = [_object(n) for n in members]
    if root in members or not members:
        raise ValueError("List the expected moving members, excluding the root")
    old = root.matrix_basis.copy()
    relative = {o.name: root.matrix_world.inverted() @ o.matrix_world for o in members}
    errors = []
    try:
        root.matrix_basis = old @ Matrix.Rotation(math.radians(angle_degrees), 4, axis)
        bpy.context.view_layer.update()
        for obj in members:
            expected = root.matrix_world @ relative[obj.name]
            error = max(abs(expected[i][j] - obj.matrix_world[i][j]) for i in range(4) for j in range(4))
            errors.append({"object": obj.name, "transform_error": error})
    finally:
        root.matrix_basis = old
        bpy.context.view_layer.update()
    return {"root": root.name, "members": errors, "ok": all(e["transform_error"] <= tolerance for e in errors),
            "collision_sweep_checked": False}


def missing_files():
    missing = []
    unchecked = []
    for image in bpy.data.images:
        if image.source not in {"FILE", "MOVIE"}:
            if image.source in {"TILED", "SEQUENCE"}:
                unchecked.append({"type": "image", "name": image.name, "source": image.source})
            continue
        if image.packed_file or getattr(image, "packed_files", None):
            continue
        if image.filepath:
            path = bpy.path.abspath(image.filepath, library=image.library)
            if not Path(path).is_file():
                missing.append({"type": "image", "name": image.name, "path": path})
    for library in bpy.data.libraries:
        path = bpy.path.abspath(library.filepath)
        if not Path(path).is_file():
            missing.append({"type": "library", "name": library.name, "path": path})
    return {"missing": missing, "unchecked": unchecked,
            "scope": "images_and_linked_libraries; caches and other file-backed nodes need separate checks"}
