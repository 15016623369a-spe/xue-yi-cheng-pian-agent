# 安装与配置

[返回 README](../../README.md)

素材水印的自动文字识别可选用macOS系统Vision和`/usr/bin/swiftc`。首次调用会在本机`.web/tools/`编译小型识别程序；不下载OCR权重，不发送帧到外部服务。没有此环境时仍可用FFmpeg框选清理，实际构建需支持`delogo`滤镜。系列默认值、标题缓存、原件和清理记录均保存在本地，不需要新增云服务或密钥。操作见[系列、标题与素材清理](EDITORIAL.md)。

## 两种体验范围

| 范围 | 需要什么 | 能做什么 |
| --- | --- | --- |
| 文字工作台 | Python 3.11+；macOS 或支持 `fcntl` 的类 Unix 环境 | 打开网页、导入 MD、查看原文与三镜文字示例 |
| 完整创作 | 文字模型 API、参考声音、本地 MLX / 模型、FFmpeg、Node / Remotion / Chrome | 模型改编、单镜试做、字幕与整片导出、封面 |

完整媒体链路实际验证于 macOS Apple Silicon。Windows 原生运行、Linux GPU 合成和云部署尚未验证。核心网页不需要安装 FastAPI、Streamlit 或数据库服务器。

## 1. 启动工作台

```bash
git clone https://github.com/15016623369a-spe/xue-yi-cheng-pian-agent.git
cd xue-yi-cheng-pian-agent
python3 -m video_agent web --port 8765
```

打开 http://127.0.0.1:8765 。该命令在终端前台运行，关闭终端会停止服务。

macOS 可双击 `启动工作台.command`，它通过 `scripts/start_workbench.py` 启动独立本地进程并检查服务是否就绪。日志位于 `.web/server.log`；没有注册开机服务。可在终端重新运行启动脚本查看就绪提示。

「载入三镜交互练习」在干净仓库中使用 `examples/` 的原创文字示例，不调用模型，也不包含可直接播放的参考声音。本机已有私人验证课程时仍沿用原验证课程，公开版不会覆盖现有用户项目。

## 2. 配置文字模型

在网页「模型设置」填写：

- API 地址：兼容 Chat Completions 的 HTTPS 根地址或 `/v1` 地址；程序拼接 `/chat/completions`。
- 模型名称：由你所使用的服务商提供。
- API 密钥：在本机页面输入，不放入 Markdown、截图或 Git。
- 可选：JSON 模式、输出 token 上限、token 参数名称、估算单价。

支持程度取决于具体服务商是否实现相应接口及 JSON 行为，不保证所有兼容服务都能无修改运行。可先做连接测试，再导入笔记并触发改编。模型用量会产生服务商费用；应用没有统一包月额度。

密钥保存在 `.web/model.json`，权限为仅文件所有者可读写，Git 默认忽略。API 设置读取接口只返回是否已配置密钥。涉及远程的模型接口要求 HTTPS，本地回环接口可用 HTTP；重定向不会携带密钥继续请求。

模型改编后的分镜还要通过项目校验，其中包括参考声音文件存在性。只有文字模型配置、没有媒体配置时，可以查看文字示例和导入笔记，但不能宣称已具备完整制作环境。

## 3. 准备本地媒体环境

优先连接已有安装；不要在每次任务中重复下载权重。完整执行使用以下组件：

| 组件 | 要求或当前使用配置 |
| --- | --- |
| Python 主进程 | 3.11+；仅标准库 |
| Node.js | 能运行仓库固定版本的 Remotion / React；未给出已验证的最低 Node 版本 |
| Remotion / React | 见 `renderer/package.json`；各 Remotion 包保持同版本 |
| 浏览器 | 可执行的 Chrome Headless Shell；渲染脚本使用 `swangle` |
| FFmpeg / FFprobe | 完整可执行安装，支持项目使用的编码、滤镜与探测能力 |
| MLX Python 环境 | 本机使用 MLX Audio 0.6.0、MLX 0.32.4、NumPy、SoundFile |
| TTS | 本地 Qwen3-TTS 12Hz 0.6B Base 8-bit 模型目录 |
| ASR | 本地 Whisper large-v3-turbo ASR fp16 模型目录 |

模型权重需要自行按发布方说明获取并遵守其条款。`workers/mlx_tool.py` 以离线模式读取已安装模型；不负责模型下载。

如没有现成的 Remotion 依赖，可以在仓库中按清单安装：

```bash
npm install --prefix renderer
```

本仓库当前没有完整媒体环境锁文件或一键安装器；上面的命令只安装 Node 依赖，不会安装 FFmpeg、Metal、MLX 或语音模型。作者机器复用已有环境，新机器的完整安装仍需实际检查。

## 4. 填写本机运行配置

```bash
cp config/local.example.json config/local.json
```

所有路径填写本机绝对路径：

| 配置项 | 指向哪里 |
| --- | --- |
| `ffmpeg` / `ffprobe` | 对应可执行文件 |
| `node` | Node 可执行文件 |
| `voice_python` | 已安装 MLX Audio 的独立 Python 可执行文件 |
| `remotion_root` | 本机 Remotion 项目根目录，内含匹配的 `package.json`；新安装可指向本仓库 `renderer/` |
| `browser` | Chrome Headless Shell 的可执行文件 |
| `voice_model` / `asr_model` | 已下载模型目录 |
| `hf_home` | 本地 Hugging Face 缓存目录 |

`renderer/node_modules` 需要实际依赖或指向兼容依赖的链接。已有本项目早期工具目录的使用者也可运行 `scripts/configure_local.py --toolkit /绝对路径/工具根目录`，它只连接已有环境，不是通用安装器。

检查路径：

```bash
python3 -m video_agent doctor
```

`doctor` 通过只代表路径存在。是否可真实合成、渲染和解码，需要单镜试做与产物检查。

## 5. 配置自己的参考声音

```bash
cp config/voice.example.json config/default-voice.json
```

修改以下项目：

- `profile`：自己的音色标识。
- `reference_audio`：有权使用的参考声音文件，使用绝对路径。
- `reference_text`：与该音频准确对应的文字文件，使用绝对路径。
- `conditioning`：`icl` 使用参考音频及文字；`speaker_embedding` 为另一种条件模式。
- `speed`：0.5–2；默认示例 1.18。
- `volume`：0–2，即 0%–200%。

本仓库不附带人物音色或授权声明。不同参考录音的自然度、相似度和语气需要试做后判断，不能保证一次生成就达到目标。

默认音色配置在**新建项目时**读取。修改默认文件不会改写旧项目已登记的声音身份或配音导入记录。资源库的「语音」分类目前也不会自动替换合成用的默认音色。

## 6. 制作与交付

1. 新建项目，导入自己的 `handoff.md`。
2. 指定受众、讲解方向和时长；模型生成知识与分镜。
3. 核对原文引用、结论、类比边界和术语读法。
4. 上传素材、选配音效，或使用「自动找素材并匹配」。
5. 先试做一镜，检查声音、字幕和画面。
6. 当前修订审阅确认后生成整片。
7. 在交付页生成横竖封面，下载视频、字幕、文案和 MD 工作表。

单个上传文件目前最多 100 MB；视频 / GIF 最长 120 秒，上传音效最长 30 秒。事件展示时长仍受分镜配置限制，不能当作自由视频剪辑时间轴。

完整项目 JSON 也可用 CLI 执行：

```bash
python3 -m video_agent validate /绝对路径/project.json
python3 -m video_agent run /绝对路径/project.json
python3 -m video_agent status /绝对路径/project.json
```

`examples/storyboard.json` 是模型层分镜示例，**不是**可以直接传给 `run` 的完整项目 JSON；完整配置还要合并 `voice`、`settings`、已登记素材及来源信息。

## 常见问题

**模型连接失败？** 核对接口地址、模型名、密钥、token 参数和网络。程序保留 TLS 校验，不建议通过关闭证书验证规避问题。

**引用校验失败？** 检查原文是否包含所引内容，Markdown 表格、空行和重复行会影响行号。模型优先选择原文编号，由程序回填引文；不能通过改成模糊匹配绕过错误。

**ASR 覆盖不足？** 先检查术语读法和漏读，不要直接降低门禁。局部修改后重新试做；缓存输入变化会使相关阶段重建。

**新机器为什么不能直接生成？** 仓库不分发模型、私人参考声音或 FFmpeg / Chrome 安装。按本页配置后，先用单镜验证。

**页面关了任务还在吗？** 后台工作进程可以继续运行；主机休眠、进程退出或环境故障会中断执行。重新打开页面查看状态，重试可复用已核验的成功阶段。

**可以直接放到公网吗？** 当前为本机工具，缺少多用户鉴权、租户隔离与公网部署方案。不要把仅适用于本机的服务当作已完成的 SaaS 服务。


### 网上片头

打开项目后，点击顶部「⌕ 查找片头」，或「学习与方向 → 本期片头」。可以搜索并自动尝试接入，也可只查候选后选择剪点。完整操作与平台边界见 [网上找片头](OPENINGS.md)。检索不需要文字模型配置；截取需要本机 yt-dlp 和 FFmpeg，自动定位原话另需本机 Whisper。配置 `local.json` 的 `yt_dlp`，或将其放在 `voice_python` 同级目录；已安装环境可直接复用。
