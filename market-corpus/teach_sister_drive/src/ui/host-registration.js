// Plugin-owned trusted page host: Unity owns its avatar. The SDK's character
// discovery/binding contract requires avatar-renderer; no web avatar is mounted.
(() => {
  const node = document.getElementById('neko-minigame-host-launch');
  Object.defineProperty(node, 'nekoCapabilityProviders', {value:Object.freeze({
    'teach-sister-drive':Object.freeze({avatarHostFactory({characterSource}) {
      return Object.freeze({
        ...characterSource,
        mount() { throw new Error('This integration renders its character in Unity'); },
        dispose() {},
      });
    }}),
  })});
})();
