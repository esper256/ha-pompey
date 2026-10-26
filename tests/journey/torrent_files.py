"""File-backed torrent payload ranges; no full per-case video copies in memory."""

import os
from pathlib import Path


class TorrentFiles:
    def __init__(self, folder):
        self.parts = []
        self.size = 0
        self.files = sorted(Path(folder).iterdir())
        for path in self.files:
            size = path.stat().st_size
            self.parts.append((self.size, self.size + size, os.open(path, os.O_RDONLY)))
            self.size += size

    def read(self, offset, size):
        if offset < 0 or size < 0 or offset + size > self.size:
            raise ValueError("Torrent range is outside the payload")
        chunks = []
        for start, stop, fd in self.parts:
            begin = max(start, offset)
            end = min(stop, offset + size)
            if begin < end:
                chunk = os.pread(fd, end - begin, begin - start)
                if len(chunk) != end - begin:
                    raise OSError("Fixture file changed during transfer")
                chunks.append(chunk)
        return b"".join(chunks)

    def close(self):
        for _, _, fd in self.parts:
            os.close(fd)
        self.parts = []
