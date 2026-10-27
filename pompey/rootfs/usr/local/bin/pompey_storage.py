#!/usr/bin/env python3
"""Probe storage as the application user and report safe permission diagnostics."""

import os
from pathlib import Path
import re
import stat
import sys
import tempfile


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
