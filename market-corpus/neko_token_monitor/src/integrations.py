"""Common capability description; each provider uses its supported native API."""
ADAPTERS = {
    'deepseek_harness': {'reader':'harness_account_bridge','kind':'money','application':'harness_native_view','website':True},
    'codex': {'reader':'codex_app_server','kind':'quota_percent','application':None,'website':True},
    'trae': {'reader':'macos_accessibility','kind':'credits','application':'macos_accessibility','website':True},
    'workbuddy': {'reader':'macos_accessibility','kind':'credits','application':'macos_accessibility','website':True},
}


def info(profile):
    return dict(ADAPTERS[profile]) if profile in ADAPTERS else None
