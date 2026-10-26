// Shared by both start paths: this notice appears when pairing becomes ready.
export class FirstUseNotice {
  constructor(config, save, show) {this.config = config; this.save = save; this.show = show; this.pending = false;}
  async tick(name) {
    if (!name || this.pending || this.config.wake_notice_seen === 1) return;
    this.pending = true;
    try {
      const accepted = await this.show({type: 'info', title: '首次使用：频道回复规则',
        message: this.config.wake_filter_enabled === false ? '当前已关闭称呼过滤，无需先叫名字即可回应。' : '发言中包含猫娘的当前称呼，回复才会播回 KOOK。',
        detail: `可在主界面“仅回应称呼”开关调整。开启时先叫当前猫娘称呼，再说想聊的内容；关闭时无需称呼。\n\n这是语音桥的规则，猫娘本体不要求唤醒词。当前称呼从猫娘设置自动同步，改名后请看主界面的“当前猫娘称呼”。\n\n免费模式下，其他发言也可能让本体回答，开启过滤时不会播回频道。本体出声不等于 KOOK 已收到；请看“最近发言”的发送状态。`,
        buttons: ['我知道了'], defaultId: 0, noLink: true});
      if (accepted.response === 0) {
        this.config.wake_notice_seen = 1;
        try {await this.save();} catch (error) {delete this.config.wake_notice_seen; throw error;}
      }
    } finally {this.pending = false;}
  }
}
