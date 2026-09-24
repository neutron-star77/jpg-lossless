# jpg-lossless CLAUDE.md

本地图片压缩工作台（WebView2 + pywebview 桌面应用），纯本地运行不上传。

## 双目录 + 一键发布
- 工作/改码目录：`F:\AI\projects\jpg-lossless`（即本目录）
- 双端 git 仓库：`F:\AI\git\jpg-lossless`（分支 main，GitHub + Gitee 双端）
- 更新后运行 `F:\AI\sync_and_publish.ps1 -Message "<说明>"`：robocopy /XO 同步源码，排除 `*.exe`/`config.json`/`failed.json`，仅真正复制才提交双端推送。

## 纪律（易忘）
- 所有 `.ps1` 必须以 **UTF-8 BOM** 保存（中文注释否则 PowerShell 解析崩溃）。
- sync 脚本 `/XD` 已含 `.codebuddy`，勿泄露工作记忆。
- 打包：PyInstaller 经 `JpgLossless.spec`；无损引擎 `bin/ect.exe`。
- 调试入口：`jpg_lossless_gui.py`（GUI）/ `jpg_lossless_web.py`（Web）。
- 已封装技能 `dual-git-publish`（写说明+同步推送）、`dual-git-release`（建 Release 传 exe）。
