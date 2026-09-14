#!/usr/bin/env python3
"""Install a verified engine bundle with durable rollback of binaries and databases."""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

SERVICES = {'Radarr':'radarr', 'Sonarr':'sonarr', 'Prowlarr':'prowlarr',
            'qbittorrent-nox':'qbittorrent', 'seerr':'seerr'}
HEALTH = {'radarr':'http://127.0.0.1:7878/ping', 'sonarr':'http://127.0.0.1:8989/ping',
          'prowlarr':'http://127.0.0.1:9696/ping', 'qbittorrent':'http://127.0.0.1:8080/api/v2/app/version',
          'seerr':'http://127.0.0.1:5055/api/v1/settings/public'}


def manifest_path():
    return Path(os.environ.get('POMPEY_ENGINE_MANIFEST', '/usr/share/pompey/engines.json')) if (
        os.environ.get('POMPEY_ENGINE_MANIFEST') or Path('/usr/share/pompey/engines.json').exists()
    ) else Path(__file__).resolve().parents[2] / 'share/pompey/engines.json'


def target():
    arch = 'aarch64' if platform.machine() in {'aarch64','arm64'} else 'amd64'
    os_name = os.environ.get('POMPEY_SERVARR_OS') or ('linuxmusl' if Path('/etc/alpine-release').exists() else 'linux')
    return os_name + '-' + arch


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    with temp.open('w') as out:
        json.dump(data, out, indent=2)
        out.flush()
        os.fsync(out.fileno())
    temp.replace(path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextlib.contextmanager
def stack_lock():
    path = Path(os.environ.get('POMPEY_DATA', '/data/pompey')) / 'stack.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def run_process(args, timeout):
    """Finish or stop the whole mutation job before its caller releases the lock."""
    proc = subprocess.Popen(args, start_new_session=True)
    try:
        code = proc.wait(timeout=timeout)
        if code:
            raise subprocess.CalledProcessError(code, args)
    finally:
        # Also clean up children whose parent exited successfully or was killed.
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()


def reject_downgrades(root, entries):
    """Compare known release versions; never inspect or migrate app databases."""
    def number(value):
        match = re.match(r'v?(\d+(?:\.\d+)+)', value)
        return tuple(map(int, match.group(1).split('.'))) if match else ()

    active = root / '.active-manifest.json'
    previous = json.loads(active.read_text()).get('engines', {}) if active.exists() else {}
    for name, entry in entries.items():
        version = previous.get(name, {}).get('version', '')
        if not version and name in {'Radarr', 'Sonarr', 'Prowlarr'}:
            stamp = root / '.stamps' / name
            if stamp.exists():
                match = re.search(r'\d+\.\d+\.\d+\.\d+', stamp.read_text())
                version = match.group() if match else ''
        if not version and name == 'seerr':
            package = root / 'seerr/app/package.json'
            if package.exists():
                version = json.loads(package.read_text()).get('version', '')
        current, wanted = number(version), number(entry.get('version', ''))
        if current and wanted and current > wanted:
            raise RuntimeError(f'{name} {version} is newer than the bundled {entry["version"]}; use a Pompey release with an equal or newer engine')


def download(artifact, dest):
    algorithm = 'sha256' if 'sha256' in artifact else 'sha512'
    expected = artifact.get(algorithm)
    if not expected:
        raise ValueError('An engine artifact must have a checksum')
    digest = hashlib.new(algorithm)
    with urllib.request.urlopen(artifact['url'], timeout=60) as incoming, dest.open('wb') as out:
        while chunk := incoming.read(1024 * 1024):
            out.write(chunk)
            digest.update(chunk)
    if digest.hexdigest().lower() != expected.lower():
        raise RuntimeError('Engine checksum mismatch')


def unpack(archive, dest, prefixes=None):
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as tar:
        # data_filter rejects escaping paths, special devices and unsafe links.
        members = None if prefixes is None else [m for m in tar.getmembers() if any(m.name.removeprefix('./') == p or m.name.removeprefix('./').startswith(p + '/') for p in prefixes)]
        tar.extractall(dest, members=members, filter='data')


def elf(path):
    with path.open('rb') as inp:
        if inp.read(4) != b'\x7fELF':
            raise RuntimeError(f'{path.name} is not a Linux executable')
    path.chmod(path.stat().st_mode | 0o111)


def stage(name, entry, folder):
    folder.mkdir(parents=True)
    if name == 'seerr':
        image = entry['image']
        if '@sha256:' not in image:
            raise ValueError('Seerr image must be pinned by digest')
        archive = folder / 'image.tar'
        subprocess.run(['crane','export','--platform', 'linux/arm64' if target().endswith('aarch64') else 'linux/amd64', image, str(archive)], check=True, timeout=600)
        unpack(archive, folder / 'unpacked', prefixes=['app','usr/local/bin/node'])
        archive.unlink()
        dest = folder / 'unpacked'
        if not (dest / 'app/dist/index.js').is_file():
            raise RuntimeError('Seerr image has no application')
        elf(dest / 'usr/local/bin/node')
        config = dest / 'app/config'
        if config.is_symlink() or config.is_file():
            config.unlink()
        elif config.exists():
            shutil.rmtree(config)
        config.symlink_to(Path(os.environ.get('POMPEY_CONFIG','/config')) / 'seerr')
        return dest
    artifact = entry['artifacts'][target()]
    archive = folder / 'download'
    download(artifact, archive)
    if name == 'qbittorrent-nox':
        elf(archive)
        return archive
    unpack(archive, folder / 'unpacked')
    archive.unlink()
    launcher = {'recyclarr':'recyclarr', 'dotnet':'dotnet'}.get(name, name)
    candidates = list((folder / 'unpacked').rglob(launcher))
    found = next((p for p in candidates if p.is_file()), None)
    if found is None:
        raise RuntimeError(f'No {launcher} in artifact')
    elf(found)
    # Native libraries/apphosts must retain executable mapping permissions.
    for path in found.parent.rglob('*'):
        if path.is_file() and not path.is_symlink():
            with path.open('rb') as inp:
                if inp.read(4) == b'\x7fELF':
                    path.chmod(path.stat().st_mode | 0o111)
    return found.parent


class Services:
    def path(self, name):
        for root in ['/run/s6/legacy-services','/run/service','/var/run/s6/services','/run/s6-rc/servicedirs']:
            path = Path(root) / name
            if path.is_dir():
                return path
        raise RuntimeError('Cannot locate supervised service ' + name)

    def stop(self, names):
        for name in names:
            path = self.path(name)
            subprocess.run(['s6-svc','-d',str(path)], check=True)
            subprocess.run(['s6-svwait','-d','-t','60000',str(path)], check=True, timeout=65)

    def start(self, names):
        for name in names:
            subprocess.run(['s6-svc','-u',str(self.path(name))], check=True)

    def health(self, names):
        deadline = time.monotonic() + 150
        pending = set(names)
        while pending and time.monotonic() < deadline:
            for name in list(pending):
                try:
                    with urllib.request.urlopen(HEALTH[name], timeout=3) as response:
                        if response.status == 200:
                            pending.remove(name)
                except OSError:
                    pass
            if pending:
                time.sleep(1)
        if pending:
            raise RuntimeError('Engine health failed: ' + ', '.join(sorted(pending)))


class Transaction:
    def __init__(self, engines, config, services):
        self.engines, self.config, self.services = Path(engines), Path(config), services
        self.journal = self.engines / '.transaction.json'
        self.backup = self.engines / '.rollback'

    def start_restored(self, record):
        ready = Path(os.environ.get('POMPEY_READY', '/tmp/pompey'))
        ready.mkdir(parents=True, exist_ok=True)
        marker = ready / 'engines-ready'
        if record.get('originals') or record['previous']:
            marker.touch()  # Restored configurations already exist, including after reboot.
        else:
            marker.unlink(missing_ok=True)
        self.services.start(record['services'])

    def rollback(self, record):
        names = record['services']
        self.services.stop(names)
        if record['phase'] == 'stopping':
            self.start_restored(record)
            self.journal.unlink()
            return
        # Backups are preserved until the rollback itself completes. A second
        # interruption can safely retry restoration from the same snapshot.
        for name in record['engines']:
            old = self.backup / name
            dest = self.engines / name
            if old.exists():
                if dest.is_dir(): shutil.rmtree(dest)
                elif dest.exists(): dest.unlink()
                if old.is_dir(): shutil.copytree(old, dest, symlinks=True)
                else: shutil.copy2(old, dest)
            elif name not in record.get('originals', record['previous']):
                if dest.is_dir(): shutil.rmtree(dest)
                elif dest.exists(): dest.unlink()
        if record['phase'] != 'stopping' and (self.backup / 'config').exists():
            self.config.mkdir(parents=True, exist_ok=True)
            for child in self.config.iterdir():
                if child.name not in {'radarr','sonarr','prowlarr','seerr','qBittorrent'}: continue
                if child.is_dir() and not child.is_symlink(): shutil.rmtree(child)
                else: child.unlink()
            shutil.copytree(self.backup / 'config', self.config, symlinks=True, dirs_exist_ok=True)
        active = self.engines / '.active-manifest.json'
        if record.get('manifest') is not None:
            atomic_json(active, record['manifest'])
        else:
            active.unlink(missing_ok=True)
        self.start_restored(record)
        if record.get('originals') or record['previous']:
            self.services.health(names)
        atomic_json(self.engines / '.installed.json', record['previous'])
        self.journal.unlink()

    def recover(self):
        if self.journal.exists():
            record = json.loads(self.journal.read_text())
            if record['phase'] == 'committed':
                self.journal.unlink()
            else:
                self.rollback(record)

    def install(self, staged, versions, configure=None, validate=None, manifest=None):
        self.recover()
        installed = self.engines / '.installed.json'
        previous = json.loads(installed.read_text()) if installed.exists() else {}
        names = [SERVICES[n] for n in SERVICES if n in versions]
        if self.backup.exists(): shutil.rmtree(self.backup)
        self.backup.mkdir()
        active = self.engines / '.active-manifest.json'
        record = {'manifest': json.loads(active.read_text()) if active.exists() else None, 'engines':list(staged),'services':names,'previous':previous,'originals':[n for n in staged if (self.engines/n).exists()],'phase':'stopping'}
        atomic_json(self.journal, record)
        try:
            self.services.stop(names)
            (self.backup / 'config').mkdir()
            for name in ['radarr','sonarr','prowlarr','seerr','qBittorrent']:
                if (self.config/name).exists():
                    shutil.copytree(self.config/name, self.backup/'config'/name, symlinks=True)
            for name in staged:
                dest = self.engines / name
                if dest.is_dir(): shutil.copytree(dest, self.backup / name, symlinks=True)
                elif dest.exists(): shutil.copy2(dest, self.backup / name)
            record['phase'] = 'installing'
            atomic_json(self.journal, record)
            for name, source in staged.items():
                dest = self.engines / name
                if dest.is_dir(): shutil.rmtree(dest)
                elif dest.exists(): dest.unlink()
                Path(source).replace(dest)
            if manifest is not None:
                atomic_json(active, manifest)
            if configure: configure()
            self.services.start(names)
            self.services.health(names)
            if validate: validate()
            atomic_json(installed, versions)
            record['phase'] = 'committed'
            atomic_json(self.journal, record)
            self.journal.unlink()
        except BaseException:
            self.rollback(record)
            raise


def selected(manifest):
    result = {}
    for name, entry in manifest['engines'].items():
        skip = {'qbittorrent-nox':'QBIT'}.get(name, name.upper())
        if os.environ.get('POMPEY_SKIP_' + skip) == '1': continue
        if name == 'dotnet' and (not target().startswith('linuxmusl') or os.environ.get('POMPEY_SKIP_RECYCLARR') == '1'): continue
        result[name] = entry
    return result


def main():
    manifest = json.loads(manifest_path().read_text())
    entries = selected(manifest)
    if '--print-urls' in sys.argv:
        for entry in entries.values(): print(entry.get('image') or entry['artifacts'][target()]['url'])
        return
    root = Path(os.environ.get('POMPEY_ENGINES','/data/engines'))
    root.mkdir(parents=True, exist_ok=True)
    manager = Transaction(root, os.environ.get('POMPEY_CONFIG','/config'), Services())
    # Recover before comparing installed identities or starting another download.
    with stack_lock(): manager.recover()
    installed_path = root / '.installed.json'
    old = json.loads(installed_path.read_text()) if installed_path.exists() else {}
    identities = {name: hashlib.sha256(json.dumps({'engine':entry,'resources':manifest.get('resources') if name == 'recyclarr' else None},sort_keys=True).encode()).hexdigest() for name, entry in entries.items()}
    changed = [name for name in entries if old.get(name) != identities[name] or not (root/name).exists()]
    ready = Path(os.environ.get('POMPEY_READY','/tmp/pompey'))
    def configure():
        subprocess.run(['write-engine-configs'],check=True)
        ready.mkdir(parents=True,exist_ok=True)
        (ready/'engines-ready').touch()

    if not changed:
        if not (ready/'engines-ready').exists():
            with stack_lock(): configure()
        return
    reject_downgrades(root, entries)
    if os.environ.get('POMPEY_FAKE_VPN') != '1' and not (ready / 'vpn-up').exists():
        raise RuntimeError('VPN is not ready for engine downloads')
    with tempfile.TemporaryDirectory(prefix='.staging-',dir=root) as temp:
        try:
            staged = {name:stage(name,entries[name],Path(temp)/name) for name in changed}
        except Exception:
            # A release download failure must not strand an existing complete
            # installation on restart. Keep its manifest and retry in the controller.
            launchers = {'seerr':'seerr/usr/local/bin/node', 'qbittorrent-nox':'qbittorrent-nox'}
            legacy_complete = (root/'.stamps').is_dir() and all(
                (root/launchers.get(n, f'{n}/{n}')).is_file() for n in entries)
            if not (ready/'engines-ready').exists() and (
                (old and all((root/n).exists() and n in old for n in entries)) or legacy_complete
            ):
                with stack_lock(): configure()
                print('Bundle download failed; starting the installed bundle',file=sys.stderr)
                return
            raise
        with stack_lock():
            def validate():
                (ready/"recyclarr").unlink(missing_ok=True)
                run_process([sys.executable,str(Path(__file__).with_name('wire_stack.py'))], timeout=300)
            manager.install(staged,identities,configure,validate,manifest)


if __name__ == '__main__':
    def terminate(_signal, _frame):
        raise SystemExit(143)
    signal.signal(signal.SIGTERM, terminate)
    try:
        main()
    except Exception as exc:
        print(f'Engine update failed: {exc}',file=sys.stderr)
        sys.exit(1)
