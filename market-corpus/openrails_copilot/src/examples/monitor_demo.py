"""
Open Rails Copilot Plugin - 实时监控演示
模拟插件的实时监控功能
"""

import asyncio
from datetime import datetime

import httpx


class MockMonitor:
    """模拟插件的监控逻辑"""
    
    def __init__(self):
        self.last_speed = 0.0
        self.last_throttle = 0.0
        self.last_brake = 0.0
        self.speed_change_threshold = 20.0
        self.emergency_brake_threshold = 0.8
    
    async def get_game_data(self, client):
        """获取游戏数据"""
        try:
            cab_response = await client.get("http://localhost:2150/API/CABCONTROLS")
            time_response = await client.get("http://localhost:2150/API/TIME")
            
            if cab_response.status_code == 200 and time_response.status_code == 200:
                cab_data = cab_response.json()
                game_time = float(time_response.text)
                
                parsed_data = {"GameTime": game_time}
                for control in cab_data:
                    type_name = control.get("TypeName", "")
                    range_fraction = control.get("RangeFraction", 0.0)
                    min_value = control.get("MinValue", 0.0)
                    max_value = control.get("MaxValue", 0.0)
                    
                    actual_value = min_value + (max_value - min_value) * range_fraction
                    parsed_data[type_name] = {
                        "value": actual_value,
                        "fraction": range_fraction,
                        "min": min_value,
                        "max": max_value
                    }
                
                return parsed_data
        except Exception as e:
            print(f"❌ 获取数据失败: {e}")
            return None
    
    def analyze_data(self, data):
        """分析数据并检测异常"""
        alerts = []
        
        # 提取关键数据
        speedometer = data.get("SPEEDOMETER", {})
        speed_kmh = speedometer.get("value", 0.0)
        
        throttle_data = data.get("THROTTLE", {})
        throttle = throttle_data.get("fraction", 0.0)
        
        brake_data = data.get("TRAIN_BRAKE", {})
        brake = brake_data.get("fraction", 0.0)
        
        speed_limit_data = data.get("SPEEDLIM_DISPLAY", {})
        speed_limit = speed_limit_data.get("value", 0.0)
        
        direction_data = data.get("DIRECTION", {})
        direction_value = direction_data.get("value", 1.0)
        
        overspeed_data = data.get("OVERSPEED", {})
        overspeed = overspeed_data.get("fraction", 0.0)
        
        # 检测急刹车
        if brake > self.emergency_brake_threshold:
            alerts.append({
                "type": "emergency_brake",
                "severity": "high",
                "message": f"🚨 检测到紧急制动！制动力：{brake*100:.1f}%",
            })
        
        # 检测速度剧变
        if self.last_speed > 0:
            speed_change = abs(speed_kmh - self.last_speed)
            if speed_change > self.speed_change_threshold:
                alerts.append({
                    "type": "speed_change",
                    "severity": "medium",
                    "message": f"⚠️ 速度急剧变化：从 {self.last_speed:.1f} km/h 到 {speed_kmh:.1f} km/h",
                })
        
        # 检测超速
        if speed_limit > 0 and speed_kmh > speed_limit:
            alerts.append({
                "type": "overspeed",
                "severity": "medium",
                "message": f"⚠️ 当前速度 {speed_kmh:.1f} km/h 超过限速 {speed_limit:.1f} km/h",
            })
        
        # 检测超速标志
        if overspeed > 0:
            alerts.append({
                "type": "overspeed_warning",
                "severity": "high",
                "message": "🚨 超速警告激活！",
            })
        
        # 更新历史数据
        self.last_speed = speed_kmh
        self.last_throttle = throttle
        self.last_brake = brake
        
        # 方向字符串
        direction_map = {0: "倒车", 1: "空档", 2: "前进"}
        direction_str = direction_map.get(int(direction_value), "未知")
        
        return {
            "speed_kmh": speed_kmh,
            "speed_limit_kmh": speed_limit,
            "throttle_percent": throttle * 100,
            "brake_percent": brake * 100,
            "direction": direction_str,
            "alerts": alerts,
            "game_time": data.get("GameTime", 0),
        }
    
    def format_game_time(self, seconds):
        """格式化游戏时间"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"


async def main():
    """主监控循环"""
    print("=" * 80)
    print("Open Rails Copilot Plugin - 实时监控演示")
    print("=" * 80)
    print("\n📋 提示：")
    print("  1. 确保 Open Rails 游戏正在运行")
    print("  2. 在游戏中操作列车（调整方向、油门、制动）")
    print("  3. 观察下方的实时数据和异常检测")
    print("  4. 按 Ctrl+C 停止监控")
    print("\n" + "=" * 80 + "\n")
    
    monitor = MockMonitor()
    
    async with httpx.AsyncClient(timeout=3.0) as client:
        # 检查连接
        try:
            response = await client.get("http://localhost:2150/API/CABCONTROLS")
            if response.status_code != 200:
                print("❌ 无法连接到 Open Rails API")
                return
            print("✅ 已连接到 Open Rails API\n")
        except Exception as e:
            print(f"❌ 无法连接到 Open Rails API: {e}")
            return
        
        sample_count = 0
        
        try:
            while True:
                sample_count += 1
                timestamp = datetime.now().strftime("%H:%M:%S")
                
                # 获取数据
                data = await monitor.get_game_data(client)
                if not data:
                    print(f"[{timestamp}] ⏸️  无法获取数据")
                    await asyncio.sleep(2)
                    continue
                
                # 分析数据
                analysis = monitor.analyze_data(data)
                
                # 格式化游戏时间
                game_time_str = monitor.format_game_time(analysis["game_time"])
                
                # 显示基础信息
                print(f"[{timestamp}] 游戏时间: {game_time_str} | 方向: {analysis['direction']}")
                print(f"           速度: {analysis['speed_kmh']:.1f} km/h (限速: {analysis['speed_limit_kmh']:.0f} km/h)")
                print(f"           油门: {analysis['throttle_percent']:.1f}% | 制动: {analysis['brake_percent']:.1f}%")
                
                # 显示警报
                if analysis["alerts"]:
                    print(f"\n{'':11}━━━ 异常警报 ━━━")
                    for alert in analysis["alerts"]:
                        severity_icon = "🚨" if alert["severity"] == "high" else "⚠️"
                        print(f"           {severity_icon} {alert['message']}")
                    print(f"{'':11}━━━━━━━━━━━━━━\n")
                else:
                    if sample_count % 5 == 0:  # 每 10 秒显示一次正常状态
                        print("           ✅ 列车运行正常\n")
                    else:
                        print()
                
                await asyncio.sleep(2)
                
        except KeyboardInterrupt:
            print("\n\n" + "=" * 80)
            print("监控已停止")
            print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
