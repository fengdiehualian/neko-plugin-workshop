"""
Open Rails Copilot Plugin 测试脚本
用于验证插件的基本功能
"""

import asyncio
import importlib
import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))


async def test_plugin_import():
    """测试插件是否能正确导入"""
    print("测试 1: 导入插件...")
    try:
        importlib.import_module("plugin.plugins.openrails_copilot")
        print("✓ 插件导入成功")
        return True
    except Exception as e:
        print(f"✗ 插件导入失败: {e}")
        return False


async def test_plugin_instantiation():
    """测试插件是否能正确实例化"""
    print("\n测试 2: 实例化插件...")
    try:
        from plugin.plugins.openrails_copilot import OpenRailsCopilotPlugin
        
        # 创建模拟上下文
        class MockLogger:
            def info(self, msg): print(f"[INFO] {msg}")
            def warning(self, msg): print(f"[WARN] {msg}")
            def error(self, msg): print(f"[ERROR] {msg}")
            def debug(self, msg): print(f"[DEBUG] {msg}")
        
        class MockContext:
            def __init__(self):
                self.logger = MockLogger()
        
        ctx = MockContext()
        plugin = OpenRailsCopilotPlugin(ctx)
        
        print("✓ 插件实例化成功")
        print(f"  - API URL: {plugin.api_base_url}")
        print(f"  - 监控间隔: {plugin.api_check_interval}s")
        return True
    except Exception as e:
        print(f"✗ 插件实例化失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_api_check():
    """测试 API 连接检查"""
    print("\n测试 3: API 连接检查...")
    try:
        from plugin.plugins.openrails_copilot import OpenRailsCopilotPlugin
        
        class MockLogger:
            def info(self, msg): pass
            def warning(self, msg): pass
            def error(self, msg): pass
            def debug(self, msg): pass
        
        class MockContext:
            def __init__(self):
                self.logger = MockLogger()
        
        ctx = MockContext()
        plugin = OpenRailsCopilotPlugin(ctx)
        
        # 初始化
        await plugin.on_startup()
        
        # 检查 API
        available = await plugin._check_api_available()
        
        if available:
            print("✓ Open Rails API 可用")
            print("  游戏正在运行，可以开始监控")
        else:
            print("⚠ Open Rails API 不可用")
            print("  这是正常的，如果游戏未运行")
        
        # 清理
        await plugin.on_shutdown()
        
        return True
    except Exception as e:
        print(f"✗ API 检查测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def test_data_analysis():
    """测试数据分析功能"""
    print("\n测试 4: 数据分析...")
    try:
        from plugin.plugins.openrails_copilot import OpenRailsCopilotPlugin
        
        class MockLogger:
            def info(self, msg): pass
            def warning(self, msg): pass
            def error(self, msg): pass
            def debug(self, msg): pass
        
        class MockContext:
            def __init__(self):
                self.logger = MockLogger()
        
        ctx = MockContext()
        plugin = OpenRailsCopilotPlugin(ctx)
        
        # 模拟游戏数据
        test_data = {
            "Speed": 30.0,  # 30 m/s ≈ 108 km/h
            "Throttle": 0.8,
            "DynamicBrake": 0.0,
            "TrainBrake": 0.0,
        }
        
        analysis = plugin._analyze_data(test_data)
        
        print("✓ 数据分析成功")
        print(f"  - 速度: {analysis['speed_kmh']} km/h")
        print(f"  - 油门: {analysis['throttle_percent']}%")
        print(f"  - 制动: {analysis['brake_percent']}%")
        print(f"  - 告警数: {len(analysis['alerts'])}")
        
        if analysis['alerts']:
            for alert in analysis['alerts']:
                print(f"  - {alert['type']}: {alert['message']}")
        
        return True
    except Exception as e:
        print(f"✗ 数据分析测试失败: {e}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """运行所有测试"""
    print("=" * 60)
    print("Open Rails Copilot Plugin 测试")
    print("=" * 60)
    
    tests = [
        test_plugin_import,
        test_plugin_instantiation,
        test_api_check,
        test_data_analysis,
    ]
    
    results = []
    for test in tests:
        result = await test()
        results.append(result)
    
    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)
    passed = sum(results)
    total = len(results)
    print(f"通过: {passed}/{total}")
    
    if passed == total:
        print("\n✓ 所有测试通过！插件可以正常使用。")
        print("\n使用说明:")
        print("1. 启动 Open Rails 游戏")
        print("2. 在游戏设置中启用 Web Server")
        print("3. 通过 AI 对话调用: '请启动 Open Rails 监控'")
    else:
        print(f"\n✗ {total - passed} 个测试失败")
        return 1
    
    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
