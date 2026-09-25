"""
打包汐音阁插件为 .neko-plugin 格式
"""
import hashlib
import shutil

try:
    import tomllib
except ImportError:
    import tomli as tomllib
from pathlib import Path

# 读取 plugin.toml
with open("plugin.toml", "rb") as f:
    meta = tomllib.load(f)

plugin_id = meta["plugin"]["id"]
version = meta["plugin"]["version"]
package_name = f"{plugin_id}.neko-plugin"

print(f"开始打包插件: {plugin_id} v{version}")

# 创建临时打包目录
pack_dir = Path("_pack_temp")
if pack_dir.exists():
    shutil.rmtree(pack_dir)
pack_dir.mkdir()

# 复制插件文件到 payload/plugins/<plugin_id>/
payload_dir = pack_dir / "payload"
plugins_dir = payload_dir / "plugins"
plugin_payload_dir = plugins_dir / plugin_id
plugin_payload_dir.mkdir(parents=True, exist_ok=True)
# 复制 plugins/<plugin_id>/ 的内容（而非目录本身）到 payload/plugins/<plugin_id>/
for item in Path(f"plugins/{plugin_id}").iterdir():
    if item.is_dir():
        shutil.copytree(item, plugin_payload_dir / item.name)
    else:
        shutil.copy2(item, plugin_payload_dir / item.name)
shutil.copy2("plugin.toml", plugin_payload_dir / "plugin.toml")

# 创建 profiles 目录
profiles_dir = payload_dir / "profiles"
profiles_dir.mkdir(exist_ok=True)

# 创建 manifest.toml
manifest_content = f"""schema_version = "1.0"
package_type = "plugin"

id = "{plugin_id}"
package_name = "汐音阁"
version = "{version}"
package_description = "音乐推送插件，支持本地上传、在线链接、定时任务队列"
"""

with open(pack_dir / "manifest.toml", "w", encoding="utf-8") as f:
    f.write(manifest_content)

# 创建 metadata.toml
digest = hashlib.sha256()
for path in sorted(payload_dir.rglob("*")):
    if not path.is_dir():
        relative = path.relative_to(payload_dir).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
payload_hash = digest.hexdigest()

metadata_content = f"""[payload]
hash_algorithm = "sha256"
hash = "{payload_hash}"

[source]
kind = "local"
paths = ["{plugin_id}"]
"""

with open(pack_dir / "metadata.toml", "w", encoding="utf-8") as f:
    f.write(metadata_content)

# 打包为 zip
output_file = Path(package_name)
if output_file.exists():
    output_file.unlink()

shutil.make_archive(plugin_id, "zip", pack_dir)
shutil.move(f"{plugin_id}.zip", package_name)

# 清理临时目录
shutil.rmtree(pack_dir)

print(f"打包完成: {package_name}")
print(f"文件大小: {output_file.stat().st_size} 字节")
