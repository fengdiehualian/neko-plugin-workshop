"""
快速测试 - 获取一次当前游戏状态
"""

import asyncio

import httpx


async def quick_test():
    """快速测试当前状态"""
    
    print("\n" + "=" * 60)
    print("Open Rails 当前状态快照")
    print("=" * 60 + "\n")
    
    async with httpx.AsyncClient(timeout=3.0) as client:
        try:
            # 获取数据
            cab_response = await client.get("http://localhost:2150/API/CABCONTROLS")
            time_response = await client.get("http://localhost:2150/API/TIME")
            
            if cab_response.status_code != 200:
                print("❌ 无法连接到 Open Rails API")
                return
            
            cab_data = cab_response.json()
            game_time = float(time_response.text)
            
            # 解析关键数据
            speed = 0.0
            throttle = 0.0
            brake = 0.0
            speed_limit = 0.0
            direction = 1.0
            overspeed = 0.0
            accel = 0.0
            
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
                elif type_name == "SPEEDLIM_DISPLAY":
                    speed_limit = actual_value
                elif type_name == "DIRECTION":
                    direction = actual_value
                elif type_name == "OVERSPEED":
                    overspeed = range_fraction
                elif type_name == "ACCELEROMETER":
                    accel = actual_value
            
            # 格式化游戏时间
            hours = int(game_time // 3600)
            minutes = int((game_time % 3600) // 60)
            seconds = int(game_time % 60)
            game_time_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            
            # 方向
            direction_map = {0: "倒车", 1: "空档", 2: "前进"}
            direction_str = direction_map.get(int(direction), "未知")
            
            # 显示状态
            print(f"🕐 游戏时间:    {game_time_str}")
            print(f"🚂 方向:        {direction_str}")
            print(f"📊 速度:        {speed:.1f} km/h")
            print(f"🚥 限速:        {speed_limit:.0f} km/h")
            print(f"⚡ 油门:        {throttle:.1f}%")
            print(f"🛑 制动:        {brake:.1f}%")
            print(f"📈 加速度:      {accel:.2f}")
            print(f"⚠️  超速警告:    {'是' if overspeed > 0 else '否'}")
            
            # 状态评估
            print("\n" + "-" * 60)
            print("状态评估:")
            
            if speed < 1.0:
                print("  ⏸️  列车静止")
            elif speed > speed_limit and speed_limit > 0:
                print(f"  🚨 超速！当前 {speed:.1f} km/h，限速 {speed_limit:.0f} km/h")
            else:
                print("  ✅ 列车正常运行")
            
            if brake > 80:
                print(f"  🚨 紧急制动中！制动力 {brake:.1f}%")
            elif brake > 0:
                print(f"  🛑 正在制动 ({brake:.1f}%)")
            
            if throttle > 0:
                print(f"  ⚡ 正在加速 (油门 {throttle:.1f}%)")
            
            print("\n" + "=" * 60 + "\n")
            
        except Exception as e:
            print(f"❌ 错误: {e}\n")


if __name__ == "__main__":
    asyncio.run(quick_test())
