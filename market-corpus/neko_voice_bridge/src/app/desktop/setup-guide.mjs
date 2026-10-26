// Finishing setup requires current-session RTP evidence AND the user's own
// confirmation that a second account heard it. RTP alone is not a remote ACK.
export function setupStatus(config, bridge, browser, cable, events, open) {
  const connected = !!bridge.plugin?.connected && !!bridge.plugin?.compatibility?.ok;
  const outputReady = config.output_transport !== 'cable' || cable.ready;
  const sent = events.some(e => e.epoch === bridge.epoch && e.type === 'playback.finished' && e.meaning === 'outbound_rtp_verified');
  const canFinish = connected && !!config.setup_output_confirmed && outputReady && !!browser.connected && sent;
  return {open, completed: config.setup_completed === 1, can_finish: canFinish,
    steps: [connected, !!config.setup_output_confirmed && outputReady, !!browser.connected, sent]};
}
