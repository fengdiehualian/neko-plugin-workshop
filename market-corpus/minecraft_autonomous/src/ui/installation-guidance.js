const backend = {
  version_matches_not_integrity_verified: '版本匹配；仍须完整性校验',
  version_mismatch: '版本不匹配，请安装要求的核心版本',
  metadata_unavailable: '元数据暂不可用，请检查后端目录',
};
const viewer = {
  text_only: '未配置本地渲染器与资源；可查看文字观测',
  renderer_catalog_pair_required: '渲染器与资源 catalog 须一起配置',
  renderer_missing: '本地渲染器缺失，请检查目录',
  catalog_invalid_or_missing: '本地资源 catalog 缺失或无效',
  catalog_version_missing_or_ambiguous: 'catalog 中缺少唯一匹配的版本资源',
  version_resources_invalid_or_missing: '本地版本资源缺失或无效',
  metadata_matches_ready_to_attempt_rendering: '本地元数据匹配；实际画面仍待核验',
};
const known = (table, code) => Object.hasOwn(table, code) ? table[code] : '状态暂不可用，请检查配置与安装说明';
const prepared = {
  prepared: '完整离线运行环境已准备；显式启动时校验完整性。',
  missing: '离线运行环境尚未准备。解压完整离线包，双击 prepare.cmd，然后返回此页。',
  incompatible: '离线运行环境不匹配或已损坏。先完整解压新版包并双击 prepare.cmd；旧版已保存的路径可用下方按钮重新选择。',
  custom: '正在使用明确配置的自定义运行环境；不会混入离线包默认路径。',
  external: '外部观察模式：运行环境由独立执行器管理。',
};
export function preparedRuntimeSelection(settings, installation) {
  if (installation?.prepared_runtime !== 'incompatible' || !settings?.runtime?.bundle_root
      || settings.runtime.mode === 'external' || settings?.viewer?.url) return null;
  return {
    backend:{node:'',directory:''},
    viewer:{renderer_directory:'',catalog_file:'',bundled_appearance_catalog_file:''},
    runtime:{bundle_root:'',bundle_manifest_sha256:''},
  };
}
export function installationGuidance(installation, external, viewerUrl) {
  return {
    preparedRuntime: known(prepared, installation?.prepared_runtime),
    backend: known(backend, installation?.backend),
    localResources: known(viewer, installation?.viewer),
    externalView: external || !!viewerUrl
      ? '实际渲染器与资源由外部执行器管理；请在实际观战页面核验画面、资源和角色绑定。'
      : '未配置观战时可使用文字模式；请按安装说明准备匹配资源。',
  };
}
