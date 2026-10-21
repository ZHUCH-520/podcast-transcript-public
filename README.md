# podcast-transcript-public

Turn podcast audio, video, episode links, or rough transcripts into clean, readable transcripts.

The skill fixes punctuation and paragraph breaks, checks names and speaker labels, and keeps the substance of the conversation intact. It produces Markdown for editing and HTML for reading or sharing.

[View the installable skill](./podcast-transcript-public/SKILL.md) · [English introduction](./docs/index.html) · [中文介绍](./docs/zh.html) · [中文说明](./README.zh.md)

## Already installed the old name?

The skill folder used to be spelled `podcast-trancript-public` (missing the “s”). It is now `podcast-transcript-public`. An assistant keeps the old folder until you remove it, and that old copy will not pick up this rename.

1. Delete the old folder, for example `~/.codex/skills/podcast-trancript-public`, `~/.claude/skills/podcast-trancript-public`, or `~/.cursor/skills/podcast-trancript-public`.
2. Install again with the sentence in Quickstart, or with `podcast-transcript install-skill --target <tool>` after `pipx install`.

Whisper models were cached under the old spelling. Rename that cache directory to `podcast-transcript-public` (in `~/Library/Caches`, `%LOCALAPPDATA%`, or `~/.cache`) to keep the download. A new run otherwise downloads the model again. `PODCAST_TRANSCRIPT_CACHE` still overrides the path.

## Sample transcript

This block is a **sample** written for the documentation. It is not a real episode and not model output.

```markdown
# 周末实验室 · 第 12 期

## Summary

Host 林夏 and guest 周衡 talk about Chinese-English podcasts. Product names stay in English. The Chinese argument stays complete.

## Full transcript

**林夏**: 欢迎来到周末实验室。今天我们聊 Whisper 在中英混说里怎么加标点。

**周衡**: faster-whisper 会把 GPU 留成英文，但同一句不该在下一分钟再印一遍。

**林夏**: 所以长音频先切开静音，专名放进 glossary，再逐段校对。
```

The command-line tool writes a simpler raw `transcript.md` from `segments.json`. The sample above is the proofread shape the skill aims for.

## Quickstart

Ask your AI assistant to install the skill, then hand it an episode. You do not need a setup command for that path. The readable transcript comes from the assistant. The command line further down only writes a raw transcript.

### Ask your assistant

Send this install sentence. The address has to be the skill folder, not only the repository root.

```text
Install this skill for me: https://github.com/ZHUCH-520/podcast-transcript-public/tree/main/podcast-transcript-public
```

Codex installs it with its built-in skill-installer. The skill is ready on your next message. Claude Code and Cursor have no built-in installer: the assistant copies the folder into its skills directory and asks before it runs that command. If the skill does not show up afterward, start a new session.

Then send an episode:

```text
Use podcast-transcript-public to turn this episode into a clean English transcript: <episode link>
```

A local file:

```text
Use podcast-transcript-public to process this audio file and give me Markdown and HTML: <file path>
```

A rough transcript, with no speech recognition:

```text
Use podcast-transcript-public to proofread this rough transcript: <paste text or file path>
```

In Codex you can also type `$` and choose `podcast-transcript-public`. The first time the skill handles audio, it shows the model, the download size, and the storage location, and waits until you approve.

### Command line

Python 3.9+ and FFmpeg are required. The tool does not install system software for you. This path writes a raw transcript with almost no punctuation, plus optional captions. Proofreading still happens in the assistant step above.

```bash
pipx install "git+https://github.com/ZHUCH-520/podcast-transcript-public.git"
podcast-transcript run episode.mp3 --output-dir ./episode --accept-download --language zh-en
```

The first run prints the model plan and stops until `--accept-download` approves that download. Nothing is downloaded during `pipx install`.

Chinese-English shows use `--language zh-en`. Chinese-only shows can use `--language zh`. Omit `--language` to let the model detect the language. `zh` and `zh-en` also send a short prompt so English names are more likely to stay in Latin script. Put names in a text file, one per line, and pass `--glossary names.txt`.

The raw `raw-transcript.txt` from this command is still mostly unpunctuated. A real Chinese episode can have punctuation on only a handful of lines. Punctuation, name corrections, and readable formatting come from the skill's proofreading step, not from Whisper.

A Xiaoyuzhou episode page, an Apple Podcasts episode link, an RSS feed, or a direct media URL works in place of the local file. The command prints the resolved audio host before it downloads that file. A show page, a sign-in page, or a URL with embedded credentials is refused.

## What it works well with

- Podcast interviews and roundtables
- Long-form video or recorded conversations
- Rough transcripts with poor punctuation or frequent recognition errors
- English, Chinese, or bilingual material

## Install

Use the sentences in Quickstart. Codex, Claude Code, and Cursor can all take them.

Codex uses its built-in skill-installer and can run the skill on the next message. Claude Code and Cursor have no built-in installer. The assistant copies this folder into `~/.claude/skills/` or `~/.cursor/skills/` and asks before running the command. Other checked hosts are installed with `podcast-transcript install-skill` after the command-line tool is installed.

If you already have a rough transcript, send the text directly. Speech recognition is not required.

## Use it without Codex

One command transcribes a local file or a supported URL:

```bash
podcast-transcript run https://example.com/episode.mp3 --output-dir ./episode --accept-download --diarize --captions both
```

The command writes `raw-transcript.txt`, `segments.json`, and a simple `transcript.md`. `--diarize` adds `SPEAKER_00` style IDs when that optional runtime is already installed, or when `--accept-download` also covers its install. If diarization cannot be installed, the transcript is still written. `--captions both` also writes `transcript.srt` and `transcript.vtt`. Existing files are not overwritten. Choose `small`, `turbo`, or `large-v3` with `--profile`; otherwise the recommended profile is used. `large-v3` is never chosen automatically.

`transcript.md` from this command is a readable copy of the raw segments. It is not the proofread Markdown and HTML the skill produces.

`podcast-transcript doctor`, `plan`, `setup`, `split`, and `diarize` call the same scripts as the skill. `podcast-transcript export segments.json --format srt --output captions.srt` renders captions from an existing segment file.

### Install the skill into an assistant

The skill is a standard `SKILL.md` skill: one folder named `podcast-transcript-public`, with `SKILL.md` plus `scripts/`. Keep that folder name. Hosts and skill hubs that load this format should be able to run it. The installer copies that folder and does not rewrite `SKILL.md`.

These are the hosts whose skill location or install method was checked. This is not a live test inside each product:

- Codex, through the install sentence in Quickstart
- Claude Code, Cursor, Qoder, Tongyi Lingma, CodeBuddy, TRAE, Qwen Code, and Kimi Code, through the directories below
- WorkBuddy, by uploading the skill package. Prepare it with `podcast-transcript install-skill --target workbuddy --dest ./workbuddy-skill`. The official page does not name an on-disk directory, so that path is not guessed

Other mainstream hosts that read a standard `SKILL.md` folder should work the same way. Hosts that were not checked, including CatPaw, are not listed as verified.

```bash
podcast-transcript install-skill --list
podcast-transcript install-skill --target qoder
podcast-transcript install-skill --target trae-cn --scope project
```

| Target | Product | User directory | Project directory |
|---|---|---|---|
| `qoder` | Qoder (not "qcoder") | `~/.qoder/skills/` | `.qoder/skills/` |
| `qoder-cn` | Qoder CN CLI, project scope only | not listed on the current skills page | `.qoder/skills/` |
| `lingma` | Tongyi Lingma and the Qoder CN IDE | `~/.lingma/skills/` | `.lingma/skills/` |
| `codebuddy` | CodeBuddy | `~/.codebuddy/skills/` | `.codebuddy/skills/` |
| `trae` | TRAE international docs | `~/.trae/skills/` | `.trae/skills/` |
| `trae-cn` | TRAE CN | `~/.trae-cn/skills/` | `.trae/skills/` |
| `qwen` | Qwen Code | `~/.qwen/skills/` | `.qwen/skills/` |
| `kimi` | Kimi Code CLI | `~/.kimi-code/skills/` | `.kimi-code/skills/` |
| `claude` | Claude Code | `~/.claude/skills/` | `.claude/skills/` |
| `cursor` | Cursor | `~/.cursor/skills/` | `.cursor/skills/` |

Cursor also reads `.agents/skills/` in the same layout. Kimi Code CLI also scans `~/.agents/skills/` and `.agents/skills/`. TRAE CN loads a project `.agents/skills/` directory only after that import setting is enabled, and a same-named skill in `.trae/skills/` wins.

These were checked and are not installed to a guessed path:

- **CatPaw**: its public docs did not provide a `SKILL.md` directory. Secondary notes mention `.catpaw/rules`, which is a different rules file.
- **WorkBuddy**: the official skills page describes uploading a package, but it does not name the directory. `podcast-transcript install-skill --target workbuddy --dest ./workbuddy-skill` prepares a folder you can upload. Third-party home-directory paths are not used.
- **qcoder**: this is not a separate product name. Use `--target qoder`.

Codex still uses the install sentence in Quickstart. Copy the folder rather than rewriting `SKILL.md`. The scripts must stay beside it.

## Your first audio or video

You do not need to set up Whisper in advance. When the skill receives audio or video for the first time, it checks your computer and shows a setup plan before downloading anything:

- Whether your computer will use MLX Whisper or faster-whisper
- Which model it recommends, with the option to choose another
- The approximate download size and storage location
- What requires a network connection

| Model | Best for | Tradeoff |
|---|---|---|
| `small` | Trying one episode or using modest hardware | About 480 MB. Faster, but names, accents, and noisy audio may need more correction |
| `turbo` | A newer Apple Silicon Mac, or Windows or Linux with a suitable GPU | About 3.2 GB on Apple Silicon, about 1.6 GB on Windows and Linux |
| `large-v3` | Name-heavy shows, strong accents, or uneven recordings | About 3.2 GB, with higher memory and processing needs |

Apple Silicon Mac uses MLX Whisper. Intel Mac, Windows, and Linux use faster-whisper. On Windows and Linux, CUDA with float16 is used when it is available; otherwise faster-whisper runs on CPU with int8. Models are stored in your operating system's user cache, not inside the skill folder.

Already have a rough transcript? Skip Whisper and the model download—the skill goes straight to checking and editing the text.

## What you get

- `transcript.md`: a transcript that is easy to edit or move into a notes app
- `transcript.html`: a readable page you can open directly
- `Unresolved Items`: included only when a name, term, or speaker still needs checking

The basic transcript structure is:

```markdown
# Episode title

## Summary

## Source notes

## Full transcript

## Unresolved items
```

The summary is an entry point. It never replaces the full transcript.

## How it works

1. Identify whether the source is a link, audio, video, or existing text, then confirm the output language.
2. Create a folder for the episode and preserve the original media and rough transcript.
3. Check the local transcription setup and show the model, download size, and storage location. Preparation starts only after approval.
4. Verify guest names and terminology against the episode page, then split long transcripts losslessly with the bundled script.
5. Fix punctuation, speaker labels, and obvious recognition errors one chunk at a time. This proofreading sends the transcript text to the AI assistant you are using.
6. Merge the chunks, check the beginning, ending, gaps, duplicates, and formatting, then create Markdown and HTML.

Claims, examples, caveats, and useful speaking habits stay in the transcript. If a speaker's identity cannot be confirmed, the skill uses a neutral label such as `Speaker A` instead of guessing.

Optional local diarization can add IDs such as `SPEAKER_00`. It stays off until you approve a separate download. The pyannote models are gated: accept their terms on Hugging Face and set `HF_TOKEN` (or `HUGGING_FACE_HUB_TOKEN`). Those IDs are not names; the skill still checks them against the episode page.

## Where files go

After local transcription is ready, the audio and models remain on your computer. Models live in the system user cache; episode files and transcripts stay in the folder created for that task.

Audio and transcription stay on your machine. Proofreading sends the transcript text to the AI assistant you are using, under that provider's data policy. The skill explains when it needs the network to fetch an episode or verify public information. Sending audio to a cloud transcription service requires your explicit approval.

Generated transcripts do not include usernames, email addresses, local computer paths, credentials, or internal logs. The introduction pages have no analytics scripts, tracking pixels, or remote fonts.

## What it does not do

- Bypass paywalls, sign-ins, DRM, or platform protections
- Guarantee automatic identification of every speaker
- Automatically install FFmpeg, CUDA, system package managers, or modify system settings
- Upload, sync, or publish generated files automatically
- Recommend shows or maintain listening history

## Troubleshooting

| Situation | What to do |
|---|---|
| First time processing audio or video | Review the setup plan, then choose `small`, `turbo`, or `large-v3` |
| FFmpeg, CUDA, or another system dependency is missing | Install the dependency shown in the report; the setup script does not modify system settings for you |
| An episode link cannot be read | Use a Xiaoyuzhou episode URL, an Apple Podcasts URL with `i=`, an RSS feed, or a direct audio file. A show page or sign-in page is refused |
| A speaker cannot be identified | Share the official episode page or correct name, or keep a neutral label |
| One chunk fails | Keep the completed chunks and retry only the failed one after fixing the cause |
| HTML is not generated | Keep the checked Markdown, then regenerate the HTML version |

## Repository contents

- [`SKILL.md`](./podcast-transcript-public/SKILL.md): the skill's instructions
- `README.md`: installation and usage guidance
- [`index.html`](./docs/index.html): the English GitHub Pages introduction
- [`zh.html`](./docs/zh.html): the Chinese GitHub Pages introduction
- `pyproject.toml`: the `podcast-transcript` command used by `pipx install`

## License

MIT License. Copyright (c) 2026 ZHUCH-520.
