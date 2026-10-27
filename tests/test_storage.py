"""Exercise the real application identity against filesystem permission boundaries."""

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
            ["s6-setuidgid", "pompey", "python3", storage.__file__, "check", str(folder)],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_probe_preserves_existing_files_and_removes_its_own_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            os.chown(root, 1000, 1000)
            existing = root / "existing.mkv"
            existing.write_bytes(b"existing media")
            result = self.probe(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(list(root.iterdir()), [existing])
            self.assertEqual(existing.read_bytes(), b"existing media")

    def test_root_owned_directory_and_inaccessible_parent_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            child = root / "downloads"
            child.mkdir(mode=0o755)
            root.chmod(0o755)
            result = self.probe(child)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("UID=1000 GID=1000", result.stderr)
            self.assertIn("Permission denied", result.stderr)
            self.assertEqual(list(child.iterdir()), [])
            os.chown(child, 1000, 1000)
            root.chmod(0o700)
            self.assertNotEqual(self.probe(child).returncode, 0)

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
