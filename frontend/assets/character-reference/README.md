# VortoOne 角色参考素材

造型基准是用户明确保留的 `exports/vortoone-character-2026-11-14/voxel-family/03-pixel-slime-v3-full-crown.png`，SHA-256 为 `c52e8895efd061c82860227c78e022c52f083efc5405d87a5b55a96941ddab43`。原设计稿未覆盖。

本目录四份透明 PNG 是用内置 imagegen 对 v3 做背景提取所得的候选参考。输入均为 `apps/web/public/vortoone/character-family.png`；输出分别是 `general-extracted.png`、`research-extracted.png`、`developer-extracted.png`、`schedule-extracted.png`。保留了生成结果，未用后处理代码改写像素。

用户随后明确要求自定义颜色、切换表情和配饰。上面四张完整角色 PNG 只作参考。`material/` 是后续针对视觉偏差补充的无脸本体及独立配饰材质：保留原图层的细腻明暗，运行时连续换色、单独绘制表情并组合配饰，避免用硬色带近似本体或整体缩小带配饰的头像。

`material/body-source.png`、`glasses-source.png`、`headphones-source.png`、`scarf-source.png` 均由内置 imagegen 从批准的 v3 参考提取，原始生成 PNG 保留。提示词见 `material/prompts.json`。通过 `scripts/prepare-character-material.mjs` 编译为共用 RGBA 材质数据；本体宽高比例约 1.29:1，眼镜宽度为本体的 61%，右侧耳机为 33%，围巾宽度为 95%。预设的主色、侧面和底缘取样自原稿，使用连续插值保留材质过渡。

生成方式：内置 `image_gen.imagegen`，`transparent_background: true`。提示词记录在 `prompts.json`。
