import zipfile
from io import BytesIO

import pytest
from plugin.plugins.hsr_companion.data.vision_runtime import VisionMatchRuntime


@pytest.mark.parametrize("path", ["../../outside.py", "/absolute.py", "cv2/../../bad.py", "cv2\\bad.py", "cv2/C:bad.py", "other_package/injected.py"])
def test_runtime_wheel_rejects_unsafe_or_unexpected_paths(tmp_path, path):
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(path, "not executed")
    runtime = VisionMatchRuntime(root=tmp_path)
    with pytest.raises(ValueError):
        runtime._unpack(stream.getvalue())
    assert not (tmp_path / "outside.py").exists()


def test_runtime_wheel_keeps_licenses_and_external_location(tmp_path):
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("cv2/__init__.py", "# fixture only, never imported")
        archive.writestr("opencv_python_headless-4.12.0.88.dist-info/LICENSE.txt", "fixture license")
    runtime = VisionMatchRuntime(root=tmp_path)
    runtime._unpack(stream.getvalue())
    assert runtime.root.is_relative_to(tmp_path)
    assert (runtime.root / "opencv_python_headless-4.12.0.88.dist-info/LICENSE.txt").is_file()
    assert (runtime.root / "INSTALL.json").is_file()
