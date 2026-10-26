"""Build the macOS widget, including the draggable original cat resources."""
import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, default=root / 'bin/NekoBudgetWidget.app',
                        help='Existing app bundle to rebuild; close it first.')
    app = parser.parse_args().app.resolve()
    if not (app / 'Contents/Info.plist').is_file():
        parser.error('Target must be an existing macOS app bundle.')
    with tempfile.TemporaryDirectory(prefix='catfood-native-build-') as temporary:
        binary = Path(temporary) / 'neko-budget-widget'
        subprocess.run([
            'xcrun', 'clang', '-fobjc-arc', '-mmacosx-version-min=11.0',
            '-arch', 'arm64', '-arch', 'x86_64',
            '-framework', 'Cocoa', '-framework', 'WebKit',
            '-framework', 'ApplicationServices', str(root / 'native/widget.m'),
            '-o', str(binary),
        ], check=True)
        shutil.copy2(binary, app / 'Contents/MacOS/neko-budget-widget')
    resources = app / 'Contents/Resources'
    resources.mkdir(exist_ok=True)
    for source, target in [('neko-white-cat-walk.gif', 'cat-walk.gif'),
                           ('neko-white-cat-react.gif', 'cat-react.gif')]:
        shutil.copy2(root / 'static/chase' / source, resources / target)
    for name in ['yui-camera-off.png', 'yui-camera-on.png']:
        shutil.copy2(root / 'static/launcher' / name, resources / name)
    subprocess.run(['codesign', '--force', '--sign', '-', str(app)], check=True)
    subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
    for source, target in [('quota-reader.m', 'catfood-quota-reader'), ('app-topup.m', 'catfood-open-topup')]:
        subprocess.run([
            'xcrun', 'clang', '-fobjc-arc', '-mmacosx-version-min=11.0',
            '-arch', 'arm64', '-arch', 'x86_64', '-framework', 'Cocoa',
            '-framework', 'ApplicationServices', str(root / 'native' / source),
            '-o', str(root / 'bin' / target),
        ], check=True)
        subprocess.run(['codesign', '--force', '--sign', '-', str(root / 'bin' / target)], check=True)
    print(f'Built and verified: {app}')


if __name__ == '__main__':
    main()
