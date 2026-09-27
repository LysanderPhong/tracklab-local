"""Build a source-complete local distribution; exclude all runtime state."""
from pathlib import Path
import hashlib
import json
import zipfile

ROOT=Path(__file__).resolve().parent
VERSION='0.5.0-beta'
FILES=['README.md','THIRD_PARTY.md','LICENSE','.gitignore','pyproject.toml','uv.lock','requirements-desktop.txt',
       'build_release.py','desktop_launcher.py','device.py','routes.py','playback.py','replay.py','replay_worker.py',
       'server.py','sdk_cli.py','cloud_server.py','relay.py','map_service.py','connector.py',
       '一键连接-Mac.command','一键连接-Windows.cmd','安装连接程序-Windows.cmd']

def build():
    paths=[ROOT/f for f in FILES]
    for folder in ['portal','licenses','tests']:
        paths += [p for p in (ROOT/folder).rglob('*') if p.is_file()
                  and not any(x in {'__pycache__','downloads'} for x in p.parts)
                  and p.suffix not in {'.pyc','.apk','.zip'} and p.name!='.DS_Store']
    out=ROOT/'dist';out.mkdir(exist_ok=True)
    target=out/f'TrackLab-Local-{VERSION}.zip'
    manifest={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as z:
        for p in sorted(paths):z.write(p,Path('TrackLab-Local')/p.relative_to(ROOT))
        z.writestr('TrackLab-Local/RELEASE.json',json.dumps({'version':VERSION,'sha256':manifest},indent=2)+'\n')
    checksum=out/'SHA256SUMS.txt'
    checksum.write_text(hashlib.sha256(target.read_bytes()).hexdigest()+'  '+target.name+'\n')
    print(target)
    return target

if __name__=='__main__':build()
