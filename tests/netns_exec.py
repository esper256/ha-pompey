"""Execute inside a test-created network namespace without remounting host /sys."""

import os
from pathlib import Path
import sys

namespace = Path("/run/netns") / sys.argv[1]
if namespace.parent != Path("/run/netns"):
    raise ValueError("Invalid test namespace")
fd = os.open(namespace, os.O_RDONLY)
os.setns(fd, os.CLONE_NEWNET)
os.close(fd)
os.execvp(sys.argv[2], sys.argv[2:])
