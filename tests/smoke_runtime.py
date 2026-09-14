#!/usr/bin/env python3
"""Run inside the built addon image: musl/layout/API smoke, without Supervisor or peers."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0,'/usr/local/bin')
import engine_manager as engines


def main():
    manifest=json.loads(engines.manifest_path().read_text())
    # Supervisor provides /data. A local image run does not.
    Path('/data').mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='runtime-',dir='/data') as temp:
        root=Path(temp);procs=[];logs=[]
        try:
            staged={name:engines.stage(name,manifest['engines'][name],root/name)
                    for name in ['Radarr','Sonarr','Prowlarr','seerr','recyclarr','dotnet']}
            for name,port in [('Radarr',7878),('Sonarr',8989),('Prowlarr',9696)]:
                config=root/(name+'-config');config.mkdir()
                (config/'config.xml').write_text(f'<Config><Port>{port}</Port><BindAddress>127.0.0.1</BindAddress><ApiKey>{"a"*32}</ApiKey><AuthenticationMethod>None</AuthenticationMethod><AuthenticationRequired>DisabledForLocalAddresses</AuthenticationRequired><LaunchBrowser>False</LaunchBrowser></Config>')
                log=(root/(name+'.log')).open('w');logs.append(log)
                procs.append(subprocess.Popen([str(staged[name]/name),'-nobrowser','-data='+str(config)],stdout=log,stderr=subprocess.STDOUT))
            app=staged['seerr']/'app';config=root/'seerr-config';config.mkdir()
            log=(root/'seerr.log').open('w');logs.append(log)
            procs.append(subprocess.Popen([str(staged['seerr']/'usr/local/bin/node'),'dist/index.js'],cwd=app,
                         env={**os.environ,'NODE_ENV':'production','PORT':'5055','HOST':'127.0.0.1','CONFIG_DIRECTORY':str(config)},stdout=log,stderr=subprocess.STDOUT))
            subprocess.run([str(staged['recyclarr']/'recyclarr'),'--version'],check=True,
                           env={**os.environ,'DOTNET_ROOT':str(staged['dotnet'])},timeout=30)
            pending={'radarr','sonarr','prowlarr','seerr'}
            for _ in range(150):
                if any(proc.poll() is not None for proc in procs):raise RuntimeError('A runtime process exited')
                for name in list(pending):
                    try:
                        with urllib.request.urlopen(engines.HEALTH[name],timeout=2) as response:
                            if response.status==200:pending.remove(name)
                    except OSError:pass
                if not pending:break
                time.sleep(1)
            if pending:raise RuntimeError('Runtime health failed: '+str(pending))
            print('Production musl layout, Node/sqlite, Arr APIs and Recyclarr runtime passed')
        except Exception:
            for log in logs:
                log.flush()
                print(Path(log.name).read_text()[-4000:],file=sys.stderr)
            raise
        finally:
            for proc in procs:
                proc.terminate()
            for proc in procs:
                try:proc.wait(15)
                except subprocess.TimeoutExpired:proc.kill();proc.wait()
            for log in logs:log.close()


if __name__=='__main__':main()
