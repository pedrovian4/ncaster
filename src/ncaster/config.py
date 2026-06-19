"""Static configuration: media extensions, format/codec profiles, and
quality/speed/language tables shared across the application."""

# Extensions scanned in interactive mode.
MEDIA_EXTENSIONS = {
    ".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".wmv", ".flv",
    ".ts", ".mts", ".m2ts", ".3gp", ".ogv", ".vob", ".mpg", ".mpeg",
    ".mp3", ".aac", ".flac", ".wav", ".opus", ".ogg", ".m4a", ".wma", ".aiff",
}

# Target format → codecs and muxer-specific audio flags.
FORMATS = {
    "mp4":  {"vcodec": "libx264",    "acodec": "aac",        "ext": "mp4",  "audio_flags": []},
    "mov":  {"vcodec": "libx264",    "acodec": "aac",        "ext": "mov",  "audio_flags": []},
    "mkv":  {"vcodec": "libx265",    "acodec": "libopus",    "ext": "mkv",  "audio_flags": ["-ac", "2", "-ar", "48000"]},
    "webm": {"vcodec": "libvpx-vp9", "acodec": "libopus",    "ext": "webm", "audio_flags": ["-ac", "2", "-ar", "48000"]},
    "avi":  {"vcodec": "mpeg4",      "acodec": "mp3",        "ext": "avi",  "audio_flags": []},
    "gif":  {"vcodec": "gif",        "acodec": None,         "ext": "gif",  "audio_flags": []},
    "mp3":  {"vcodec": None,         "acodec": "libmp3lame", "ext": "mp3",  "audio_flags": []},
    "aac":  {"vcodec": None,         "acodec": "aac",        "ext": "aac",  "audio_flags": []},
    "flac": {"vcodec": None,         "acodec": "flac",       "ext": "flac", "audio_flags": []},
    "wav":  {"vcodec": None,         "acodec": "pcm_s16le",  "ext": "wav",  "audio_flags": []},
    "opus": {"vcodec": None,         "acodec": "libopus",    "ext": "opus", "audio_flags": ["-ac", "2", "-ar", "48000"]},
}

# CRF tables per video codec; lower = better quality / larger file.
QUALITY_CRF = {
    "libx264":    {"lossless": 0,  "high": 18, "medium": 23, "low": 28},
    "libx265":    {"lossless": 0,  "high": 20, "medium": 26, "low": 32},
    "libvpx-vp9": {"lossless": 0,  "high": 25, "medium": 33, "low": 42},
    "mpeg4":      {"lossless": 1,  "high": 2,  "medium": 5,  "low": 10},
}

AUDIO_BITRATE = {"high": "320k", "medium": "192k", "low": "128k"}

PRESETS = {
    "libx264": ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"],
    "libx265": ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"],
}

QUALITIES = ["lossless", "high", "medium", "low"]

# --- Transcription (local Whisper) --------------------------------------
WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v3"]
# Default languages for now: auto-detect, English, Portuguese.
TRANSCRIBE_LANGS = {"auto": "Auto-detect", "en": "English", "pt": "Portuguese"}
TRANSCRIPT_FORMATS = ["srt", "vtt", "txt", "json"]
