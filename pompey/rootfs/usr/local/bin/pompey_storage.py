#!/usr/bin/env python3
"""Probe storage as the application user and report safe permission diagnostics."""

import os
import hashlib
import json
from pathlib import Path
import re
import stat
import sys
import tempfile
from pompey_runtime import atomic_json

REMOTE_FILESYSTEMS = {"cifs", "nfs", "nfs4", "fuse.sshfs"}


def check(folder):
    # access(W_OK) alone cannot establish whether a remote server permits I/O.
    with os.scandir(folder) as entries:
        next(entries, None)
    with tempfile.TemporaryDirectory(prefix=".pompey-write-test-", dir=folder) as tmp:
        source = Path(tmp) / "create"
        source.write_bytes(b"Pompey storage check\n")
        target = source.with_name("renamed")
        source.rename(target)
        target.unlink()


def unescape_mount(value):
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), value)


def mount_identity(folder):
    path = Path(folder).absolute()
    selected = None
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        before, after = line.split(" - ", 1)
        fields, details = before.split(), after.split()
        mountpoint = Path(unescape_mount(fields[4]))
        if path == mountpoint or mountpoint in path.parents:
            if selected is None or len(mountpoint.parts) >= len(Path(selected["mountpoint"]).parts):
                selected = {"mountpoint": str(mountpoint), "filesystem": details[0],
                            "source": hashlib.sha256(details[1].encode()).hexdigest()}
    if selected is None:
        raise OSError("Storage mount could not be identified")
    return selected


def storage_paths():
    import pompey_common as api
    return [api.media_root() + "/downloads", api.movies_dir(), api.movies_kid_dir(), api.tv_dir(), api.tv_kid_dir()]


def expected_remote():
    path = Path(os.environ.get("POMPEY_DATA", "/data/pompey")) / "storage-mounts.json"
    return path, json.loads(path.read_text()) if path.exists() else {}


def validate_expected():
    _, expected = expected_remote()
    for folder in storage_paths():
        if folder in expected and mount_identity(folder)["filesystem"] not in REMOTE_FILESYSTEMS:
            raise OSError("Expected network share is not mounted at " + folder)


def baseline():
    import time
    ready = Path(os.environ.get("POMPEY_READY", "/tmp/pompey"))
    mounts = {str(folder): mount_identity(folder) for folder in storage_paths()}
    path, expected = expected_remote()
    for folder, value in mounts.items():
        if value["filesystem"] in REMOTE_FILESYSTEMS:
            expected[folder] = True
    atomic_json(path, expected)
    atomic_json(ready / "storage-baseline.json", mounts)
    atomic_json(ready / "storage.json", {"ok": True, "phase": "ready", "updated": time.time()})


def report():
    import time
    ready = Path(os.environ.get("POMPEY_READY", "/tmp/pompey"))
    expected = json.loads((ready / "storage-baseline.json").read_text())
    for folder, identity in expected.items():
        if mount_identity(folder) != identity:
            return {"ok": False, "path": folder, "reason": "The expected storage mount is unavailable.", "updated": time.time()}
        try:
            check(folder)
        except OSError as exc:
            import errno
            reason = {errno.ENOSPC: "Storage is full.", errno.EROFS: "Storage is read-only.", errno.EACCES: "Storage access was denied.", errno.EDQUOT: "Storage quota is exhausted."}.get(exc.errno, "Storage could not be accessed.")
            return {"ok": False, "path": folder, "reason": reason,
                    "detail": f"{exc.strerror or 'I/O error'} (errno={exc.errno})",
                    "updated": time.time()}
    return {"ok": True, "updated": time.time()}


def describe(folder):
    path = Path(folder).absolute()
    print(f"Application identity: UID={os.getuid()} GID={os.getgid()}")
    for parent in reversed([path, *path.parents]):
        try:
            info = parent.stat()
            print(
                f"Directory {str(parent)!r}: UID={info.st_uid} GID={info.st_gid} "
                f"mode={stat.S_IMODE(info.st_mode):04o} ({stat.filemode(info.st_mode)})"
            )
        except OSError as exc:
            print(f"Directory {str(parent)!r}: {exc.strerror} (errno={exc.errno})")

    # Never log raw mount options: they can contain SMB credentials or identities.
    selected = None
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        before, after = line.split(" - ", 1)
        fields, details = before.split(), after.split()
        mountpoint = Path(unescape_mount(fields[4]))
        if path == mountpoint or mountpoint in path.parents:
            if selected is None or len(mountpoint.parts) >= len(selected[0].parts):
                selected = mountpoint, details[0], fields[5] + "," + details[2]
    if selected:
        mountpoint, filesystem, options = selected
        safe = sorted(
            {
                option
                for option in options.split(",")
                if option in {"ro", "rw", "noperm", "forceuid", "forcegid", "multiuser"}
                or re.fullmatch(r"(?:uid|gid|file_mode|dir_mode)=[0-9]+", option)
            }
        )
        print(
            f"Filesystem: {filesystem}; mount={str(mountpoint)!r}; "
            f"options={','.join(safe)}"
        )
        if filesystem == "cifs":
            print(
                "SMB/CIFS ownership and modes may be supplied by the client mount; "
                "chown/chmod can succeed without changing them. Check whether the "
                "mount is read-only and whether the account used by Home Assistant "
                "has write access on the server. Recreating downloads does not change "
                "mount permissions."
            )
    print(
        "Applications need read, write and directory traversal access. "
        "Check the mount state, available space and server permissions. "
        "Do not delete downloads to repair permissions."
    )


def main():
    if len(sys.argv) == 2:
        if sys.argv[1] == "validate-expected":
            validate_expected()
        elif sys.argv[1] == "baseline":
            baseline()
        elif sys.argv[1] == "report":
            print(json.dumps(report()))
        else:
            raise ValueError("Unknown storage action")
        return 0
    action, folder = sys.argv[1:]
    if action == "describe":
        describe(folder)
        return 0
    if action != "check":
        raise ValueError("Expected check or describe")
    try:
        check(folder)
    except OSError as exc:
        print(
            f"Storage check failed as UID={os.getuid()} GID={os.getgid()}: "
            f"{exc.strerror} (errno={exc.errno}); path={exc.filename!r}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
