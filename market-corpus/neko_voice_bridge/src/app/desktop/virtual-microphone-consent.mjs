// Consent concerns the app's generated audio, never access to a hardware mic.
export async function ensureVirtualMicrophoneConsent(config, save, show) {
  if (config.virtual_microphone_consent === 1) return true;
  const result = await show({type: 'question', title: '允许 KOOK 使用猫娘虚拟麦克风',
    message: '允许此 KOOK 窗口使用内置虚拟麦克风吗？',
    detail: '它只发送猫娘回复和你点击播放的测试提示音，不采集电脑麦克风。允许后，KOOK 的输入设备会显示“猫娘语音桥（内置虚拟麦克风）”。无需安装虚拟声卡。',
    buttons: ['允许使用', '暂不使用'], defaultId: 0, cancelId: 1, noLink: true});
  if (result.response !== 0) return false;
  config.virtual_microphone_consent = 1;
  try {await save();} catch (e) {delete config.virtual_microphone_consent; throw e;}
  return true;
}
