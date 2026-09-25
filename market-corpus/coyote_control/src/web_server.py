"""
独立的 9006 Web 面板服务器（备用方案）
提供状态查看和测试功能，不依赖托管 UI
"""
import asyncio
import logging
from typing import Optional

from aiohttp import web


class CoyoteWebServer:
    """郊狼控制面板 HTTP 服务器"""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 9006,
        dglab_server=None,
        logger: Optional[logging.Logger] = None,
    ):
        self.host = host
        self.port = port
        self.dglab_server = dglab_server
        self.logger = logger or logging.getLogger(__name__)
        
        self.app: Optional[web.Application] = None
        self.runner: Optional[web.AppRunner] = None
        self.site: Optional[web.TCPSite] = None
        self.running = False

    async def start(self):
        """启动 Web 服务器"""
        self.app = web.Application()
        self.app.router.add_get("/", self.handle_index)
        self.app.router.add_get("/api/status", self.handle_status)
        self.app.router.add_post("/api/test", self.handle_test)

        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        
        self.site = web.TCPSite(self.runner, self.host, self.port)
        await self.site.start()
        
        self.running = True
        self.logger.info(f"Web server started on http://{self.host}:{self.port}")

    async def stop(self):
        """停止 Web 服务器"""
        self.running = False
        if self.site:
            await self.site.stop()
        if self.runner:
            await self.runner.cleanup()
        self.logger.info("Web server stopped")

    def _get_local_ip(self) -> str:
        """获取本机 IP"""
        try:
            import socket
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.connect(("8.8.8.8", 80))
                return sock.getsockname()[0]
            finally:
                sock.close()
        except Exception:
            return "127.0.0.1"

    async def handle_index(self, request: web.Request) -> web.Response:
        """主页面"""
        html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>郊狼控制面板</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #F7F4EF;
            color: #1F2421;
            padding: 20px;
            line-height: 1.6;
        }
        .container {
            max-width: 800px;
            margin: 0 auto;
        }
        h1 {
            font-family: "Playfair Display", serif;
            font-size: 2.5rem;
            font-weight: 400;
            margin-bottom: 10px;
            letter-spacing: -0.02em;
        }
        h1 em {
            color: #C4612F;
            font-style: italic;
        }
        .subtitle {
            color: #5C635D;
            font-size: 0.95rem;
            margin-bottom: 30px;
        }
        .card {
            background: #FFFFFF;
            border: 1px solid #E7E1D7;
            border-radius: 12px;
            padding: 24px;
            margin-bottom: 20px;
            box-shadow: 0 1px 3px rgba(31, 36, 33, 0.05);
        }
        .card h2 {
            font-size: 1.1rem;
            font-weight: 500;
            margin-bottom: 16px;
            color: #1F2421;
        }
        .status-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 10px 0;
            border-bottom: 1px solid #E7E1D7;
        }
        .status-row:last-child {
            border-bottom: none;
        }
        .status-label {
            font-weight: 500;
            color: #1F2421;
        }
        .status-value {
            color: #5C635D;
            font-family: "SF Mono", Monaco, monospace;
            font-size: 0.9rem;
        }
        .badge {
            display: inline-block;
            padding: 4px 12px;
            border-radius: 999px;
            font-size: 0.85rem;
            font-weight: 500;
        }
        .badge-success {
            background: #D4EDDA;
            color: #155724;
        }
        .badge-warning {
            background: #FFF3CD;
            color: #856404;
        }
        .badge-error {
            background: #F8D7DA;
            color: #721C24;
        }
        .qr-container {
            text-align: center;
            padding: 20px;
        }
        .qr-container img {
            border: 4px solid #E7E1D7;
            border-radius: 12px;
            max-width: 280px;
        }
        .qr-text {
            margin-top: 12px;
            font-size: 0.85rem;
            color: #5C635D;
            word-break: break-all;
            font-family: "SF Mono", Monaco, monospace;
        }
        button {
            background: #C4612F;
            color: #FFFFFF;
            border: none;
            padding: 12px 32px;
            border-radius: 999px;
            font-size: 1rem;
            font-weight: 500;
            cursor: pointer;
            transition: all 0.2s;
            width: 100%;
        }
        button:hover {
            background: #A94E22;
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(196, 97, 47, 0.3);
        }
        button:disabled {
            background: #E7E1D7;
            color: #5C635D;
            cursor: not-allowed;
            transform: none;
        }
        .client-card {
            background: #FBF9F5;
            border: 1px solid #E7E1D7;
            padding: 12px;
            border-radius: 8px;
            margin-bottom: 10px;
        }
        .client-id {
            font-weight: 600;
            color: #1F2421;
            margin-bottom: 6px;
        }
        .client-info {
            font-size: 0.9rem;
            color: #5C635D;
        }
        .empty-state {
            text-align: center;
            padding: 40px 20px;
            color: #5C635D;
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>郊狼<em>控制</em>面板</h1>
        <p class="subtitle">DG-Lab 设备管理与监控</p>

        <div class="card">
            <h2>服务器状态</h2>
            <div class="status-row">
                <span class="status-label">服务器状态</span>
                <span id="server-status" class="badge badge-warning">加载中...</span>
            </div>
            <div class="status-row">
                <span class="status-label">WebSocket 地址</span>
                <span id="ws-url" class="status-value">-</span>
            </div>
            <div class="status-row">
                <span class="status-label">本机 IP</span>
                <span id="local-ip" class="status-value">-</span>
            </div>
        </div>

        <div class="card">
            <h2>连接二维码</h2>
            <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px;">
                <div>
                    <div style="text-align: center; margin-bottom: 8px;">
                        <span class="badge" style="background: #F2E3D6; color: #C4612F;">DG-Lab V4</span>
                    </div>
                    <div id="qr-container-v4" class="qr-container">
                        <p class="empty-state">加载中...</p>
                    </div>
                </div>
                <div>
                    <div style="text-align: center; margin-bottom: 8px;">
                        <span class="badge" style="background: #F2E3D6; color: #C4612F;">DG-Lab 3 (兼容)</span>
                    </div>
                    <div id="qr-container-v3" class="qr-container">
                        <p class="empty-state">加载中...</p>
                    </div>
                </div>
            </div>
        </div>

        <div class="card">
            <h2>已连接设备</h2>
            <div id="clients-container">
                <p class="empty-state">加载中...</p>
            </div>
        </div>

        <div class="card">
            <h2>测试连接</h2>
            <p class="subtitle" style="margin-bottom: 16px;">发送 10 功率脉冲波形，持续 5 秒</p>
            <button id="test-btn" onclick="testConnection()">发送测试脉冲</button>
            <p id="test-result" class="subtitle" style="margin-top: 12px; text-align: center;"></p>
        </div>
    </div>

    <script>
        async function fetchStatus() {
            try {
                const response = await fetch('/api/status');
                const data = await response.json();
                
                // 更新服务器状态
                const statusBadge = document.getElementById('server-status');
                if (!data.server_running) {
                    statusBadge.className = 'badge badge-error';
                    statusBadge.textContent = '服务器未启动';
                } else if (data.connected && data.connected_clients.length > 0) {
                    statusBadge.className = 'badge badge-success';
                    statusBadge.textContent = '已连接';
                } else {
                    statusBadge.className = 'badge badge-warning';
                    statusBadge.textContent = '等待连接';
                }
                
                document.getElementById('ws-url').textContent = data.ws_url || '-';
                document.getElementById('local-ip').textContent = data.local_ip || '-';
                
                // 更新 V4 二维码
                const qrContainerV4 = document.getElementById('qr-container-v4');
                if (data.qr_code_data_v4) {
                    const qrUrlV4 = `https://api.qrserver.com/v1/create-qr-code/?size=240x240&data=${encodeURIComponent(data.qr_code_data_v4)}`;
                    qrContainerV4.innerHTML = `
                        <img src="${qrUrlV4}" alt="V4 二维码" style="max-width: 240px;" />
                        <p class="qr-text" style="font-size: 0.75rem;">${data.qr_code_data_v4.substring(0, 50)}...</p>
                    `;
                } else {
                    qrContainerV4.innerHTML = '<p class="empty-state">不可用</p>';
                }
                
                // 更新 V3 二维码
                const qrContainerV3 = document.getElementById('qr-container-v3');
                if (data.qr_code_data_v3) {
                    const qrUrlV3 = `https://api.qrserver.com/v1/create-qr-code/?size=240x240&data=${encodeURIComponent(data.qr_code_data_v3)}`;
                    qrContainerV3.innerHTML = `
                        <img src="${qrUrlV3}" alt="V3 二维码" style="max-width: 240px;" />
                        <p class="qr-text" style="font-size: 0.75rem;">${data.qr_code_data_v3.substring(0, 50)}...</p>
                    `;
                } else {
                    qrContainerV3.innerHTML = '<p class="empty-state">不可用</p>';
                }
                
                // 更新设备列表
                const clientsContainer = document.getElementById('clients-container');
                if (data.connected_clients && data.connected_clients.length > 0) {
                    clientsContainer.innerHTML = data.connected_clients.map(client => `
                        <div class="client-card">
                            <div class="client-id">设备: ${client.client_id.substring(0, 8)}...</div>
                            <div class="client-info">
                                ${client.battery !== undefined ? `电量: ${client.battery}% ` : ''}
                                ${client.signal_strength !== undefined ? `信号: ${client.signal_strength}` : ''}
                            </div>
                        </div>
                    `).join('');
                } else {
                    clientsContainer.innerHTML = '<p class="empty-state">无已连接设备</p>';
                }
                
                // 更新测试按钮状态
                const testBtn = document.getElementById('test-btn');
                testBtn.disabled = !data.connected;
                
            } catch (error) {
                console.error('获取状态失败:', error);
            }
        }
        
        async function testConnection() {
            const testBtn = document.getElementById('test-btn');
            const testResult = document.getElementById('test-result');
            
            testBtn.disabled = true;
            testResult.textContent = '发送中...';
            
            try {
                const response = await fetch('/api/test', { method: 'POST' });
                const data = await response.json();
                
                if (data.success) {
                    testResult.textContent = '✓ ' + data.message;
                    testResult.style.color = '#155724';
                } else {
                    testResult.textContent = '✗ ' + data.error;
                    testResult.style.color = '#721C24';
                }
            } catch (error) {
                testResult.textContent = '✗ 请求失败';
                testResult.style.color = '#721C24';
            }
            
            setTimeout(() => {
                testBtn.disabled = false;
                testResult.textContent = '';
            }, 3000);
        }
        
        // 初始加载
        fetchStatus();
        
        // 每 3 秒刷新
        setInterval(fetchStatus, 3000);
    </script>
</body>
</html>"""
        return web.Response(text=html, content_type="text/html")

    async def handle_status(self, request: web.Request) -> web.Response:
        """状态 API"""
        try:
            server_running = bool(self.dglab_server and self.dglab_server.is_running())
            connected = self.dglab_server.has_bound_app() if server_running else False
            connected_clients = self.dglab_server.get_clients_detail() if server_running else []
            
            local_ip = self._get_local_ip()
            server_port = self.dglab_server.port if self.dglab_server else 9998
            ws_url = f"ws://{local_ip}:{server_port}"
            
            qr_code_data_v4 = ""
            qr_code_data_v3 = ""
            if server_running:
                # V4 二维码
                qr_code_data_v4 = self.dglab_server.get_qrcode_url(ws_url)
                # V3 兼容二维码
                qr_code_data_v3 = self.dglab_server.get_qrcode_url_v3(ws_url)
            
            data = {
                "server_running": server_running,
                "connected": connected,
                "connected_clients": connected_clients,
                "qr_code_data_v4": qr_code_data_v4,
                "qr_code_data_v3": qr_code_data_v3,
                "ws_url": ws_url,
                "local_ip": local_ip,
                "server_port": server_port,
            }
            
            return web.json_response(data)
        except Exception as error:
            self.logger.error(f"Status API error: {error}")
            return web.json_response({"error": str(error)}, status=500)

    async def handle_test(self, request: web.Request) -> web.Response:
        """测试连接 API"""
        try:
            if not self.dglab_server or not self.dglab_server.has_bound_app():
                return web.json_response({
                    "success": False,
                    "error": "设备未连接"
                })
            
            # 生成 10 功率的 PULSE 波形，持续 5 秒
            from .waveforms import get_waveform_frames
            
            frames = get_waveform_frames("PULSE", intensity=10)
            duration_ms = 5000
            
            # dglab_server 跑在另一个事件循环上，必须用
            # run_coroutine_threadsafe 投递，不能直接 await（跨循环会炸）
            server_loop = getattr(self.dglab_server, "loop", None)
            if server_loop is None:
                raise RuntimeError("WebSocket 服务器事件循环未就绪")
            future = asyncio.run_coroutine_threadsafe(
                self.dglab_server.add_pulses(
                    channel="both",
                    waveform_frames=frames,
                    duration_ms=duration_ms,
                ),
                server_loop,
            )
            await asyncio.wait_for(asyncio.wrap_future(future), timeout=5.0)
            
            self.logger.info("Test pulse sent via web API: 10 power, 5s")
            
            return web.json_response({
                "success": True,
                "message": "测试波形已发送（10功率，5秒）"
            })
            
        except Exception as error:
            self.logger.error(f"Test API error: {error}")
            return web.json_response({
                "success": False,
                "error": str(error)
            }, status=500)
