# ncaster

A high-quality CLI tool to **cast (convert) video and audio between formats**, powered by FFmpeg. It ships with an interactive mode that fuzzy-finds media files in a directory (via `fzf`) and walks you through format, quality, and speed — plus a scriptable `cast` command for batch jobs.

```
╭─────────────────────────────────╮
│ ncaster  ~/Videos  · fzf         │
╰─────────────────────────────────╯
```

## Features

- **Interactive mode** — run `ncaster` in any directory and pick files with a fuzzy finder, then choose the target format, quality, and encoding speed through arrow-key menus.
- **Fuzzy file selection** — uses [`fzf`](https://github.com/junegunn/fzf) when available (multi-select with `TAB`), and falls back to a checkbox list otherwise.
- **Batch conversion** — convert many files at once with the non-interactive `cast` command.
- **Quality presets** — `lossless`, `high`, `medium`, `low`, mapped to sensible CRF / bitrate values per codec.
- **Speed presets** — trade encoding time for smaller files (`veryslow` → `ultrafast`).
- **GPU acceleration** — optional NVENC encoding via `--gpu`.
- **Live progress bars** with ETA, plus a size-reduction summary after each job.
- **Audio extraction** — pull audio out of a video straight into `mp3`, `flac`, `opus`, etc.

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

## Installation

Install globally with [uv](https://github.com/astral-sh/uv):

```bash
git clone https://github.com/pedrovian4/ncaster.git
cd ncaster
uv tool install .
```

This puts an `ncaster` executable on your `PATH` (typically `~/.local/bin/ncaster`).

To update after pulling changes:

```bash
uv tool install . --reinstall
```

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

## License

MIT
