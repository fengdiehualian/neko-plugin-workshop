# 水面第二稿：实机评审

第一稿单图预览获得认可，但铺满实机水池后，密集闭合的明亮光网与深色斑块产生细胞／鳞片观感，整张贴图平移进一步像一层膜。第一稿不再作为默认资源，原文件保留。

第二稿由内置 ImageGen 重新生成，完整提示见 `water-surface-concept-v2.json`，原图为 `water-surface-concept-v2.png`，运行副本为 `../../static/assets/summer-pool-water-v2.png`。降低色块与光纹对比、减少密集亮边，但生成结果仍有宽缓网状结构；这是待用户实机评审的候选，不能声称完全消除了网状纹理。

接入入口为 `static/assets/summer-pool.json` 的 `waterSurface.image`；替换水图不影响物理场地、角色半径、出生点、UI。640×320 像素网格以最近邻取样。去掉第一稿整幅平移，猫猫周围涟漪和碰撞水花保持原逻辑。加载失败仍回退原池图；URL 参数 `water=classic` 可查看原水面。

实机完整画面：`water-implemented-v2.png`。采用 1920×960 浏览器内容区截图，没有将水图后期贴到截图中；另检查 1536×824 窗口。此轮仅调整水资源与绘制，不改变猫猫、UI、阵型或对战规则。未提交或推送 GitHub。
