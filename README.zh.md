# podcast-transcript-public

把播客音频、视频、单集链接或粗转录，整理成可以阅读和校对的全文。

[英文说明](./README.md) · [中文介绍页](./docs/zh.html) · [English introduction](./docs/index.html)

## 以前装过旧名字？

skill 文件夹以前写成 `podcast-trancript-public`（transcript 少了一个 s）。现在的文件夹和 skill 名是 `podcast-transcript-public`。助手里的旧文件夹不会自动改名，留着就会继续用旧的那份。

1. 删掉旧文件夹，例如 `~/.codex/skills/podcast-trancript-public`、`~/.claude/skills/podcast-trancript-public` 或 `~/.cursor/skills/podcast-trancript-public`。
2. 用下面的安装句子再装一次，或者先 `pipx install`，再运行 `podcast-transcript install-skill --target <工具名>`。

Whisper 模型缓存在旧拼写的目录里。把那个缓存目录改名为 `podcast-transcript-public`（在 `~/Library/Caches`、`%LOCALAPPDATA%` 或 `~/.cache` 下），就能接着用已下载的模型。不改的话，下次运行会重新下载。`PODCAST_TRANSCRIPT_CACHE` 仍然可以指定位置。

## 示例文稿

下面这段是**文档示例**。它不是真实节目，也不是模型输出。

```markdown
# 周末实验室 · 第 12 期

## 摘要

主播林夏和嘉宾周衡讨论中英混说播客。产品名保留英文，中文论述保持完整。

## 全文

**林夏**: 欢迎来到周末实验室。今天我们聊 Whisper 在中英混说里怎么加标点。

**周衡**: faster-whisper 会把 GPU 留成英文，但同一句不该在下一分钟再印一遍。
```

命令行写出的 `transcript.md` 是分段原文的可读副本。上面的结构是 skill 校对后的目标。

## 快速开始

跟 AI 助手说两句话就行，不用先学命令。好读的精校稿来自助手这一步。命令行只出粗转录。

### 跟助手说一句话

把这句发给助手。地址要给到 skill 文件夹，不要只给仓库根目录。

```text
帮我安装这个 skill：https://github.com/ZHUCH-520/podcast-transcript-public/tree/main/podcast-transcript-public
```

Codex 会用自带的 skill-installer 装好，下一条消息就能用。Claude Code、Cursor 没有内置安装器，助手会用命令把文件夹复制到它的 skill 目录，执行前会问你；装好后如果没出现，新开一个会话。

然后发一期节目：

```text
请用 podcast-transcript-public 帮我把这期播客整理成中文文字稿：<小宇宙 / Apple Podcasts 链接>
```

本地音频：

```text
请用 podcast-transcript-public 帮我处理这个音频文件，输出 Markdown 和 HTML：<文件路径>
```

已经有粗转录：

```text
请用 podcast-transcript-public 帮我校对这份粗转录：<粘贴文字或文件路径>
```

Codex 里也可以输入 `$` 选择 `podcast-transcript-public`。第一次处理音频前，会先告诉你要下载什么、多大、存在哪，你同意才开始。

音频和转写留在本机。校对会把文字稿发给你正在用的 AI 助手，由那个模型处理。

### 命令行

需要 Python 3.9+ 和 FFmpeg。工具不会替你安装系统软件。命令行只出粗转录（几乎没有标点）和可选字幕，好读的精校稿要靠上面的 AI 助手那一步。

```bash
pipx install "git+https://github.com/ZHUCH-520/podcast-transcript-public.git"
podcast-transcript run episode.mp3 --output-dir ./episode --accept-download --language zh-en
```

`pipx install` 本身不下载模型。第一次运行会先打印安装计划，只有带上 `--accept-download` 才会下载。

| 语言参数 | 行为 |
|---|---|
| 省略或 `auto` | 由模型判断语言 |
| `zh` | 按中文识别 |
| `en` | 按英文识别 |
| `zh-en` | 按中文识别，并提示模型保留英文专名 |

`--language zh` 不会给原始转写自动补上中文标点。真实节目里，一千行里可能只有几行带标点，而且多半是英文逗号。标点、人名修正和分段排版来自 skill 的校对步骤，不是 Whisper 的原始输出。

专名写在 UTF-8 文本里，一行一个，然后：

```bash
podcast-transcript run episode.m4a --output-dir ./episode --language zh-en --glossary names.txt --accept-download
```

空行和以 `#` 开头的行会被忽略。faster-whisper 把这些词当作 hotwords。Apple Silicon 上的 MLX Whisper 没有 hotwords 参数，这些词会附加到提示里。

长音频会做语音活动检测，并在语音边缘留出空白，避免把轻声切掉。每一段不把上一段文字当作条件，避免长节目里同一句被反复印出来。时间重叠且文字相同的片段会合并成一次。上一句已经结束后又出现的重复，会保留。

## 单集链接

可以代替本地文件的地址：

- 直接的音频或视频 URL
- 小宇宙单集页：`https://www.xiaoyuzhoufm.com/episode/<id>`
- Apple Podcasts 单集链接：地址里要有 `i=<单集 id>`
- RSS 或 Atom 订阅。使用第一条条目的音频地址，并在下载前打印节目标题

下载音频之前，命令会打印解析出的音频主机名。节目主页、登录页、带账号密码的 URL 会被拒绝。付费墙、DRM 和需要登录的媒体不会被绕过。模型下载仍然要单独同意。

```bash
podcast-transcript run "https://www.xiaoyuzhoufm.com/episode/示例id" --output-dir ./episode --accept-download
```

只查看单集页对应的音频地址、不下载文件：

```bash
python podcast-transcript-public/scripts/resolve_media.py "https://www.xiaoyuzhoufm.com/episode/示例id"
```

## 模型

| 模型 | 适合 | 代价 |
|---|---|---|
| `small` | 先试一集，或机器比较小 | 约 480 MB。更快，人名和噪音可能要多改 |
| `turbo` | 较新的 Apple Silicon，或有合适 GPU 的 Windows / Linux | Apple Silicon 约 3.2 GB，Windows / Linux 约 1.6 GB |
| `large-v3` | 人名多、口音重、录音不稳 | 约 3.2 GB，占用更高 |

Apple Silicon 使用 MLX Whisper。Intel Mac、Windows 和 Linux 使用 faster-whisper。Windows 和 Linux 在检测到 CUDA 时使用 float16，否则使用 CPU 和 int8。`large-v3` 不会被自动选中。已有粗转录时可以跳过模型下载，直接校对文本。

## 安装到中文编程助手

这是一份标准 `SKILL.md` skill：一个名为 `podcast-transcript-public` 的文件夹，里面是 `SKILL.md` 和 `scripts/`。能读取这种格式的助手和技能库应当可以使用它。下面列出的是已经核对过安装位置或安装方式的宿主，不是在每个产品里做过实机验收：

- Codex：用上面的安装句子。Codex 自带 skill-installer，下一条消息就能用
- Claude Code、Cursor、Qoder、通义灵码、CodeBuddy、TRAE、Qwen Code、Kimi Code：使用下面的目录
- WorkBuddy：上传技能包。用 `podcast-transcript install-skill --target workbuddy --dest ./workbuddy-skill` 准备文件夹。官方页面没有写出磁盘目录，所以不会猜一个主目录路径

其他能读取标准 `SKILL.md` 文件夹的主流宿主也应同样可用。没有核对成功的宿主，例如 CatPaw，不列入已核对名单。

一条命令复制同一份 `SKILL.md` 和 `scripts/`，不改写说明文件：

```bash
podcast-transcript install-skill --list
podcast-transcript install-skill --target qoder
podcast-transcript install-skill --target trae-cn --scope project
```

已核对的目录：

| 目标 | 产品 | 用户级 | 项目级 |
|---|---|---|---|
| `qoder` | Qoder。没有叫 qcoder 的独立产品 | `~/.qoder/skills/` | `.qoder/skills/` |
| `qoder-cn` | Qoder CN CLI，仅项目级 | 当前技能页没有列出用户级目录 | `.qoder/skills/` |
| `lingma` | 通义灵码，以及 Qoder CN IDE | `~/.lingma/skills/` | `.lingma/skills/` |
| `codebuddy` | CodeBuddy | `~/.codebuddy/skills/` | `.codebuddy/skills/` |
| `trae` | TRAE 国际版文档 | `~/.trae/skills/` | `.trae/skills/` |
| `trae-cn` | TRAE 中文版 | `~/.trae-cn/skills/` | `.trae/skills/` |
| `qwen` | Qwen Code | `~/.qwen/skills/` | `.qwen/skills/` |
| `kimi` | Kimi Code CLI | `~/.kimi-code/skills/` | `.kimi-code/skills/` |

这次没有自动安装的：

- **CatPaw**：公开文档里没有核对到 `SKILL.md` 目录。旁路资料提到的 `.catpaw/rules` 是规则文件，不是这个 skill 文件夹。
- **WorkBuddy**：官方说明是上传技能包，但没有写出目录名。可以用 `--target workbuddy --dest ./workbuddy-skill` 准备一个文件夹再手动上传。第三方主目录路径不会被使用。

Codex 仍用快速开始里的安装句子。Claude Code 和 Cursor 没有内置安装器，助手会在确认后复制文件夹；这两个目录也在 `install-skill --list` 里。说话人分离要在 Hugging Face 同意 pyannote 模型条款，并设置 `HF_TOKEN`（或 `HUGGING_FACE_HUB_TOKEN`）。

## 它不会做的事

- 绕过付费墙、登录、DRM 或平台限制
- 保证认出每一个说话人
- 自动安装 FFmpeg、CUDA 或修改系统设置
- 自动上传或发布生成的文件
