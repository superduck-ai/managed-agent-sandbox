import hashlib
import io
import json
from pathlib import Path
import platform
import tarfile
import urllib.request
import zipfile


def download(item):
    request = urllib.request.Request(item['url'], headers={'User-Agent': 'oma-toolkit-build'})
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != item['sha256']:
        raise ValueError('Asset checksum mismatch')
    return data


def main():
    contract = json.loads(Path(__file__).with_name('assets.json').read_text())
    arch = {'aarch64': 'arm64', 'x86_64': 'amd64'}[platform.machine()]
    licenses = Path('/usr/local/share/oma-tools/licenses')
    licenses.mkdir(parents=True, exist_ok=True)
    for item in contract[arch]:
        data = download(item)
        if item['url'].endswith('.zip'):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = archive.namelist()
                matches = [n for n in names if Path(n).name == item['binary']]
                if len(matches) != 1:
                    raise ValueError('Archive binary is ambiguous')
                binary = archive.read(matches[0])
                for name in names:
                    if Path(name).name.lower().startswith(('license', 'copying')):
                        (licenses / (item['binary'] + '-' + Path(name).name)).write_bytes(archive.read(name))
        else:
            with tarfile.open(fileobj=io.BytesIO(data)) as archive:
                matches = [m for m in archive.getmembers() if m.isfile() and Path(m.name).name == item['binary']]
                if len(matches) != 1:
                    raise ValueError('Archive binary is ambiguous')
                binary = archive.extractfile(matches[0]).read()
                for member in archive.getmembers():
                    if member.isfile() and Path(member.name).name.lower().startswith(('license', 'copying')):
                        (licenses / (item['binary'] + '-' + Path(member.name).name)).write_bytes(archive.extractfile(member).read())
        target = Path('/usr/local/bin') / item['binary']
        target.write_bytes(binary)
        target.chmod(0o755)
    model = Path('/opt/oma-models/whisper-tiny')
    model.mkdir(parents=True, exist_ok=True)
    for item in contract['whisper']['files']:
        (model / item['name']).write_bytes(download(item))


if __name__ == '__main__':
    main()
