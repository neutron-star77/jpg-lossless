# JpgLossless

JpgLossless 是一个 Windows 本地图片压缩工作台。图片始终在本机处理，不上传服务器；界面使用 WebView2，压缩引擎使用 ECT、jpegtran 和 Pillow。

## 下载与运行

### 直接运行（推荐）

1. 下载 `JpgLossless-portable.zip`。
2. 将 ZIP 解压到任意目录。
3. 双击 `JpgLossless.exe`。
4. 如果系统提示缺少 WebView2，安装 Microsoft Edge WebView2 Runtime 后再次启动。

ZIP 已包含可执行程序和运行说明，解压后无需安装 Python、Pillow 或压缩引擎。

### 从 Release 下载

GitHub 和 Gitee 的 Releases 页面提供同一版本的 ZIP 或 EXE。普通用户推荐下载 ZIP，开发者可以 Clone 源码自行构建。

## 快速使用

1. 将图片或文件夹拖入右侧文件区，也可以使用“选择文件 / 选择文件夹”。
2. 选择保存位置、目标格式和文件名规则。
3. 设置“压缩质量”。质量越高，视觉保真度越高；JPG 始终是有损格式，WebP 质量 100 为无损模式。
4. 如需控制文件大小，在“目标体积”中填写 KB 数。JPG/WebP 会自动搜索目标值上下约 10KB 的结果，并优先选择范围内质量最高的编码。
5. 点击“预览选中对比”检查视觉效果，再点击“开始压缩”。
6. 可开启完成后自动删除源文件，但建议先确认输出结果并保留备份。

## 压缩策略

### 原格式无损

- JPG、JPEG 和 PNG 使用 ECT 批量多线程优化。
- ECT 只在结果更小时替换文件，不会因为优化失败而放大源文件。
- 中文路径会自动使用 ASCII 临时路径，规避部分 Windows 编码问题。
- JPEG 在 ECT 失败时可以使用 jpegtran 无损熵编码优化兜底。
- 原格式模式不受“目标体积”限制，因为无损压缩无法保证任意目标大小。

### JPG 转换

- 使用 Pillow 的优化编码和渐进式 JPEG。
- 目标体积模式通过质量二分搜索寻找最高质量结果。
- 如果图片在最低质量下仍无法接近目标，会保留最佳结果并明确显示未达目标。

### WebP 转换

- 使用 libwebp method=6，在体积和编码时间之间选择更高压缩效率。
- 保留 RGBA 透明通道。
- 质量 100 使用 WebP 无损编码；较低质量使用有损编码。

### PNG 转换

- 使用 Pillow optimize=True 的无损 PNG 编码。
- PNG 本身无法通过质量参数变小；超过目标体积时会尝试生成 JPG 兜底。
- JPG 兜底也无法接近目标时，会删除候选 JPG 并保留 PNG。

## 软件优点

- **本地隐私**：图片不离开电脑，适合照片、设计稿和内部资料。
- **无损优先**：原格式模式优先保留像素数据，并在压缩结果放大时保护源文件。
- **目标体积搜索**：不是简单降低质量，而是在目标附近寻找视觉质量更高的结果。
- **透明通道保护**：WebP 转换不会默认丢失 RGBA 透明信息。
- **批量高效**：文件夹递归、目录结构保持、并行处理、断点续传和失败重试均可用。
- **结果可核对**：每个文件显示原始大小、输出大小、压缩比例和处理状态。
- **异常可恢复**：引擎失败、损坏图片、非 ASCII 路径和超出目标体积都会给出明确状态。
- **零安装便携**：ZIP 解压后即可运行，不修改系统，不要求 Python 环境。

## 功能清单

- JPG / JPEG / PNG 原格式无损优化
- JPG、PNG、WebP 格式转换
- WebP 有损 / 无损模式
- 目标体积约束（JPG/WebP 约 ±10KB）
- 批量拖拽、文件夹递归、保持目录结构
- 预览前后对比和像素比例条
- 中文路径兼容
- 失败记录、断点续传和失败重试
- 完成后自动打开输出目录
- 可选完成后自动删除源文件
- 设置保存到程序目录的 `config.json`

## 运行环境

- Windows 10 / 11
- Microsoft Edge WebView2 Runtime

便携 ZIP 已内置 ECT 引擎。源码模式如果 `bin/ect.exe` 缺失，首次使用原格式无损功能时会尝试自动下载。

## 从源码运行

```bash
python -m pip install pywebview pillow
python jpg_lossless_web.py
```

## 构建可执行文件

```bash
pyinstaller --noconfirm --clean JpgLossless.spec
```

构建结果位于 `dist/JpgLossless.exe`。

## 项目结构

```text
jpg-lossless/
├── JpgLossless.exe              # 本地便携运行程序
├── JpgLossless-portable.zip     # 解压即用发布包
├── jpg_lossless_web.py          # WebView2 后端和压缩策略
├── jpg_lossless_gui.py          # 旧版 Tkinter 参考实现
├── web/index.html               # 前端界面
├── bin/ect.exe                  # ECT 无损引擎
├── JpgLossless.spec             # PyInstaller 配置
├── tests/                       # 压缩策略回归测试
└── LICENSE                      # MIT License
```

## 许可证

MIT License。第三方压缩引擎按照各自许可证发布。
