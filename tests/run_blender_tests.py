"""Background Blender entrypoint for fixtures and their saved-file verification."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

import bpy

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    if not bpy.app.background:
        raise RuntimeError("Tests require a disposable background Blender process")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reopen", action="store_true")
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
    output = args.output.expanduser().resolve()
    if args.reopen:
        expected_file = output / "fixtures.blend"
        if Path(bpy.data.filepath).resolve() != expected_file:
            raise ValueError("Reopen verification requires the saved fixture file")
        qa = load("qa", ROOT / "scripts/scene_checks.py")
        before = json.loads((output / "snapshot_before_save.json").read_text(encoding="utf-8"))
        result = qa.compare_snapshot(before)
        qa.write_json(output / "reopen.json", result)
        if not result["ok"]:
            raise AssertionError(result)
    else:
        result = load("fixtures", ROOT / "scripts/self_test.py").run(output)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
