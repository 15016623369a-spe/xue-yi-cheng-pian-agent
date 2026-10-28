# 来源与使用说明

[返回 README](../../README.md)

## 项目边界

本仓库为独立的学习内容创作工作台。历史探索曾评估 NarratoAI、MoneyPrinterTurbo 等项目，现有 `video_agent/` 执行链直接组织 Remotion、FFmpeg 与本地语音工具；没有把第三方完整产品的能力宣称为本项目已实现。

README 的导航、技术表格与架构介绍组织方式参考 [Ragent](https://github.com/15016623369a-spe/ragent)。未复制其技术栈、标识或功能声明。

## 使用的第三方组件与模型

以下为项目来源入口，具体使用与分发需查看对应版本的许可和条款：

| 组件 / 模型 | 官方或发布方来源 | 使用方式 |
| --- | --- | --- |
| Python | https://www.python.org/ | 本地服务与执行器 |
| React | https://github.com/facebook/react | Remotion 中的画面组件 |
| Remotion | https://github.com/remotion-dev/remotion | 参数化渲染与封面；适用其许可及使用条款 |
| FFmpeg | https://ffmpeg.org/legal.html | 调用本机安装；具体构建的许可和编码支持有差异 |
| MLX | https://github.com/ml-explore/mlx | Apple Silicon 本地计算 |
| MLX Audio | https://github.com/Blaizzy/mlx-audio | TTS 与 ASR 模型加载及推理 |
| Qwen3-TTS | https://github.com/QwenLM/Qwen3-TTS | 用户本地模型；权重及转换版本分别查看发布说明 |
| Whisper | https://github.com/openai/whisper | ASR 模型来源；本项目通过 MLX 转换版本执行 |
| NumPy / SoundFile | https://numpy.org/ / https://python-soundfile.readthedocs.io/ | 本地音频数值与文件操作 |
| Wikimedia Commons | https://commons.wikimedia.org/ | 图片检索；每个文件分别保留来源与许可 |

Node 依赖清单见 `renderer/package.json`。模型及媒体工作环境未打包进主 Python 分发，本仓库没有重新分发 Chrome、FFmpeg 二进制或模型权重。

## 素材与声音

公开仓库不附个人课程全文、人物参考录音、直播切片、商用音乐、表情包或生成成片。文字示例和项目横幅为本项目编写。

使用者应自行提供有权使用的素材及参考声音。某个声音允许使用，不自动代表相关第三方视频、音乐、肖像和其他素材的全部权利都已取得。自动检索 Commons 图片会记录来源、作者和许可，但下载成功不能代替检查最终使用方式是否符合该文件条款。

## 仓库许可状态

仓库目前用于公开展示与技术交流，**尚未指定整体开源许可证**。GitHub 的公开可见性不等于授予任意复制、修改、再分发或商用许可。第三方组件和素材始终适用其各自条款。

后续若选择仓库许可证，应独立审查源码来源与依赖条款；不能把对本项目代码的许可扩展到人物声音、模型权重或第三方素材。
