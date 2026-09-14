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


def validate():
    paths = [library_dir(name,default) for name,default in DEFAULTS.items()]
    paths += [paths[0].parent/'By Rating', paths[2].parent/'By Rating', media_root()/'downloads']
    resolved = [p.resolve() for p in paths]
    for i, path in enumerate(resolved):
        if not path.is_relative_to(media_root().resolve()) or path == media_root().resolve():
            raise ValueError('Library folders must remain inside the media folder')
        for other in resolved[i+1:]:
            if path.is_relative_to(other) or other.is_relative_to(path):
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
