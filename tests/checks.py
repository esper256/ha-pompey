"""Syntax, meaningful logic boundaries and shell lint against the built runtime."""

from pathlib import Path
import subprocess

subprocess.run(["python3", "/tests/test_logic.py", "-v"], check=True)
subprocess.run(["python3", "/tests/test_storage.py", "-v"], check=True)
subprocess.run(["python3", "/tests/test_harness.py", "-v"], check=True)
for path in Path("/usr/local/bin").glob("*.py"):
    compile(path.read_bytes(), str(path), "exec")
for root in ["/etc/cont-init.d", "/etc/services.d", "/usr/local/bin"]:
    for path in Path(root).rglob("*"):
        if not path.is_file():
            continue
        try:
            line = path.open("rb").readline(256).decode("utf-8")
        except UnicodeError:
            continue
        if line.startswith("#!") and ("bash" in line or "bashio" in line):
            subprocess.run(
                ["/tests/journey/tool", "shellcheck", "-s", "bash", str(path)],
                check=True,
            )
print("Runtime syntax and shell lint passed")

# Parse the actual inline UI with the shipped Node executable.
import re

html = Path("/usr/share/pompey/index.html").read_text()
for index, script in enumerate(re.findall(r"<script>(.*?)</script>", html, re.S)):
    path = Path("/tmp") / f"pompey-ui-{index}.js"
    path.write_text(script)
    subprocess.run(
        ["/opt/pompey/engines/seerr/usr/local/bin/node", "--check", str(path)],
        check=True,
    )
print("Application UI syntax passed")
