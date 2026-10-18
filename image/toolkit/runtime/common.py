from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import tempfile


ROOT = Path('/mnt/user-data')
OUTPUTS = ROOT / 'outputs'


def input_file(value, limit=500 << 20):
    path = Path(value)
    if not path.is_absolute():
        raise ValueError('Input requires an absolute path under /mnt/user-data')
    path = path.resolve(strict=True)
    if not path.is_relative_to(ROOT) or not path.is_file() or path.stat().st_size > limit:
        raise ValueError('Input must be a local file under /mnt/user-data within the size limit')
    return path


def output_file(value, suffixes=None):
    path = Path(value)
    if not path.is_absolute() or not path.resolve().is_relative_to(OUTPUTS):
        raise ValueError('Output requires an absolute path under /mnt/user-data/outputs')
    if suffixes and path.suffix.lower() not in suffixes:
        raise ValueError('Unsupported output extension: ' + path.suffix)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise ValueError('Output already exists; choose a new file')
    return path


@contextmanager
def staging(destination):
    with tempfile.TemporaryDirectory(prefix='oma-tools-', dir='/tmp') as directory:
        yield Path(directory) / destination.name


def publish(temporary, destination):
    if not temporary.is_file() or temporary.stat().st_size == 0:
        raise ValueError('Tool did not produce a nonempty output')
    created = False
    try:
        with temporary.open('rb') as source, destination.open('xb') as output:
            created = True
            shutil.copyfileobj(source, output)
            output.flush()
    except FileExistsError:
        raise ValueError('Output already exists; choose a new file') from None
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise
    return {'file_path': str(destination), 'bytes': destination.stat().st_size}


def write_json(value, destination):
    with staging(destination) as temporary:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
        return publish(temporary, destination)


def run(arguments, timeout=180):
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(['setpriv', '--pdeathsig', 'TERM', '--', *[str(a) for a in arguments]], stdout=subprocess.PIPE, stderr=errors, start_new_session=True)
        try:
            stdout, _ = process.communicate(timeout=timeout)
        except BaseException:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise
        finally:
            if process.stdout:
                process.stdout.close()
        if process.returncode:
            errors.seek(0, 2)
            errors.seek(max(0, errors.tell() - 2400))
            raise ValueError('Local tool failed: ' + errors.read().decode(errors='replace'))
        return stdout
