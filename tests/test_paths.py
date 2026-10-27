from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from aula_f87 import paths


class ProfilePathTests(unittest.TestCase):
    def test_profile_dir_uses_xdg_config_home(self) -> None:
        result = paths.default_profile_dir(
            {"XDG_CONFIG_HOME": "/tmp/example-config"},
            home=Path("/home/example"),
        )
        self.assertEqual(result, Path("/tmp/example-config/aula-f87-rgb"))

    def test_profile_dir_falls_back_to_home_config(self) -> None:
        result = paths.default_profile_dir({}, home=Path("/home/example"))
        self.assertEqual(result, Path("/home/example/.config/aula-f87-rgb"))

    def test_relative_xdg_config_home_is_ignored(self) -> None:
        result = paths.default_profile_dir(
            {"XDG_CONFIG_HOME": "relative/config"},
            home=Path("/home/example"),
        )
        self.assertEqual(result, Path("/home/example/.config/aula-f87-rgb"))

    def test_profile_dir_override_wins(self) -> None:
        result = paths.default_profile_dir(
            {
                "XDG_CONFIG_HOME": "/tmp/example-config",
                "AULA_F87_PROFILE_DIR": "/tmp/custom-profiles",
            },
            home=Path("/home/example"),
        )
        self.assertEqual(result, Path("/tmp/custom-profiles"))

    def test_legacy_profile_dir(self) -> None:
        self.assertEqual(
            paths.legacy_profile_dir(home=Path("/home/example")),
            Path("/home/example/aula-f87/profiles"),
        )


class ProfileMigrationTests(unittest.TestCase):
    def test_legacy_profiles_are_copied_once_without_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "config" / "aula-f87-rgb"
            legacy = root / "home" / "aula-f87" / "profiles"
            legacy.mkdir(parents=True)
            (legacy / "normal.json").write_text('{"name": "old"}', encoding="utf-8")
            (legacy / "extra.json").write_text('{"name": "extra"}', encoding="utf-8")
            target.mkdir(parents=True)
            (target / "normal.json").write_text('{"name": "new"}', encoding="utf-8")

            copied = paths.migrate_legacy_profiles(target, legacy)

            self.assertEqual(copied, [target / "extra.json"])
            self.assertEqual(
                (target / "normal.json").read_text(encoding="utf-8"),
                '{"name": "new"}',
            )
            self.assertEqual(
                (target / "extra.json").read_text(encoding="utf-8"),
                '{"name": "extra"}',
            )
            self.assertTrue((legacy / "normal.json").exists())
            self.assertTrue((legacy / "extra.json").exists())
            self.assertTrue((target / paths.MIGRATION_MARKER).exists())

            (legacy / "later.json").write_text('{"name": "later"}', encoding="utf-8")
            self.assertEqual(paths.migrate_legacy_profiles(target, legacy), [])
            self.assertFalse((target / "later.json").exists())

    def test_migration_creates_the_new_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "config" / "aula-f87-rgb"
            legacy = root / "home" / "aula-f87" / "profiles"

            self.assertEqual(paths.migrate_legacy_profiles(target, legacy), [])
            self.assertTrue(target.is_dir())
            self.assertTrue((target / paths.MIGRATION_MARKER).exists())


if __name__ == "__main__":
    unittest.main()
