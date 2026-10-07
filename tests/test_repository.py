"""Repository tooling tests; no Blender or third-party packages required."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


installer = load("installer", ROOT / "tools/install_skill.py")
indexer = load("indexer", ROOT / "scripts/reference_index.py")


class ReferenceIndexTests(unittest.TestCase):
    def test_recursive_duplicates_and_hidden_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "room").mkdir()
            (root / "first.PNG").write_bytes(b"same")
            (root / "room/second.jpg").write_bytes(b"same")
            (root / ".hidden.png").write_bytes(b"hidden")
            (root / "notes.pdf").write_bytes(b"pdf")
            result = indexer.index_folder(root)
            self.assertEqual(len(result["images"]), 2)
            self.assertEqual(result["unique_images"], 1)
            self.assertEqual(len(result["exact_duplicates"]), 1)
            self.assertEqual(len(result["unsupported_files"]), 1)

    def test_missing_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                indexer.index_folder(Path(tmp) / "absent")


class InstallTests(unittest.TestCase):
    def test_reject_payload_root_symlinks_before_copying(self):
        for entry in installer.PAYLOAD:
            with self.subTest(entry=entry), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = root / "repo"
                source.mkdir()
                (source / "SKILL.md").write_text("skill")
                outside = root / "external"
                outside.mkdir()
                (outside / "private.txt").write_text("must not be copied")
                link = source / entry
                if link.exists():
                    link.unlink()
                link.symlink_to(outside, target_is_directory=True)
                target = root / "codex/skills/blender-interior"
                target.mkdir(parents=True)
                (target / "SKILL.md").write_text("existing installation")
                with self.assertRaises(ValueError):
                    installer.payload_files(source)
                with self.assertRaises(ValueError):
                    installer.install(source, target)
                self.assertEqual((target / "SKILL.md").read_text(), "existing installation")
                self.assertFalse((target / entry / "private.txt").exists())
                self.assertFalse((root / "codex/skill-backups").exists())

    def test_install_update_backup_and_repeat(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "repo"
            source.mkdir()
            (source / "SKILL.md").write_text("version one")
            (source / "README.md").write_text("not installed")
            (source / "scripts/__pycache__").mkdir(parents=True)
            (source / "scripts/helper.py").write_text("pass\n")
            (source / "scripts/__pycache__/helper.pyc").write_bytes(b"ignored")
            target = root / "codex/skills/blender-interior"
            self.assertTrue(installer.install(source, target)["changed"])
            self.assertTrue(installer.check(source, target)["ok"])
            self.assertFalse((target / "README.md").exists())
            self.assertFalse((target / "scripts/__pycache__").exists())
            self.assertFalse(installer.install(source, target)["changed"])
            (source / "SKILL.md").write_text("version two")
            self.assertFalse(installer.check(source, target)["ok"])
            result = installer.install(source, target)
            self.assertEqual((Path(result["backup"]) / "SKILL.md").read_text(), "version one")
            self.assertEqual((target / "SKILL.md").read_text(), "version two")
            self.assertTrue(installer.check(source, target)["ok"])
            (target / "stale.txt").write_text("stale")
            self.assertEqual(installer.check(source, target)["extra"], ["stale.txt"])

    def test_reject_source_and_symlink_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "SKILL.md").write_text("skill")
            with self.assertRaises(ValueError):
                installer.install(root, root)
            target = root.parent / (root.name + "-link")
            target.symlink_to(root, target_is_directory=True)
            try:
                with self.assertRaises(ValueError):
                    installer.install(root, target)
            finally:
                target.unlink()


if __name__ == "__main__":
    unittest.main()
