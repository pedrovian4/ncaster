from pathlib import Path

from ncaster.convert import build_ffmpeg_cmd, parse_progress_us


def test_mov_to_mp4_uses_x264_and_crf():
    cmd = build_ffmpeg_cmd(
        Path("in.mov"), Path("out.mp4"), "mp4",
        quality="high", speed="slow", extra_flags=[], gpu=False,
    )
    assert "-c:v" in cmd and "libx264" in cmd
    assert "-crf" in cmd and "18" in cmd
    assert "-preset" in cmd and "slow" in cmd
    assert "-c:a" in cmd and "aac" in cmd


def test_audio_only_target_drops_video():
    cmd = build_ffmpeg_cmd(
        Path("in.mov"), Path("out.mp3"), "mp3",
        quality="high", speed="slow", extra_flags=[], gpu=False,
    )
    assert "-vn" in cmd
    assert "libmp3lame" in cmd


def test_gpu_swaps_to_nvenc():
    cmd = build_ffmpeg_cmd(
        Path("in.mov"), Path("out.mp4"), "mp4",
        quality="high", speed="slow", extra_flags=[], gpu=True,
    )
    assert "h264_nvenc" in cmd
    # preset is x264-specific and should not be forced for nvenc here
    assert "-preset" not in cmd


def test_opus_target_normalizes_audio():
    cmd = build_ffmpeg_cmd(
        Path("in.mov"), Path("out.webm"), "webm",
        quality="high", speed="medium", extra_flags=[], gpu=False,
    )
    assert "-ac" in cmd and "2" in cmd
    assert "-ar" in cmd and "48000" in cmd


def test_extra_flags_are_forwarded():
    cmd = build_ffmpeg_cmd(
        Path("in.mov"), Path("out.mp4"), "mp4",
        quality="high", speed="slow", extra_flags=["-vf", "scale=1280:-2"], gpu=False,
    )
    assert "scale=1280:-2" in cmd


def test_parse_progress_us():
    assert parse_progress_us("out_time_us=2500000") == 2.5
    assert parse_progress_us("frame=10") is None
