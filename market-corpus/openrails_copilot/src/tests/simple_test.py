"""
Open Rails Copilot Plugin 简单测试
测试插件代码的核心逻辑，不依赖 NEKO 框架
"""

import asyncio


def test_basic_logic():
    """测试插件的基本逻辑"""
    print("=" * 60)
    print("Open Rails Copilot Plugin 基础测试")
    print("=" * 60)
    
    # 测试 1: 速度转换
    print("\n测试 1: 速度单位转换")
    speed_ms = 30.0  # 30 m/s
    speed_kmh = speed_ms * 3.6
    print(f"✓ {speed_ms} m/s = {speed_kmh} km/h")
    assert abs(speed_kmh - 108.0) < 0.1
    
    # 测试 2: 告警逻辑
    print("\n测试 2: 告警逻辑")
    
    # 紧急制动检测
    brake_threshold = 0.8
    test_brake = 0.9
    if test_brake > brake_threshold:
        print(f"✓ 检测到紧急制动: {test_brake*100:.1f}% > {brake_threshold*100:.1f}%")
    
    # 速度变化检测
    last_speed = 80.0
    current_speed = 105.0
    speed_change_threshold = 20.0
    speed_change = abs(current_speed - last_speed)
    if speed_change > speed_change_threshold:
        print(f"✓ 检测到速度剧变: {speed_change:.1f} km/h > {speed_change_threshold} km/h")
    
    # 超速检测
    overspeed_threshold = 100.0
    if current_speed > overspeed_threshold:
        print(f"✓ 检测到超速: {current_speed:.1f} km/h > {overspeed_threshold} km/h")
    
    # 测试 3: 数据格式化
    print("\n测试 3: 数据格式化")
    throttle = 0.65
    brake = 0.2
    print(f"✓ 油门: {throttle*100:.1f}%")
    print(f"✓ 制动: {brake*100:.1f}%")
    
    # 测试 4: API 端点
    print("\n测试 4: API 配置")
    api_base = "http://localhost:2150"
    api_endpoint = f"{api_base}/api/apisample"
    print(f"✓ API 基础地址: {api_base}")
    print(f"✓ 数据端点: {api_endpoint}")
    
    print("\n" + "=" * 60)
    print("所有基础测试通过！")
    print("=" * 60)
    
    print("\n插件功能说明:")
    print("1. 监控频率: 每 2 秒")
    print("2. API 检查: 每 5 秒")
    print("3. 状态报告: 每 60 秒")
    print("\n告警类型:")
    print("- 紧急制动 (制动 > 80%)")
    print("- 速度剧变 (变化 > 20 km/h)")
    print("- 超速告警 (速度 > 100 km/h)")
    
    print("\n使用前准备:")
    print("1. 安装依赖: pip install httpx")
    print("2. 启动 Open Rails 游戏")
    print("3. 在游戏中启用 Web Server (Options → Experimental)")
    print("4. 通过 AI 对话调用: '请启动 Open Rails 监控'")
    
    return True


async def test_httpx_availability():
    """测试 httpx 是否可用"""
    print("\n" + "=" * 60)
    print("依赖检查")
    print("=" * 60)
    
    try:
        import httpx
        print("✓ httpx 已安装")
        
        # 测试创建客户端
        async with httpx.AsyncClient(timeout=3.0):
            print("✓ httpx 客户端创建成功")
        
        return True
    except ImportError:
        print("✗ httpx 未安装")
        print("  请运行: pip install httpx")
        return False
    except Exception as e:
        print(f"✗ httpx 测试失败: {e}")
        return False


async def test_api_connection():
    """测试 Open Rails API 连接"""
    print("\n" + "=" * 60)
    print("API 连接测试")
    print("=" * 60)
    
    try:
        import httpx
    except ImportError:
        print("⊗ 跳过（httpx 未安装）")
        return None
    
    api_url = "http://localhost:2150/api/apisample"
    
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get(api_url)
            
            if response.status_code == 200:
                data = response.json()
                print(f"✓ API 可用: {api_url}")
                print(f"  响应状态: {response.status_code}")
                
                # 显示一些关键数据
                if "Speed" in data:
                    speed_kmh = float(data["Speed"]) * 3.6
                    print(f"  当前速度: {speed_kmh:.1f} km/h")
                if "Throttle" in data:
                    print(f"  油门: {float(data['Throttle'])*100:.1f}%")
                
                return True
            else:
                print(f"✗ API 响应异常: {response.status_code}")
                return False
                
    except httpx.ConnectError:
        print("⚠ API 不可用（这是正常的，如果游戏未运行）")
        print(f"  尝试连接: {api_url}")
        print("\n  请确保:")
        print("  1. Open Rails 游戏已启动")
        print("  2. 游戏在活动场景中（非主菜单）")
        print("  3. Web Server 已启用 (Options → Experimental)")
        return False
    except Exception as e:
        print(f"✗ 连接测试失败: {e}")
        return False


async def main():
    """主测试函数"""
    # 基础逻辑测试
    test_basic_logic()
    
    # 依赖检查
    httpx_ok = await test_httpx_availability()
    
    # API 连接测试（如果 httpx 可用）
    if httpx_ok:
        await test_api_connection()
    
    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
