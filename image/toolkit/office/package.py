import hashlib
from pathlib import Path
import sys
import zipfile

source = Path(__file__).resolve().parent / 'skills' / 'oma-office'
output = Path(sys.argv[1] if len(sys.argv) > 1 else 'tmp/office-packages')
output.mkdir(parents=True, exist_ok=True)
archive = output / 'oma-office.skill'
with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as package:
    for path in sorted(source.rglob('*')):
        if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc':
            info = zipfile.ZipInfo(str(path.relative_to(source.parent)), (2026, 10, 17, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            package.writestr(info, path.read_bytes())
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
(output / 'SHA256SUMS').write_text(f'{digest}  {archive.name}\n')
print(f'{digest}  {archive.resolve()}')
