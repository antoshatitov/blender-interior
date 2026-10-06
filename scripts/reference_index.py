"""Inventory local reference images without changing or interpreting sources."""
import argparse
import hashlib
import json
from pathlib import Path

SUPPORTED = {".png", ".jpg", ".jpeg", ".webp"}


def index_folder(folder):
    root = Path(folder).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"Reference folder does not exist: {root}")
    images, unsupported, groups = [], [], {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(p.startswith(".") for p in path.relative_to(root).parts):
            continue
        if path.suffix.lower() not in SUPPORTED:
            unsupported.append(str(path))
            continue
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        sha = digest.hexdigest()
        images.append({"path": str(path), "relative_path": str(path.relative_to(root)),
                       "bytes": path.stat().st_size, "sha256": sha})
        groups.setdefault(sha, []).append(str(path))
    return {"folder": str(root), "images": images,
            "exact_duplicates": [g for g in groups.values() if len(g) > 1],
            "unique_images": len(groups), "unsupported_files": unsupported}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = index_folder(args.folder)
    output = Path(args.output).expanduser().resolve()
    if output.exists() or output == Path(args.folder).expanduser().resolve():
        raise ValueError("Output must be a new JSON file; existing files are not overwritten")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"images": len(result["images"]), "unique_images": result["unique_images"],
                      "output": str(output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
