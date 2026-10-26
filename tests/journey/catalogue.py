"""Namespaced external movie identities, including unique bytes in Matroska padding."""

from dataclasses import dataclass
import gzip
import hashlib
import json
import os
from pathlib import Path
import re


@dataclass(frozen=True)
class Case:
    index: int
    prefix: str
    certification: str

    @property
    def movie_id(self):
        return 1184918 + self.index

    @property
    def imdb_id(self):
        return str(29623480 + self.index)

    @property
    def title(self):
        return self.prefix

    @property
    def release(self):
        return self.prefix + ".2024.1080p.WEB-DL"

    @property
    def library(self):
        return "Kid Friendly" if self.certification == "PG" else "Not Kid Friendly"


def cases():
    run = os.environ["POMPEY_TEST_RUN_ID"]
    if not re.fullmatch("[a-f0-9]{10}", run):
        raise ValueError("Invalid test run identity")
    count = int(os.environ.get("POMPEY_TEST_CASES", "3"))
    if not 1 <= count <= 100:
        raise ValueError("Choose between 1 and 100 scenarios")
    return [
        Case(i, f"Pompey{run}Case{i:03d}", ["PG", "R", ""][i % 3]) for i in range(count)
    ]


def prepare_media(folder):
    fixture = Path("/tests/journey/fixtures")
    original = gzip.decompress((fixture / "movie.mkv.gz").read_bytes())
    metadata = json.loads((fixture / "movie.json").read_text())
    if (
        len(original) != metadata["bytes"]
        or hashlib.sha256(original).hexdigest() != metadata["sha256"]
    ):
        raise RuntimeError("Synthetic media fixture is corrupted")
    offset = metadata["identityPaddingOffset"]
    length = 128
    if original[offset : offset + length] != bytes(length):
        raise RuntimeError("Identity padding must be inside the reserved Matroska Void")
    end = metadata["sparsePaddingEnd"]
    if any(original[offset + length : end]):
        raise RuntimeError("Sparse region must contain only Matroska Void padding")
    for case in cases():
        target = folder / case.release
        target.mkdir()
        # The long zero-filled Void is stored as a sparse hole, not N disk copies.
        with (target / (case.release + ".mkv")).open("wb") as output:
            output.write(original[:offset])
            output.write(case.prefix.encode().ljust(length, b"\0"))
            output.seek(end)
            output.write(original[end:])
        (target / (case.release + ".en.srt")).write_text(
            f"1\n00:00:00,000 --> 00:00:02,000\nSynthetic {case.prefix}\n"
        )
