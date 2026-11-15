# 新版角色透明素材提示词

2026-11-15，使用内置 imagegen，以主人提供的优化版角色家族图为编辑目标。原图见 `reference.png`。
产物为根据参考提取的透明版本，不声明逐像素裁切；原始生成物保留，项目消费以下四张静态 PNG。

| 角色 | 项目文件 |
| --- | --- |
| 总助手 | `desktop/src/assets/characters/general.png` |
| 研究助手 | `desktop/src/assets/characters/research.png` |
| 开发助手 | `desktop/src/assets/characters/development.png` |
| 日程助手 | `desktop/src/assets/characters/calendar.png` |

## general

```text
Use case: background-extraction. Asset type: transparent app role portrait. Edit target: the supplied VortoOne role family reference image. Extract ONLY the CORAL RED general assistant at the LEFT of the LOWER LARGE ROW, immediately above the label 总助手. Use that lower-row character, not the giant top character or the tiny bottom icon. Preserve this exact optimized character identity, stepped pixel silhouette, proportions, face, accessory placement, colors, light from upper left and block depth on the lower right. Do not redesign or add detail. Remove all page background, Chinese labels, title, other characters and decorative marks. Keep the whole character and its original restrained contact shadow. Output exactly one centered character on a square canvas with REAL transparent alpha, NOT a white background and NOT a drawn checkerboard. Use a consistent framing for the four portraits: character and contact shadow fit approximately 84% of canvas width, leaving clear transparent margin on every side. No text, border, badge, logo, duplicate characters, cropped accessories or extra decorations. This is extraction of the chosen design, not a new mascot concept.
```

## research

```text
Use case: background-extraction. Asset type: transparent app role portrait. Edit target: the supplied VortoOne role family reference image. Extract ONLY the PURPLE RESEARCH assistant with BLACK SQUARE GLASSES at the SECOND position of the LOWER LARGE ROW immediately above 研究助手. Preserve this exact optimized character identity, stepped pixel silhouette, proportions, face, accessory placement, colors, light from upper left and block depth on the lower right. Do not redesign or add detail. Remove all page background, Chinese labels, title, other characters and decorative marks. Keep the whole character and its original restrained contact shadow. Output exactly one centered character on a square canvas with REAL transparent alpha, NOT a white background and NOT a drawn checkerboard. Use a consistent framing for the four portraits: character and contact shadow fit approximately 84% of canvas width, leaving clear transparent margin on every side. No text, border, badge, logo, duplicate characters, cropped accessories or extra decorations. This is extraction of the chosen design, not a new mascot concept.
```

## development

```text
Use case: background-extraction. Asset type: transparent app role portrait. Edit target: the supplied VortoOne role family reference image. Extract ONLY the MINT GREEN DEVELOPMENT assistant wearing DARK GRAY OVER-EAR HEADPHONES at the THIRD position of the LOWER LARGE ROW immediately above 开发助手. Preserve this exact optimized character identity, stepped pixel silhouette, proportions, face, accessory placement, colors, light from upper left and block depth on the lower right. Do not redesign or add detail. Remove all page background, Chinese labels, title, other characters and decorative marks. Keep the whole character and its original restrained contact shadow. Output exactly one centered character on a square canvas with REAL transparent alpha, NOT a white background and NOT a drawn checkerboard. Use a consistent framing for the four portraits: character and contact shadow fit approximately 84% of canvas width, leaving clear transparent margin on every side. No text, border, badge, logo, duplicate characters, cropped accessories or extra decorations. This is extraction of the chosen design, not a new mascot concept.
```

## calendar

```text
Use case: background-extraction. Asset type: transparent app role portrait. Edit target: the supplied VortoOne role family reference image. Extract ONLY the GOLDEN YELLOW CALENDAR assistant with an ORANGE SCARF and a hanging orange scarf tail on its right at the FOURTH position of the LOWER LARGE ROW immediately above 日程助手. Preserve this exact optimized character identity, stepped pixel silhouette, proportions, face, accessory placement, colors, light from upper left and block depth on the lower right. Do not redesign or add detail. Remove all page background, Chinese labels, title, other characters and decorative marks. Keep the whole character and its original restrained contact shadow. Output exactly one centered character on a square canvas with REAL transparent alpha, NOT a white background and NOT a drawn checkerboard. Use a consistent framing for the four portraits: character and contact shadow fit approximately 84% of canvas width, leaving clear transparent margin on every side. No text, border, badge, logo, duplicate characters, cropped accessories or extra decorations. This is extraction of the chosen design, not a new mascot concept.
```
