# JpgLossless 深度审计报告

## 范围

审计了 `jpg_lossless_web.py`、`web/index.html`、打包配置和双端 Git 仓库。项目是一个功能完整但仍以单体入口承载多数领域逻辑的本地图片优化器，当前没有自动化测试。

## 已落地优化

- 抽出统一的有损目标搜索算法 `_compress_to_target`，集中处理质量边界、二分搜索和是否接近目标。
- JPG/WebP 在最低质量仍超限时不再伪报 `≤N KB`，而是显示实际未达标状态。
- PNG 转 JPG 兜底未达标时删除候选 JPG，恢复 PNG 的真实大小和路径。
- JPG/WebP 输出目标使用质量二分搜索，目标范围为目标值上下 10KB，并优先选择范围内最高质量。
- JPEG 使用 optimize + progressive，WebP 使用 method=6 并保留 RGBA 透明通道；PNG 使用 optimize 编码。
- 质量值在后端统一限制到 1–100；界面不再宣称 JPG 质量 100 是无损。
- 增加领域词汇、ADR 和限额算法回归测试。

## 架构发现

### 强推荐：深化压缩策略模块

当前 `Api._run` 同时负责路径规划、并发调度、引擎进程、重试、大小约束、结果事件和持久化。它是一个浅接口背后过深且难测的单体。下一步应把“输出规划”“编码策略”“结果提交”做成内部模块，保留 `Api._run(settings)` 作为唯一外部接口。这样测试可在真实接口上替换 Engine adapter，失败重试和源文件保护也能保持局部性。

### 值得探索：增加成熟引擎适配器

Oxipng 适合作为 PNG 无损 adapter；MozJPEG/jpegtran 适合作为 JPEG 无损与渐进式优化 adapter；libwebp/cwebp 的 method、metadata 和透明像素选项可补足 WebP adapter。当前程序已有一个 ECT 和一个 jpegtran fallback，第二个真实 adapter seam 已经存在，适合抽象；但应先确认发行包体积、许可证和 Windows 二进制供应链。

### 值得探索：原子输出与校验

目前部分外部引擎直接写目标文件。将编码写入同目录临时文件，验证可读、非空且满足“只在更小或明确转换时替换”后再原子替换，可降低中断留下半成品的风险。

## 开源项目对照

- ECT：保留为原格式无损主引擎，并继续利用批量多线程调用。
- MozJPEG/jpegtran：保留 `-optimize -progressive -copy none` 的无损 JPEG 优化思路。
- Oxipng：建议作为 PNG adapter，提供多线程、重过滤和 metadata 策略。
- libwebp/cwebp：建议把 method、lossless/near-lossless、alpha 和 metadata 作为明确的策略参数，而非只传 Pillow 的默认值。

## 风险与后续顺序

1. 先把当前工作目录同步到 `F:\AI\git\jpg-lossless`，再在双端仓库跑测试和提交。
2. 再做输出策略模块化，避免把 UI 事件和编码器细节继续耦合。
3. 最后评估 Oxipng、MozJPEG 和 cwebp 的可分发二进制、许可证与体积。
