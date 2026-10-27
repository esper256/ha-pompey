"""Exercise root application access and safe storage failure diagnostics."""

from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

sys.path.insert(0, "/usr/local/bin")
import pompey_storage as storage


class StorageAccess(unittest.TestCase):
    def probe(self, folder):
        return subprocess.run(
            ["python3", storage.__file__, "check", str(folder)],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_probe_preserves_existing_files_and_removes_its_own_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertEqual(os.getuid(), 0)
            root.chmod(0o755)
            existing = root / "existing.mkv"
            existing.write_bytes(b"existing media")
            # An earlier Pompey installation may leave files owned by UID 1000.
            os.chown(existing, 1000, 1000)
            existing.chmod(0o600)
            result = self.probe(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(list(root.iterdir()), [existing])
            self.assertEqual(existing.read_bytes(), b"existing media")

    def test_read_only_storage_still_fails_with_application_identity(self):
        # Docker/Podman expose sysfs read-only in the isolated checks container.
        result = self.probe("/sys")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("UID=0 GID=0", result.stderr)
        self.assertIn("Read-only file system", result.stderr)

    def test_changed_mount_is_rejected_before_any_write_probe(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            ready = Path(tmp)
            (ready / "storage-baseline.json").write_text(json.dumps({"/media/share": {"filesystem": "cifs"}}))
            with patch.dict(os.environ, POMPEY_READY=tmp), patch.object(storage, "mount_identity", return_value={"filesystem": "overlay"}), patch.object(storage, "check") as probe:
                result = storage.report()
                self.assertFalse(result["ok"])
                self.assertEqual(result["path"], "/media/share")
                probe.assert_not_called()

    def test_mount_report_excludes_credentials_and_shows_parent_modes(self):
        mountinfo = (
            "10 1 0:5 / / rw - overlay overlay rw\n"
            "11 10 0:6 / /media/network\\040share rw - cifs //private-server/share "
            "rw,uid=0,gid=0,dir_mode=0755,file_mode=0755,username=private-user,"
            "password=secret-password,credentials=/secret/path\n"
        )
        output = StringIO()
        with (
            patch.object(Path, "read_text", return_value=mountinfo),
            redirect_stdout(output),
        ):
            storage.describe("/media/network share/downloads")
        report = output.getvalue()
        self.assertIn("Filesystem: cifs", report)
        self.assertIn("uid=0", report)
        self.assertIn("Directory '/'", report)
        self.assertIn("mode=", report)
        for value in ["private-server", "private-user", "secret-password", "/secret/path"]:
            self.assertNotIn(value, report)


if __name__ == "__main__":
    unittest.main()
