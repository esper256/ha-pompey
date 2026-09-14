#!/usr/bin/env python3
"""Resolve an upstream candidate bundle. Review and test the output before promotion."""
import argparse
import json
from pathlib import Path
import urllib.parse
import urllib.request


def get(url, headers=None):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=60))


def build():
    repos = {'Radarr':'Radarr/Radarr', 'Sonarr':'Sonarr/Sonarr', 'Prowlarr':'Prowlarr/Prowlarr',
             'recyclarr':'recyclarr/recyclarr', 'qbittorrent-nox':'userdocs/qbittorrent-nox-static'}
    manifest = {'schema':1, 'engines':{}}
    for engine, repo in repos.items():
        release = get('https://api.github.com/repos/' + repo + '/releases/latest')
        artifacts = {}
        for arch, cpu in [('amd64','x64'),('aarch64','arm64')]:
            for os_name in ['linux', 'linuxmusl']:
                if engine == 'qbittorrent-nox':
                    name = ('x86_64' if arch == 'amd64' else 'aarch64') + '-qbittorrent-nox'
                    matches = [a for a in release['assets'] if a['name'] == name]
                else:
                    rid = ('linux-musl' if os_name == 'linuxmusl' else 'linux')
                    ending = (('-core-' if engine in {'Radarr','Prowlarr'} else '-') + cpu + '.tar.gz') if engine != 'recyclarr' else '-' + cpu + '.tar.xz'
                    matches = [a for a in release['assets'] if rid + ending in a['name']]
                asset = matches[0]
                digest = asset.get('digest') or ''
                if not digest.startswith('sha256:'):
                    raise RuntimeError('Missing upstream SHA256 for ' + asset['name'])
                artifacts[os_name + '-' + arch] = {'url':asset['browser_download_url'], 'sha256':digest.split(':')[1]}
        manifest['engines'][engine] = {'version':release['tag_name'], 'artifacts':artifacts}
    release = get('https://api.github.com/repos/seerr-team/seerr/releases/latest')
    tag = release['tag_name']
    token = get('https://ghcr.io/token?service=ghcr.io&scope=repository:seerr-team/seerr:pull')['token']
    req = urllib.request.Request('https://ghcr.io/v2/seerr-team/seerr/manifests/' + tag,
        headers={'Authorization':'Bearer ' + token, 'Accept':'application/vnd.oci.image.index.v1+json,application/vnd.docker.distribution.manifest.list.v2+json'})
    with urllib.request.urlopen(req, timeout=60) as response:
        digest = response.headers['Docker-Content-Digest']
    manifest['engines']['seerr'] = {'version':tag, 'image':'ghcr.io/seerr-team/seerr@' + digest}
    # The framework-dependent musl Recyclarr launcher needs a pinned runtime too.
    net = get('https://builds.dotnet.microsoft.com/dotnet/release-metadata/10.0/releases.json')
    runtime = net['releases'][0]['runtime']
    artifacts={}
    for arch,cpu in [('amd64','x64'),('aarch64','arm64')]:
        for os_name,rid in [('linux','linux-'),('linuxmusl','linux-musl-')]:
            asset = next(f for f in runtime['files'] if f['rid'] == rid + cpu and f['name'].startswith('dotnet-runtime-') and f['name'].endswith('.tar.gz'))
            artifacts[os_name+'-'+arch]={'url':asset['url'],'sha512':asset['hash']}
    manifest['engines']['dotnet']={'version':runtime['version'],'artifacts':artifacts}
    manifest['resources'] = {
        'trash_guides': get('https://api.github.com/repos/TRaSH-Guides/Guides/commits/master')['sha'],
        'config_templates': get('https://api.github.com/repos/recyclarr/config-templates/commits/master')['sha'],
    }
    return manifest


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('output', type=Path)
    args=parser.parse_args()
    args.output.write_text(json.dumps(build(),indent=2)+'\n')
