"""Expose useful acceptance timings in Actions without opening an artifact."""

import html
import json
import os
from pathlib import Path


def markdown(data):
    lines = ["## Application acceptance", "", "Result: **" + ("passed" if data.get("success") else "failed / incomplete") + "**",
             "", "Test command elapsed: " + str(data.get("totalSeconds", "unknown")) + " seconds.",
             "", "### Shared checks and test-image preparation", "", "| Section | Seconds |", "| --- | ---: |"]
    for name, seconds in data.get("sharedSeconds", {}).items():
        lines.append("| " + html.escape(str(name)).replace("|", "&#124;") + " | " + str(seconds) + " |")
    for batch in data.get("journeys", []):
        lines += ["", "### " + ("Previous-release upgrade" if batch.get("layout") == "forward-upgrade" else "Integration: " + str(batch.get("layout"))),
                  "", "Result: **" + ("passed" if batch.get("success") else "failed / incomplete") + "**",
                  "", "| Container / scenario | Seconds | Result |", "| --- | ---: | --- |"]
        for index, container in enumerate(batch.get("containers", []), 1):
            lines.append(f"| Container {index}: startup | {container.get('startupSeconds', 'unknown')} | {'passed' if container.get('success') else 'failed / incomplete'} |")
            for case in container.get("cases", []):
                label = html.escape(str(case.get("scenario", case.get("prefix", "scenario")))).replace("|", "&#124;")
                lines.append(f"| {label} | {case.get('durationSeconds', 'handoff')} | {html.escape(str(case.get('status', 'observed')))} |")
            for key, label in [("snapshotExportSeconds", "Snapshot export"), ("snapshotRestoreSeconds", "Snapshot restore"), ("lifecycleOverheadSeconds", "Container lifecycle")]:
                if container.get(key):
                    lines.append(f"| {label} | {container[key]} | — |")
    lines += ["", "Product image build/loading and runner queue time are reported separately in the workflow jobs. Full evidence is retained in `integration-results`.", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    source = Path(".test-results/timing-summary.json")
    report = markdown(json.loads(source.read_text())) if source.exists() else "## Application acceptance\n\nNo timing report was produced; inspect the failing build/setup step.\n"
    print(report)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as output:
            output.write(report)
