"""Build the audio-only helper for both Mac architectures using Apple's SDK."""
import plistlib
import subprocess
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parent
app = root.parents[1] / 'bin/CatFoodBPM.app'
contents = app/'Contents'
(contents/'MacOS').mkdir(parents=True,exist_ok=True)
info = dict(CFBundleIdentifier='cn.projectneko.plugin.catfood-bpm',CFBundleName='CatFoodBPM',
            CFBundleDisplayName='猫粮节拍',CFBundleExecutable='CatFoodBPM',CFBundlePackageType='APPL',
            CFBundleShortVersionString='0.8.9',CFBundleVersion='1',LSUIElement=True,LSMinimumSystemVersion='13.0',
            NSAudioCaptureUsageDescription='本机分析播放器的音乐节拍，不保存或上传声音。',
            NSScreenCaptureUsageDescription='仅接收系统音频计算 BPM，不接收、保存或上传屏幕画面。')
(contents/'Info.plist').write_bytes(plistlib.dumps(info))
with tempfile.TemporaryDirectory(prefix='catfood-bpm-build-') as directory:
    outputs=[]
    for arch in ['arm64','x86_64']:
        output=Path(directory)/arch
        subprocess.run(['swiftc','-O','-target',f'{arch}-apple-macos13.0','-module-cache-path',str(Path(directory)/'cache'),
                        *[str(root/name) for name in ['TempoTracker.swift','AudioListener.swift','CatFoodBPM.swift']],'-o',str(output)],check=True)
        outputs.append(str(output))
    subprocess.run(['lipo','-create',*outputs,'-output',str(contents/'MacOS/CatFoodBPM')],check=True)
subprocess.run(['xattr','-cr',str(app)],check=True)
subprocess.run(['codesign','--force','--sign','-',str(app)],check=True)
subprocess.run(['codesign','--verify','--deep','--strict',str(app)],check=True)
print('Universal macOS 13+ BPM helper built and signature verified')
