# JpgLossless · 图片压缩工作台（Web 版）

纯本地运行的图片压缩工具。界面用系统 Edge **WebView2** 内核渲染（「工程蓝图」风格），
压缩逻辑全部在本机完成，图片**不会上传**任何服务器。

## 下载与安装（推荐从 Release 页面获取）

> 仓库里直接 Clone / 下载的是「源码」，并不能双击运行（需要装 Python、装依赖、再打包）。**最省事的方式是到 Release 页面下载已经打包好的单文件 exe。**

### 为什么建议从 Release 页面下载，而不是 Clone 源码？

- **Clone / 下载源码 = 拿到的是 Python 源代码**（`.py`、前端 HTML 等），没有打包成可执行程序，双击打不开；要自己装 Python 3.x、执行 `pip install pywebview pillow`，再用 PyInstaller 打包，对普通用户门槛很高。
- **Release 页面 = 官方发布的成品**：开发者已经把源码连同压缩引擎 `ect.exe` 一起打包成了单文件 `JpgLossless.exe`，**双击即用，无需安装任何环境**。版本号清晰，出问题能对照版本反馈。
- 每次发版都会在 Release 里附上对应版本的 exe 与更新说明，下载到的就是当前最稳定、已验证的版本，不必自己从源码构建。

### 需要下载什么？

1. **`JpgLossless.exe`**（必需）：在仓库的 **Releases** 里下载——
   - GitHub：进仓库主页，点右侧的 **Releases**（或「发布」），找到最新版本（如 `v1.1`），在 `Assets` 里点 `JpgLossless.exe`。
   - Gitee：进仓库主页，点 **发行版**，同样找到最新版本下载 `JpgLossless.exe`。
   - 它是单文件，已内置压缩引擎，不依赖仓库里的 `bin/ect.exe` 目录。
2. **Microsoft Edge WebView2 运行时**（仅在双击 exe 报错时按需安装）：Windows 10/11 系统通常已自带；若双击后提示缺少 WebView2，请到微软官网下载安装「WebView2 Runtime」（Evergreen Bootstrapper）即可。

> 不需要下载源码、不需要装 Python、不需要手动放 `ect.exe`。

### 下载之后怎么用？（详细步骤）

1. **下载**：进入仓库的 Releases 页面，找到**最新版本**，在「Assets / 资源」里点 `JpgLossless.exe` 下载到本地（如下载到「下载」文件夹或任意目录）。
2. **（可选）放好位置**：把它挪到你习惯的目录，例如 `D:\Tools\`。单文件 exe 不挑位置，随意存放、可建快捷方式到桌面。
3. **双击运行**：直接双击 `JpgLossless.exe`。
   - 正常情况：窗口立刻弹出，进入工作台界面。
   - 若弹窗报错「无法启动，因为计算机中丢失 …」或提示缺少 WebView2：按上面第 2 点安装 WebView2 Runtime，装完再双击即可。
4. **开始压缩**（与下方「快速使用」一致）：
   1. 把图片 / 文件夹拖进右侧「拖入区」，或点「选择文件 / 选择文件夹」。
   2. 左侧设置保存位置、目标格式、文件名后缀、JPG 质量等。
   3. 选中文件点「预览选中对比」查看前后效果。
   4. 点「开始压缩」。
5. **设置自动保存**：你的偏好会写入 exe 同目录的 `config.json`，下次启动自动恢复。

## 快速使用

1. 双击 `JpgLossless.exe`（无需安装 Python）。
2. 把图片 / 文件夹拖进右侧「拖入区」，或点「选择文件 / 选择文件夹」。
3. 在左侧设置：保存位置、目标格式、文件名后缀、JPG 质量、是否自动打开输出文件夹。
4. 选中某个文件后点「预览选中对比」，可先在右侧查看前后大小与效果。
5. 点「开始压缩」。

每个文件行里有一条**像素对比条**：灰色=原大小，蓝/绿=压缩后大小（变绿表示更小，变橙表示变大），
配合等宽字体的字节数与百分比，压缩结果一眼可见。

## 功能

- 原格式无损（JPG/PNG，依赖 `ect` 引擎；首次使用自动下载，无网络时把 `ect.exe` 放进 `bin/`）
- 转 WebP 无损 / PNG / JPG（JPG 有损，质量可调）
- 批量拖入、文件夹递归、保持目录结构
- 输出前预览对比、处理完成自动打开输出文件夹
- 设置自动写入 `config.json`，下次启动恢复

## 运行环境

- Windows 10 / 11（需 Edge WebView2 运行时，系统通常自带；若双击报错提示缺少 WebView2，
  到微软官网安装「WebView2 Runtime」即可）
- 压缩引擎 `bin/ect.exe` 已随包内置；原文件夹无损模式若缺失会自动联网下载

## 开发 / 打包

```bash
# 源码运行（需 Python + pip install pywebview pillow）
python jpg_lossless_web.py

# 打包为单文件 exe
pyinstaller --onefile --noconsole --name JpgLossless ^
  --add-binary "bin/ect.exe;bin" --add-data "web/index.html;web" ^
  --hidden-import webview --collect-submodules webview --collect-data webview ^
  jpg_lossless_web.py
```

## 文件结构

```
jpg-lossless/
├── JpgLossless.exe        ← 双击即用（方案 B 成品，WebView2 界面）
├── jpg_lossless_web.py    ← 后端（pywebview API + 压缩逻辑）
├── jpg_lossless_gui.py    ← 旧版 tkinter 界面（保留参考）
├── web/index.html         ← 前端界面
├── bin/ect.exe            ← 压缩引擎
├── config.json            ← 自动生成（上次设置）
└── README.md
```
