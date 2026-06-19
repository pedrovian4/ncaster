# ncaster

**A command-line companion for content creators editing audiovisual productions for social media and YouTube.** ncaster takes the repetitive media chores out of your editing workflow: converting clips into the right format for each platform, shrinking files so they upload fast, pulling audio for podcasts and Reels, and generating subtitles locally so your videos are accessible and watchable on mute.

It's built on FFmpeg and local Whisper, with an interactive mode that fuzzy-finds your footage (via `fzf`) and walks you through everything — plus scriptable commands for batching an entire shoot folder.

```
╭─────────────────────────────────╮
│ ncaster  ~/Videos  · fzf         │
╰─────────────────────────────────╯
```

## Why creators use it

- 🎬 **Deliver to any platform** — convert recordings to MP4 (Reels / Shorts / YouTube), WebM, MOV, GIF previews, and more, without memorizing FFmpeg flags.
- 📉 **Upload faster** — quality/speed presets compress footage hard while keeping it crisp, and every job prints how much smaller the file got.
- 🎙️ **Repurpose for audio** — extract clean audio tracks (`mp3`, `flac`, `opus`…) for podcasts, voiceovers, or music platforms.
- 💬 **Subtitles & captions, offline** — auto-generate `srt` / `vtt` (and `txt` / `json`) with local Whisper. Great for accessibility and the ~80% of social feeds watched on mute. Default languages: **English** and **Portuguese** (plus auto-detect).
- 📁 **Batch a whole shoot** — point it at a folder, multi-select clips, and process them all in one pass.

## Features

- **Interactive mode** — run `ncaster` in any directory, pick files with a fuzzy finder, then choose convert or transcribe through arrow-key menus.
- **Fuzzy file selection** — uses [`fzf`](https://github.com/junegunn/fzf) when available (multi-select with `TAB`), and falls back to a checkbox list otherwise.
- **Batch conversion** — convert many files at once with the non-interactive `cast` command.
- **Quality presets** — `lossless`, `high`, `medium`, `low`, mapped to sensible CRF / bitrate values per codec.
- **Speed presets** — trade encoding time for smaller files (`veryslow` → `ultrafast`).
- **GPU acceleration** — optional NVENC encoding via `--gpu`.
- **Live progress bars** with ETA, plus a size-reduction summary after each job.
- **Audio extraction** — pull audio out of a video straight into `mp3`, `flac`, `opus`, etc.
- **Local transcription** — speech-to-text with [faster-whisper](https://github.com/SYSTRAN/faster-whisper), running fully offline (no API, nothing uploaded). Outputs `srt`, `vtt`, `txt`, or `json`.
- **AI editing drafts** — from a transcript, generate a per-segment **overlay plan** for short-form video (the *Narrative Visual* style): what B-roll / image / graphic to show over the speaker as they say each line. Uses OpenAI (lightweight `gpt-4o-mini`) on the transcript text only — your audio/video never leaves your machine.

## Supported formats

| Format | Video codec  | Audio codec  | Type      |
| ------ | ------------ | ------------ | --------- |
| mp4    | libx264      | aac          | video     |
| mov    | libx264      | aac          | video     |
| mkv    | libx265      | libopus      | video     |
| webm   | libvpx-vp9   | libopus      | video     |
| avi    | mpeg4        | mp3          | video     |
| gif    | gif          | —            | gif/image |
| mp3    | —            | libmp3lame   | audio     |
| aac    | —            | aac          | audio     |
| flac   | —            | flac         | audio     |
| wav    | —            | pcm_s16le    | audio     |
| opus   | —            | libopus      | audio     |

## Requirements

- **Python** ≥ 3.11
- **[FFmpeg](https://ffmpeg.org/)** (`ffmpeg` and `ffprobe` on your `PATH`)
- **[fzf](https://github.com/junegunn/fzf)** *(optional)* — enables fuzzy file selection in interactive mode
- **[faster-whisper](https://github.com/SYSTRAN/faster-whisper)** *(optional)* — required only for `transcribe`; installed via the `[transcribe]` extra
- **[openai](https://pypi.org/project/openai/)** *(optional)* — required only for AI editing drafts; installed via the `[ai]` extra. Needs an `OPENAI_API_KEY` (see [Configuration](#configuration))

## Installation

Install globally with [uv](https://github.com/astral-sh/uv):

```bash
git clone https://github.com/pedrovian4/ncaster.git
cd ncaster

# core (convert only)
uv tool install .

# with transcription support
uv tool install ".[transcribe]"

# everything (convert + transcribe + AI editing drafts)
uv tool install ".[transcribe,ai]"
```

This puts an `ncaster` executable on your `PATH` (typically `~/.local/bin/ncaster`).

To update after pulling changes, append `--reinstall`:

```bash
uv tool install ".[transcribe,ai]" --reinstall
```

## Configuration

AI editing drafts need an OpenAI API key. ncaster looks for it in
`OPENAI_API_KEY` (env), then `~/.config/ncaster/.env`, then a local `.env`.
If none is set, it asks you to paste one the first time it's needed and saves
it to `~/.config/ncaster/.env`.

```bash
ncaster config            # show status, or prompt for a key if missing
ncaster config --show     # show the (masked) key and config file path
ncaster config --set-key  # replace the stored key
```

> The key is used **only** for the text-based editing drafts. Transcription is
> always local — no audio or video is ever sent anywhere.

## Usage

### Interactive mode

```bash
ncaster                 # scan the current directory
ncaster .               # same as above
ncaster ~/Videos        # scan another directory
ncaster -r ~/Videos     # scan recursively
```

In the fuzzy finder: **type to filter**, `TAB` / `Shift-TAB` to select multiple files, `ENTER` to confirm, `ESC` to cancel. Then choose format → quality → speed → output directory, review the summary, and confirm.

### Scriptable conversion

```bash
# MOV → MP4, high quality
ncaster cast video.mov -f mp4

# Batch convert all MOV files to MP4
ncaster cast *.mov -f mp4 -q high -s slow

# Make a GIF on the Desktop
ncaster cast clip.mp4 -f gif -o ~/Desktop

# Extract audio from a video as MP3
ncaster cast talk.mov -f mp3 -q high

# GPU-accelerated encode
ncaster cast video.mov -f webm --gpu
```

### Transcription (local Whisper)

Runs entirely offline via `faster-whisper` — no API or network needed after the model downloads on first use. Default languages are **English** and **Portuguese**, plus `auto` detection.

```bash
# Auto-detect language, SRT subtitles next to the video
ncaster transcribe talk.mp4

# Portuguese lecture → SRT
ncaster transcribe aula.mov -l pt -f srt

# English podcast → plain text, larger/more accurate model
ncaster transcribe podcast.mp3 -l en -m medium -f txt
```

| Option             | Default  | Description                                        |
| ------------------ | -------- | -------------------------------------------------- |
| `-l, --language`   | `auto`   | `auto`, `en`, `pt`                                 |
| `-m, --model`      | `small`  | `tiny`, `base`, `small`, `medium`, `large-v3`      |
| `-f, --format`     | `srt`    | `srt`, `vtt`, `txt`, `json`                        |
| `-o, --output-dir` | input    | Output directory                                   |

### AI editing drafts (Narrative Visual)

Turn a transcript into an overlay plan for vertical short-form video. For each
spoken segment, ncaster proposes a visual (B-roll, image, graphic, or text
overlay) to show over the speaker — perfect as a starting point for editing
Shorts, Reels, and TikToks. It writes a `<name>.draft.md` next to the video.

In **interactive mode**, after transcribing you're asked *"Generate an AI
editing draft?"* — pick the style and ncaster does the rest. From the CLI:

```bash
# Transcribe, then generate the overlay draft in one go
ncaster transcribe short.mp4 --draft narrative-visual

# Portuguese Reel
ncaster transcribe reel.mp4 -l pt --draft narrative-visual

# Reuse an existing transcript (json/srt/vtt) — skips Whisper
ncaster transcribe short.mp4 --reuse --draft narrative-visual
```

If a transcript already exists for the file, interactive mode asks whether to
**reuse it or transcribe again**, and the CLI reuses it with `--reuse`. If the
OpenAI key is rejected, ncaster tells you it was wrong and asks for a new one.

Example draft entry:

```markdown
## 00:00:00,000 → 00:00:11,000
🗣️  And so my fellow Americans, ask not what your country can do for you…
🎬  **Overlay:** A waving American flag with the sun setting in the background.
```

The model is configurable via `OPENAI_MODEL` (default `gpt-4o-mini`).

### Other commands

```bash
ncaster formats         # list supported formats and codecs
ncaster info video.mov  # show media info via ffprobe
```

## `cast` options

| Option              | Default        | Description                                          |
| ------------------- | -------------- | ---------------------------------------------------- |
| `-f, --format`      | *(required)*   | Target format                                        |
| `-q, --quality`     | `high`         | `lossless`, `high`, `medium`, `low`                  |
| `-s, --speed`       | `slow`         | Encoding preset (`veryslow` → `ultrafast`)           |
| `-o, --output-dir`  | same as input  | Output directory                                     |
| `--suffix`          | —              | Suffix appended to the output filename               |
| `--gpu`             | off            | Use NVENC GPU acceleration                           |
| `--dry-run`         | off            | Print the FFmpeg commands without running them       |
| `--ffmpeg-flag`     | —              | Forward a raw flag to FFmpeg (repeatable)            |

## Project structure

```
src/ncaster/
├── __init__.py      # package version + cli export
├── __main__.py      # python -m ncaster
├── config.py        # format/codec profiles, quality & language tables
├── console.py       # shared Rich console
├── settings.py      # .env loading + OpenAI key management
├── probe.py         # ffprobe helpers + human-readable formatting
├── convert.py       # ffmpeg command building & progress-tracked runs
├── transcribe.py    # local Whisper transcription + subtitle writers
├── editing.py       # AI editing drafts (Narrative Visual) via OpenAI
├── interactive.py   # directory scan, fzf picker, guided flows
└── cli.py           # click command group and subcommands
tests/               # unit tests for the pure logic
```

## Development

```bash
uv run --extra dev --extra transcribe --extra ai pytest   # run the test suite
uv run python -m ncaster --help                           # run from source
```

## License

MIT
