import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.runtime_config import RuntimeConfig, xdg_home
from tools.migrate_runtime_state import (
    MigrationError,
    apply_migration,
    dry_run,
    inspect_paths,
    rollback_migration,
    verify_migration,
)


class RuntimeConfigTests(unittest.TestCase):
    def test_xdg_home_uses_absolute_override_or_default(self):
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(xdg_home("XDG_CONFIG_HOME", ".config", {"XDG_CONFIG_HOME": temp}), Path(temp))
            self.assertEqual(xdg_home("XDG_CONFIG_HOME", ".config", {"XDG_CONFIG_HOME": "relative"}), Path.home() / ".config")

    def test_default_root_is_xdg_state(self):
        with tempfile.TemporaryDirectory() as temp:
            controlcenter = Path(temp) / "ControlCenter"
            config = RuntimeConfig.from_env({}, controlcenter_root=controlcenter)

            self.assertEqual(
                config.state_root,
                (Path.home() / ".local/state/controlcenter/dashboard-state").resolve(),
            )
            self.assertEqual(
                config.database_path,
                config.state_root / "db" / "fleet2.sqlite3",
            )

    def test_xdg_state_home_override_and_relative_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            override = str(Path(temp) / "custom-state")
            config = RuntimeConfig.from_env({"XDG_STATE_HOME": override})
            self.assertEqual(config.state_root, (Path(override) / "controlcenter/dashboard-state").resolve())
            fallback = RuntimeConfig.from_env({"XDG_STATE_HOME": "relative"})
            self.assertEqual(fallback.state_root, (Path.home() / ".local/state/controlcenter/dashboard-state").resolve())

    def test_env_override_must_not_point_into_synced_controlcenter(self):
        with tempfile.TemporaryDirectory() as temp:
            controlcenter = Path(temp) / "ControlCenter"
            safe_root = Path(temp) / "host-local-dashboard"
            config = RuntimeConfig.from_env(
                {"CONTROLCENTER_DASHBOARD_STATE_DIR": str(safe_root)},
                controlcenter_root=controlcenter,
            )
            self.assertEqual(config.state_root, safe_root.resolve())

            with self.assertRaises(ValueError):
                RuntimeConfig.from_env(
                    {
                        "CONTROLCENTER_DASHBOARD_STATE_DIR": str(
                            controlcenter / "dashboard" / "data"
                        )
                    },
                    controlcenter_root=controlcenter,
                )

    def test_runtime_directories_are_private_and_retention_is_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            config = RuntimeConfig.from_env(
                {"CONTROLCENTER_DASHBOARD_STATE_DIR": str(Path(temp) / "state")},
                controlcenter_root=Path(temp) / "ControlCenter",
            )

            config.ensure_directories()

            for directory in config.directories:
                self.assertTrue(directory.is_dir())
                if os.name == "posix":
                    self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
            self.assertEqual(config.sample_retention_days, 14)
            self.assertEqual(config.receipt_retention_days, 90)


class SQLitePlumbingTests(unittest.TestCase):
    def test_inspect_reports_metadata_without_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "legacy.db"
            destination = root / "state" / "telemetry.db"
            source.write_bytes(b"offline sqlite fixture")

            report = inspect_paths(source, destination)

            self.assertEqual(report["status"], "observed")
            self.assertEqual(report["source_size_bytes"], 22)
            self.assertFalse(report["destination_exists"])
            self.assertFalse(destination.exists())

    def test_dry_run_does_not_create_or_change_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "legacy.db"
            destination = root / "state" / "db" / "telemetry.db"
            source.write_bytes(b"offline sqlite fixture")

            report = dry_run(source, destination)

            self.assertEqual(report["status"], "ready")
            self.assertFalse(destination.exists())
            self.assertEqual(source.read_bytes(), b"offline sqlite fixture")

    def test_apply_verify_and_rollback_preserve_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "legacy.db"
            destination = root / "state" / "db" / "telemetry.db"
            source.write_bytes(b"offline sqlite fixture")

            applied = apply_migration(source, destination)
            verified = verify_migration(source, destination)
            rolled_back = rollback_migration(source, destination)

            self.assertEqual(applied["status"], "applied")
            self.assertEqual(verified["status"], "verified")
            self.assertEqual(rolled_back["status"], "rolled_back")
            self.assertTrue(source.exists())
            self.assertFalse(destination.exists())

    def test_apply_refuses_destination_collision(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "legacy.db"
            destination = root / "state" / "telemetry.db"
            source.write_bytes(b"source")
            destination.parent.mkdir()
            destination.write_bytes(b"existing")

            with self.assertRaises(MigrationError):
                apply_migration(source, destination)

            self.assertEqual(destination.read_bytes(), b"existing")

    def test_rollback_refuses_source_as_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "telemetry.db"
            source.write_bytes(b"source")

            with self.assertRaises(MigrationError):
                rollback_migration(source, source)

            self.assertTrue(source.exists())


if __name__ == "__main__":
    unittest.main()
