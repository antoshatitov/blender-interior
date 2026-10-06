"""Install the skill payload from this checkout, preserving the previous installation."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid

PAYLOAD = ("SKILL.md", "agents", "references", "scripts", "assets")


def payload_files(source):
    source = Path(source).resolve()
    if not (source / "SKILL.md").is_file():
        raise ValueError("Source does not contain SKILL.md")
    files = []
    for name in PAYLOAD:
        entry = source / name
        candidates = [entry] if entry.is_file() else entry.rglob("*") if entry.is_dir() else []
        for path in candidates:
            relative = path.relative_to(source)
            if "__pycache__" in relative.parts or path.suffix in {".pyc", ".pyo"}:
                continue
            if path.is_symlink():
                raise ValueError(f"Symlinks are not supported in the payload: {relative}")
            if path.is_file():
                files.append(relative)
    return sorted(files)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(source, target):
    source, target = Path(source).resolve(), Path(target)
    files = payload_files(source)
    different = [str(p) for p in files if not (target / p).is_file()
                 or digest(source / p) != digest(target / p)]
    expected = set(files)
    extra = sorted(str(p.relative_to(target)) for p in target.rglob("*")
                   if p.is_file() and p.relative_to(target) not in expected
                   and "__pycache__" not in p.relative_to(target).parts
                   and p.suffix not in {".pyc", ".pyo"}) if target.is_dir() else []
    return {"ok": not different and not extra, "target": str(target),
            "files": len(files), "different": different, "extra": extra}


def install(source, target):
    source = Path(source).resolve()
    target = Path(target).expanduser().absolute()
    if target.is_symlink():
        raise ValueError("Installation target is a symlink; refusing to replace it")
    resolved_target = target.resolve()
    if resolved_target == source or source in resolved_target.parents or resolved_target in source.parents:
        raise ValueError("Source and installation target must be separate directories")
    if target.exists() and not target.is_dir():
        raise ValueError("Installation target is not a directory")
    files = payload_files(source)
    if check(source, target)["ok"]:
        return {"installed": str(target), "changed": False, "files": len(files), "backup": None}
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = None
    with tempfile.TemporaryDirectory(prefix=".blender-interior-install-", dir=target.parent) as temp:
        stage = Path(temp) / "payload"
        stage.mkdir()
        for relative in files:
            output = stage / relative
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / relative, output)
        assert check(source, stage)["ok"], "Staged files differ from source"
        if target.exists():
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = target.parent.parent / "skill-backups" / target.name / (stamp + "-" + uuid.uuid4().hex[:8])
            backup.parent.mkdir(parents=True, exist_ok=True)
            target.rename(backup)
        try:
            stage.rename(target)
        except BaseException:
            if backup is not None:
                backup.rename(target)
            raise
    return {"installed": str(target), "changed": True, "files": len(files),
            "backup": str(backup) if backup else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    codex_root = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    parser.add_argument("--target", type=Path, default=codex_root / "skills" / "blender-interior")
    parser.add_argument("--check", action="store_true", help="Compare files without changing the installation")
    args = parser.parse_args()
    source = Path(__file__).resolve().parents[1]
    target = args.target.expanduser().absolute()
    result = check(source, target) if args.check else install(source, target)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.check and not result["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
