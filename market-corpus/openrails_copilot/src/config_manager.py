"""
参数配置对比工具

用于保存和切换不同的调优配置，方便 A/B 测试。

用法：
  # 保存当前配置为"保守型"
  python plugin/plugins/openrails_copilot/config_manager.py save conservative

  # 保存当前配置为"激进型"
  python plugin/plugins/openrails_copilot/config_manager.py save aggressive

  # 列出所有保存的配置
  python plugin/plugins/openrails_copilot/config_manager.py list

  # 切换到"保守型"配置
  python plugin/plugins/openrails_copilot/config_manager.py load conservative

  # 对比两个配置
  python plugin/plugins/openrails_copilot/config_manager.py diff conservative aggressive
"""

import argparse
import json
import re
import shutil
from datetime import datetime
from pathlib import Path


class ConfigManager:
    """管理不同的参数配置"""
    
    def __init__(self, plugin_dir: Path):
        self.plugin_dir = plugin_dir
        self.configs_dir = plugin_dir / "configs"
        self.configs_dir.mkdir(exist_ok=True)
        
        # 需要管理的文件
        self.managed_files = [
            "core/constants.py",
            "core/arbiter.py",
            "core/event_catalog.py",
        ]
    
    def save_config(self, name: str):
        """保存当前配置"""
        config_dir = self.configs_dir / name
        config_dir.mkdir(exist_ok=True)
        
        metadata = {
            "name": name,
            "saved_at": datetime.now().isoformat(),
            "description": "",
        }
        
        # 复制文件
        for file_path in self.managed_files:
            src = self.plugin_dir / file_path
            dst = config_dir / Path(file_path).name
            if src.exists():
                shutil.copy2(src, dst)
                print(f"✅ 已保存: {file_path}")
        
        # 保存元数据
        with open(config_dir / "metadata.json", "w") as f:
            json.dump(metadata, f, indent=2)
        
        print(f"\n✅ 配置已保存为 '{name}'")
        print(f"   位置: {config_dir}")
    
    def load_config(self, name: str):
        """加载指定配置"""
        config_dir = self.configs_dir / name
        if not config_dir.exists():
            print(f"❌ 配置 '{name}' 不存在")
            print("\n可用配置:")
            self.list_configs()
            return
        
        # 备份当前配置
        backup_name = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        print(f"📦 正在备份当前配置为 '{backup_name}'...")
        self.save_config(backup_name)
        
        # 恢复文件
        for file_path in self.managed_files:
            src = config_dir / Path(file_path).name
            dst = self.plugin_dir / file_path
            if src.exists():
                shutil.copy2(src, dst)
                print(f"✅ 已恢复: {file_path}")
        
        print(f"\n✅ 已切换到配置 '{name}'")
    
    def list_configs(self):
        """列出所有保存的配置"""
        configs = []
        for config_dir in self.configs_dir.iterdir():
            if not config_dir.is_dir():
                continue
            
            metadata_file = config_dir / "metadata.json"
            if metadata_file.exists():
                with open(metadata_file) as f:
                    metadata = json.load(f)
                configs.append((config_dir.name, metadata))
            else:
                configs.append((config_dir.name, {"saved_at": "未知"}))
        
        if not configs:
            print("暂无保存的配置")
            return
        
        print("\n可用配置:")
        for name, metadata in sorted(configs):
            saved_at = metadata.get("saved_at", "未知")
            if saved_at != "未知":
                saved_at = saved_at.split("T")[0]  # 只显示日期
            desc = metadata.get("description", "")
            print(f"  • {name:20s} (保存于 {saved_at})")
            if desc:
                print(f"    {desc}")
    
    def diff_configs(self, name1: str, name2: str):
        """对比两个配置的差异"""
        config1 = self.configs_dir / name1
        config2 = self.configs_dir / name2
        
        if not config1.exists() or not config2.exists():
            print("❌ 配置不存在")
            return
        
        print(f"\n📊 对比配置: {name1} vs {name2}\n")
        
        for file_name in ["constants.py", "arbiter.py"]:
            file1 = config1 / file_name
            file2 = config2 / file_name
            
            if not (file1.exists() and file2.exists()):
                continue
            
            print(f"━━━ {file_name} ━━━")
            
            # 提取关键参数
            params1 = self._extract_params(file1)
            params2 = self._extract_params(file2)
            
            # 找出差异
            all_keys = set(params1.keys()) | set(params2.keys())
            has_diff = False
            
            for key in sorted(all_keys):
                val1 = params1.get(key, "(未定义)")
                val2 = params2.get(key, "(未定义)")
                if val1 != val2:
                    has_diff = True
                    print(f"  {key:40s} : {val1:15s} → {val2:15s}")
            
            if not has_diff:
                print("  (无差异)")
            print()
    
    def _extract_params(self, file_path: Path) -> dict[str, str]:
        """从 Python 文件中提取参数值"""
        params = {}
        with open(file_path, encoding="utf-8") as f:
            content = f.read()
        
        # 匹配形如 PARAM_NAME = value 的行
        pattern = r'^([A-Z_]+)\s*=\s*(.+?)(?:\s*#.*)?$'
        for match in re.finditer(pattern, content, re.MULTILINE):
            key = match.group(1)
            value = match.group(2).strip()
            params[key] = value
        
        return params
    
    def export_comparison_table(self, *names: str):
        """导出配置对比表（Markdown 格式）"""
        if len(names) < 2:
            print("❌ 至少需要两个配置名称")
            return
        
        # 收集所有配置的参数
        all_params = {}
        for name in names:
            config_dir = self.configs_dir / name
            if not config_dir.exists():
                print(f"❌ 配置 '{name}' 不存在")
                return
            
            params = {}
            for file_name in ["constants.py", "arbiter.py"]:
                file_path = config_dir / file_name
                if file_path.exists():
                    params.update(self._extract_params(file_path))
            
            all_params[name] = params
        
        # 生成 Markdown 表格
        output_path = self.plugin_dir / "config_comparison.md"
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("# 配置对比表\n\n")
            f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            
            # 表头
            f.write("| 参数 | " + " | ".join(names) + " |\n")
            f.write("|------|" + "|".join(["------"] * len(names)) + "|\n")
            
            # 收集所有参数名
            all_keys = set()
            for params in all_params.values():
                all_keys.update(params.keys())
            
            # 每一行
            for key in sorted(all_keys):
                values = [all_params[name].get(key, "-") for name in names]
                f.write(f"| `{key}` | " + " | ".join(values) + " |\n")
        
        print(f"✅ 对比表已导出: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Open Rails Copilot 配置管理工具")
    subparsers = parser.add_subparsers(dest="command", help="命令")
    
    # save 命令
    save_parser = subparsers.add_parser("save", help="保存当前配置")
    save_parser.add_argument("name", help="配置名称")
    
    # load 命令
    load_parser = subparsers.add_parser("load", help="加载配置")
    load_parser.add_argument("name", help="配置名称")
    
    # list 命令
    subparsers.add_parser("list", help="列出所有配置")
    
    # diff 命令
    diff_parser = subparsers.add_parser("diff", help="对比两个配置")
    diff_parser.add_argument("name1", help="配置1")
    diff_parser.add_argument("name2", help="配置2")
    
    # export 命令
    export_parser = subparsers.add_parser("export", help="导出对比表")
    export_parser.add_argument("names", nargs="+", help="配置名称列表")
    
    args = parser.parse_args()
    
    # 确定插件目录
    plugin_dir = Path(__file__).parent
    manager = ConfigManager(plugin_dir)
    
    if args.command == "save":
        manager.save_config(args.name)
    elif args.command == "load":
        manager.load_config(args.name)
    elif args.command == "list":
        manager.list_configs()
    elif args.command == "diff":
        manager.diff_configs(args.name1, args.name2)
    elif args.command == "export":
        manager.export_comparison_table(*args.names)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
