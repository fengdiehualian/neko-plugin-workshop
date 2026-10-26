"""Create the standard YSM ZIP with ysm.json at its root, then verify exact bytes."""
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

root = Path(__file__).resolve().parent
manifest = json.loads((root / 'neko-yui-manifest.json').read_text(encoding='utf-8'))
target = root / 'neko_yui.zip'
with ZipFile(target, 'w', compression=ZIP_DEFLATED, compresslevel=9) as archive:
    for name in sorted(manifest['files']):
        data = (root / 'neko_yui' / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == manifest['files'][name]['sha256']
        info = ZipInfo(name, date_time=(2026, 10, 11, 0, 0, 0))
        info.compress_type = ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        archive.writestr(info, data, compresslevel=9)
with ZipFile(target) as archive:
    assert archive.testzip() is None
    assert set(archive.namelist()) == set(manifest['files'])
    assert 'ysm.json' in archive.namelist()
    for name in archive.namelist():
        assert archive.read(name) == (root / 'neko_yui' / name).read_bytes()
print(json.dumps({'path': str(target), 'bytes': target.stat().st_size,
                  'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                  'entries': len(manifest['files'])}))
