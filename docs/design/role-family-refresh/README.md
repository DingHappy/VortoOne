# 优化版角色家族验收

2026-11-15，按主人提供的新版参考更新四张 Desktop 角色透明素材。
本批直接基于 main，与模型连接功能分别提交；只更换静态图片及其来源、提示词和验收记录。

## 素材

| 角色 | 产物 |
| --- | --- |
| 珊瑚红总助手 | [general.png](../../../desktop/src/assets/characters/general.png) |
| 紫色眼镜研究助手 | [research.png](../../../desktop/src/assets/characters/research.png) |
| 绿色耳机开发助手 | [development.png](../../../desktop/src/assets/characters/development.png) |
| 黄色围巾日程助手 | [calendar.png](../../../desktop/src/assets/characters/calendar.png) |

参考原图为 [reference.png](reference.png)，使用内置 imagegen 提取透明版本，
完整提示词见 [prompts.md](prompts.md)。生成物保留原始像素，没有再用代码重画或替换为 SVG。
生成提取不承诺逐像素裁切；本次以角色形状、五官、颜色、配饰及实际显示效果核对。

## 验收

- 四张均为 1254×1254 PNG，具有透明通道。真实组件加载成功，天然尺寸正确；
  在浏览器内只读采样四个角的 alpha，每张四角均为 0。
- 沿用原 `RoleAvatar`：侧栏显示 40×40，资料页使用原大头像尺寸，图像保持 contain，未裁掉配饰。
  [总助手](general-light.png)、[研究助手](research-light.png)、[开发助手](development-light.png)、
  [日程助手](calendar-light.png) 四个真实资料页逐一核对了图片来源和加载结果。
- 浅色与[深色](research-dark.png)下核对透明边缘、眼镜、耳机、围巾与小头像辨识。
  所有图均来自生产组件和合成名录，不读取或改动主人资料。
- 截图视口为 1240×900 CSS，按 Retina 原始密度保存为 2480×1800；未放大旧图。
- 角色资料切换期间未观察到控制台 error 或未捕获异常。
- 角色登记、权限、未知岗位回退、自定义头像优先保持原实现，没有改动映射或运行层。
  静止以外的思考/招呼动画、应用图标和手机端未在本批新增或替换。

完整本地门禁的执行项通过；11 条 Python skip、2 条 Rust ignore 单独列在 PR，不算通过。
没有新增不必要的位图单测，现有角色与自定义头像行为契约随门禁运行。
安装版与手机真机未验证，没有合并、发版、部署或修改主工作区 iOS 工程。

final result: passed
