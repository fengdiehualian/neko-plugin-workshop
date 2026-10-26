import {randomUUID} from 'node:crypto';

export class IdentityDirectory {
  constructor() {this.reset();}
  reset() {this.epoch = randomUUID(); this.peers = new Map(); return this.epoch;}
  upsert(peerId, nickname = '') {
    const id = typeof peerId === 'string' ? peerId : '';
    if (!id || id.length > 128) throw new Error('INVALID_PEER');
    const old = this.peers.get(id);
    const p = {presence_id: randomUUID(), ...old, peer_id: id, display_name: String(nickname || old?.display_name || '未知成员').slice(0, 80)};
    this.peers.set(id, p); return p;
  }
  bind(peerId, userId, nickname, epoch) {
    if (epoch !== this.epoch || !this.peers.has(peerId)) throw new Error('STALE_PEER');
    if (typeof userId !== 'string' || !userId || userId.length > 128) throw new Error('INVALID_USER');
    if (typeof nickname !== 'string' || !nickname.trim() || nickname.length > 80) throw new Error('INVALID_NICKNAME');
    for (const p of this.peers.values()) if (p.user_id === userId && p.peer_id !== peerId) throw new Error('USER_ALREADY_BOUND');
    // An operator-confirmed association is explicitly different from platform-proven mapping.
    const p = {...this.upsert(peerId, nickname), user_id: String(userId), identity_source: 'operator_confirmed', identity_verified: false};
    this.peers.set(peerId, p); return p;
  }
  remove(peerId) {this.peers.delete(String(peerId));}
  rtc(peerId, nickname) {
    const p = this.upsert(peerId, nickname);
    this.peers.set(peerId, {...p, identity_source: 'rtc_signaling', audio_source_verified: true});
  }
  resolve(peerId) {
    const p = this.peers.get(String(peerId));
    return {peer_id: String(peerId), presence_id: p?.presence_id, user_id: p?.user_id || null, display_name: p?.display_name || '未知成员', identity_source: p?.identity_source || 'rtc_peer_unverified', audio_source_verified: p?.audio_source_verified === true, identity_verified: false};
  }
}
