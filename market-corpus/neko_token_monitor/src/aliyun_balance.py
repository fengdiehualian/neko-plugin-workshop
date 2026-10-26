"""Use the official CLI's configured RAM identity for the read-only account API."""
import json
import re
import shutil
import subprocess
from pathlib import Path


def query(profile=''):
    if not isinstance(profile, str) or not re.fullmatch(r'[A-Za-z0-9_.-]{0,80}', profile):
        raise ValueError('阿里云 CLI 配置名无效')
    candidates = [shutil.which('aliyun'), '/opt/homebrew/bin/aliyun', '/usr/local/bin/aliyun']
    executable = next((p for p in candidates if p and Path(p).is_file()), None)
    if not executable:
        raise ValueError('请先安装阿里云官方 CLI 并配置只读账务身份；DashScope 模型 Key 不能查询阿里云账务')
    command = [executable, 'bssopenapi', 'QueryAccountBalance']
    if profile:
        command += ['--profile', profile]
    try:
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, timeout=15)
        if result.returncode or len(result.stdout) > 1048576:
            raise ValueError()
        payload = json.loads(result.stdout)
        if payload.get('Success') is not True:
            raise ValueError()
        return payload
    except Exception:
        raise ValueError('阿里云余额查询失败，请检查 CLI 登录、只读账务权限和网络；保留上次数据') from None
