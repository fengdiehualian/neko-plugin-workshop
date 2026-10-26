"""
测试 Open Rails 真实 API 连接
"""

import asyncio
from datetime import datetime

import httpx


async def test_api():
    """测试真实的 Open Rails API"""
    
    print("=" * 60)
    print("Open Rails 真实 API 测试")
    print("=" * 60)
    
    api_base_url = "http://localhost:2150"
    
    async with httpx.AsyncClient(timeout=3.0) as client:
        
        # 测试 1: 检查 API 可用性
        print("\n测试 1: API 可用性")
        try:
            response = await client.get(f"{api_base_url}/API/CABCONTROLS")
            if response.status_code == 200:
                print("✓ API 连接成功")
            else:
                print(f"✗ API 返回状态码: {response.status_code}")
                return
        except Exception as e:
            print(f"✗ API 连接失败: {e}")
            return
        
        # 测试 2: 获取驾驶舱数据
        print("\n测试 2: 获取驾驶舱控制数据")
        try:
            response = await client.get(f"{api_base_url}/API/CABCONTROLS")
            cab_data = response.json()
            print(f"✓ 获取到 {len(cab_data)} 个控制项")
            
            # 解析关键数据
            parsed_data = {}
            for control in cab_data:
                type_name = control.get("TypeName", "")
                range_fraction = control.get("RangeFraction", 0.0)
                min_value = control.get("MinValue", 0.0)
                max_value = control.get("MaxValue", 0.0)
                
                # 计算实际值
                actual_value = min_value + (max_value - min_value) * range_fraction
                parsed_data[type_name] = {
                    "value": actual_value,
                    "fraction": range_fraction,
                    "min": min_value,
                    "max": max_value
                }
            
            print("\n关键控制数据:")
            
            # 速度
            if "SPEEDOMETER" in parsed_data:
                speed = parsed_data["SPEEDOMETER"]["value"]
                print(f"  速度: {speed:.1f} km/h")
            
            # 油门
            if "THROTTLE" in parsed_data:
                throttle = parsed_data["THROTTLE"]["fraction"] * 100
                print(f"  油门: {throttle:.1f}%")
            
            # 制动
            if "TRAIN_BRAKE" in parsed_data:
                brake = parsed_data["TRAIN_BRAKE"]["fraction"] * 100
                print(f"  制动: {brake:.1f}%")
            
            # 限速
            if "SPEEDLIM_DISPLAY" in parsed_data:
                speed_limit = parsed_data["SPEEDLIM_DISPLAY"]["value"]
                print(f"  限速: {speed_limit:.1f} km/h")
            
            # 方向
            if "DIRECTION" in parsed_data:
                direction = parsed_data["DIRECTION"]["value"]
                direction_str = {0: "倒车", 1: "空档", 2: "前进"}.get(int(direction), "未知")
                print(f"  方向: {direction_str}")
            
            # 加速度
            if "ACCELEROMETER" in parsed_data:
                accel = parsed_data["ACCELEROMETER"]["value"]
                print(f"  加速度: {accel:.2f}")
            
            # 超速警告
            if "OVERSPEED" in parsed_data:
                overspeed = parsed_data["OVERSPEED"]["fraction"]
                print(f"  超速警告: {'是' if overspeed > 0 else '否'}")
            
        except Exception as e:
            print(f"✗ 解析数据失败: {e}")
            return
        
        # 测试 3: 获取游戏时间
        print("\n测试 3: 获取游戏时间")
        try:
            response = await client.get(f"{api_base_url}/API/TIME")
            game_time_seconds = float(response.text)
            
            # 转换为时:分:秒
            hours = int(game_time_seconds // 3600)
            minutes = int((game_time_seconds % 3600) // 60)
            seconds = int(game_time_seconds % 60)
            
            print(f"✓ 游戏时间: {hours:02d}:{minutes:02d}:{seconds:02d}")
            
        except Exception as e:
            print(f"✗ 获取时间失败: {e}")
        
        # 测试 4: 监控循环测试（3次）
        print("\n测试 4: 监控循环测试 (3 次采样)")
        for i in range(3):
            print(f"\n--- 采样 {i+1}/3 ---")
            try:
                response = await client.get(f"{api_base_url}/API/CABCONTROLS")
                cab_data = response.json()
                
                # 快速解析
                speed = 0.0
                throttle = 0.0
                brake = 0.0
                
                for control in cab_data:
                    type_name = control.get("TypeName", "")
                    range_fraction = control.get("RangeFraction", 0.0)
                    min_value = control.get("MinValue", 0.0)
                    max_value = control.get("MaxValue", 0.0)
                    actual_value = min_value + (max_value - min_value) * range_fraction
                    
                    if type_name == "SPEEDOMETER":
                        speed = actual_value
                    elif type_name == "THROTTLE":
                        throttle = range_fraction * 100
                    elif type_name == "TRAIN_BRAKE":
                        brake = range_fraction * 100
                
                timestamp = datetime.now().strftime("%H:%M:%S")
                print(f"[{timestamp}] 速度: {speed:.1f} km/h | 油门: {throttle:.1f}% | 制动: {brake:.1f}%")
                
                await asyncio.sleep(2)
                
            except Exception as e:
                print(f"✗ 采样失败: {e}")
                break
    
    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)
    print("\n插件已准备就绪，可以在 N.E.K.O 中使用以下命令:")
    print("  - '请启动 Open Rails 监控'")
    print("  - 'Open Rails 现在的状态如何？'")
    print("  - '停止 Open Rails 监控'")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_api())
