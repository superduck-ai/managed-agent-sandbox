import hashlib
import os
from pathlib import Path
import pwd
import shlex
import shutil
import tempfile
import tarfile
import time
import urllib.request
import zipfile


def contract(path):
    values = {}
    for line in Path(path).read_text().splitlines():
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1)
            values[key] = shlex.split(value)[0]
    suffix = {'x86_64': 'AMD64', 'aarch64': 'ARM64'}[os.uname().machine]
    for key in ['CHROMIUM_URL', 'CHROMIUM_SHA256', 'GH_URL', 'GH_SHA256', 'D2_URL', 'D2_SHA256', 'TYPST_URL', 'TYPST_SHA256']:
        values[key] = values[f'{key}_{suffix}']
    return values


def digest(path):
    with Path(path).open('rb') as stream:
        value = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
        return value.hexdigest()


def download(url, destination, expected):
    destination = Path(destination)
    if destination.is_file() and digest(destination) == expected:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        try:
            with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as stream:
                temporary = Path(stream.name)
                try:
                    with urllib.request.urlopen(url, timeout=120) as response:
                        shutil.copyfileobj(response, stream)
                    stream.flush()
                    if digest(temporary) != expected:
                        raise ValueError(f'Asset checksum mismatch: {destination.name}')
                    temporary.chmod(0o644)
                    temporary.replace(destination)
                finally:
                    temporary.unlink(missing_ok=True)
            return
        except OSError:
            if attempt == 2:
                raise
            time.sleep(attempt + 1)


def install_binary(name, values, cache):
    key = name.upper()
    archive = cache / values[key + '_SHA256']
    download(values[key + '_URL'], archive, values[key + '_SHA256'])
    licenses = Path('/usr/local/share/licenses/sandbox-toolkit')
    licenses.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, 'r:*') as package:
        members = [m for m in package.getmembers() if m.isfile()]
        matches = [m for m in members if Path(m.name).name == name]
        if len(matches) != 1:
            raise ValueError(f'Ambiguous executable for {name}')
        target = Path('/usr/local/bin') / name
        target.write_bytes(package.extractfile(matches[0]).read())
        target.chmod(0o755)
        for member in members:
            if Path(member.name).name.lower().startswith(('license', 'copying')):
                destination = licenses / (name + '-' + Path(member.name).name)
                destination.write_bytes(package.extractfile(member).read())


def register_browser_caches(browser_root, values):
    browser_name = 'chromium_headless_shell-' + values['CHROMIUM_REVISION']
    for account in ['root', 'user', 'claude']:
        owner = pwd.getpwnam(account)
        cache = Path(owner.pw_dir) / '.cache'
        browsers = cache / 'ms-playwright'
        for directory in [cache, browsers]:
            if not directory.exists():
                directory.mkdir()
                os.chown(directory, owner.pw_uid, owner.pw_gid)
        link, target = browsers / browser_name, browser_root / browser_name
        if link.is_symlink() and link.resolve() == target.resolve():
            continue
        if link.exists() or link.is_symlink():
            raise FileExistsError(link)
        link.symlink_to(target, target_is_directory=True)
        os.lchown(link, owner.pw_uid, owner.pw_gid)


def install(values):
    cache = Path('/var/cache/oma-resources')
    cache.mkdir(parents=True, exist_ok=True)
    for name in ['gh', 'd2', 'typst']:
        install_binary(name, values, cache)
    browser_root = Path(os.environ['PLAYWRIGHT_BROWSERS_PATH'])
    target = browser_root / ('chromium_headless_shell-' + values['CHROMIUM_REVISION'])
    browser_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=browser_root) as directory:
        archive_path = cache / values['CHROMIUM_SHA256']
        download(values['CHROMIUM_URL'], archive_path, values['CHROMIUM_SHA256'])
        unpacked = Path(directory) / 'unpacked'
        with zipfile.ZipFile(archive_path) as archive:
            for item in archive.infolist():
                path = Path(item.filename)
                if path.is_absolute() or '..' in path.parts:
                    raise ValueError('Unsafe Chromium archive path')
            archive.extractall(unpacked)
            for item in archive.infolist():
                if not item.is_dir():
                    (unpacked / item.filename).chmod((item.external_attr >> 16) & 0o777 or 0o644)
        if not (unpacked / 'chrome-linux/headless_shell').is_file():
            raise ValueError('Chromium executable missing')
        if target.exists():
            raise FileExistsError(target)
        unpacked.rename(target)
    model = Path(os.environ['OMA_LOCAL_ASR_MODEL'])
    model.mkdir(parents=True, exist_ok=True)
    prefix = 'https://huggingface.co/Systran/faster-whisper-small/resolve/' + values['WHISPER_REVISION'] + '/'
    for name, key in [('model.bin', 'MODEL'), ('config.json', 'CONFIG'), ('tokenizer.json', 'TOKENIZER'), ('vocabulary.txt', 'VOCABULARY'), ('README.md', 'README')]:
        expected = values['WHISPER_' + key + '_SHA256']
        cached = cache / expected
        download(prefix + name, cached, expected)
        shutil.copyfile(cached, model / name)
        (model / name).chmod(0o644)
    register_browser_caches(browser_root, values)


if __name__ == '__main__':
    install(contract('/etc/oma-sandbox-versions.env'))
