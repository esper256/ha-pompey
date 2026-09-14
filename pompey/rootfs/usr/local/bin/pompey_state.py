"""Small durable records for explicit intent and operation receipts."""
import json
import os
from pathlib import Path
import tempfile


def directory():
    return Path(os.environ.get('POMPEY_DATA', '/data/pompey')) / 'state'


def load(name):
    path = directory() / (name + '.json')
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f'Invalid state: {name}')
    return data


def save(name, data):
    root = directory()
    root.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=name + '.', dir=root)
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(data, out, sort_keys=True)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, root / (name + '.json'))
        fd = os.open(root, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
