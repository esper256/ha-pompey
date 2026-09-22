#!/usr/bin/env python3
"""Validate shared paths before any service creates folders or changes engines."""
import ipaddress
import os
from pathlib import Path

DEFAULTS = {'MEDIA_MOVIES':'Movies/Not Kid Friendly', 'MEDIA_MOVIES_KID':'Movies/Kid Friendly',
            'MEDIA_TV':'TV/Not Kid Friendly', 'MEDIA_TV_KID':'TV/Kid Friendly'}


def media_root():
    root = Path(os.environ.get('MEDIA_ROOT','/media/dlna'))
    if not root.is_absolute() or root == Path('/') or '..' in root.parts:
        raise ValueError('Media folder must be an absolute folder below /')
    return root


def library_dir(name, default):
    raw = os.environ.get(name,default)
    relative = Path(raw)
    if relative.is_absolute() or not raw.strip() or '..' in relative.parts or relative == Path('.'):
        raise ValueError(f'{name} must be a relative library folder without ..')
    return media_root() / relative


def staging_dirs():
    """Arr roots for automatic rating sorts. Kept out of the browsed libraries."""
    base = media_root() / 'downloads' / 'By Rating'
    return base / 'Movies', base / 'TV'


def legacy_staging_dirs():
    """0.3 staging roots beside the general libraries."""
    libraries = [library_dir(name, default) for name, default in DEFAULTS.items()]
    return libraries[0].parent / 'By Rating', libraries[2].parent / 'By Rating'


def _overlaps(path, other):
    return path == other or path.is_relative_to(other) or other.is_relative_to(path)


def validate():
    libraries = [library_dir(name, default) for name, default in DEFAULTS.items()]
    root = media_root().resolve()
    downloads = root / 'downloads'
    reserved = [p.resolve() for p in (
        *staging_dirs(),
        *legacy_staging_dirs(),
        *(downloads / name for name in ('complete', 'incomplete', 'manual', 'recycle')),
    )]
    resolved = []
    for folder in libraries:
        path = folder.resolve()
        if not path.is_relative_to(root) or path == root or path == downloads or path.is_relative_to(downloads):
            raise ValueError('Library folders must remain inside the media folder and outside downloads')
        resolved.append(path)
    for i, path in enumerate(resolved):
        for other in resolved[i + 1:]:
            if _overlaps(path, other):
                raise ValueError(f'Library folders overlap: {path} and {other}')
        for other in reserved:
            if _overlaps(path, other):
                raise ValueError(f'Library and download folders overlap: {path} and {other}')
    if os.environ.get('AFTER_DOWNLOAD','stop_sharing') not in {'stop_sharing','share_to_ratio','share_one_day'}:
        raise ValueError('Unknown after-download sharing policy')
    if not 1 <= int(os.environ.get('SIMULTANEOUS_DOWNLOADS','8')) <= 20:
        raise ValueError('Simultaneous downloads must be between 1 and 20')
    gateway = os.environ.get('NAT_PMP_GATEWAY','')
    if gateway:
        ipaddress.IPv4Address(gateway)


if __name__ == '__main__':
    validate()
