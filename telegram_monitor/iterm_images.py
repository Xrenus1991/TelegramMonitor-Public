"""
iTerm2 inline image display via OSC 1337 protocol.
Displays images, GIFs (animated), and video thumbnails directly in the terminal.
Falls back gracefully in non-iTerm2 terminals.
"""

import base64
import os
import subprocess
import sys
from pathlib import Path

# Detect terminal capabilities once at import
IS_ITERM2 = os.environ.get("TERM_PROGRAM") == "iTerm.app"
IS_TMUX = bool(os.environ.get("TMUX"))


def _osc() -> bytes:
    # OSC 1337 opener: in tmux, wrap in DCS passthrough (\033Ptmux;\033)
    # so the escape reaches iTerm2 through the multiplexer.
    if IS_TMUX:
        return b"\033Ptmux;\033\033]"
    return b"\033]"


def _st() -> bytes:
    # String terminator: BEL (\a) ends the OSC sequence.
    # In tmux, also close the DCS passthrough wrapper.
    if IS_TMUX:
        return b"\a\033\\"
    return b"\a"


def can_display_inline() -> bool:
    """Check if the terminal supports inline images."""
    return IS_ITERM2


def display_image(filepath: str | Path, width: str = "40", height: str = "auto",
                  preserve_aspect: bool = True, fp=None) -> bool:
    """
    Display an image file inline in iTerm2.
    Returns True if displayed, False if not supported.
    """
    if not IS_ITERM2:
        return False

    filepath = Path(filepath)
    if not filepath.exists():
        return False

    try:
        data = filepath.read_bytes()
    except OSError:
        return False

    if fp is None:
        fp = sys.stdout.buffer

    name_b64 = base64.b64encode(filepath.name.encode()).decode("ascii")
    data_b64 = base64.b64encode(data).decode("ascii")

    params = f"inline=1;size={len(data)};name={name_b64};width={width};height={height}"
    if not preserve_aspect:
        params += ";preserveAspectRatio=0"

    fp.write(_osc())
    fp.write(f"1337;File={params}:{data_b64}".encode("ascii"))
    fp.write(_st())
    fp.write(b"\n")
    fp.flush()
    return True


def display_bytes(data: bytes, filename: str = "image", width: str = "40",
                  height: str = "auto", fp=None) -> bool:
    """Display raw image bytes inline in iTerm2."""
    if not IS_ITERM2 or not data:
        return False

    if fp is None:
        fp = sys.stdout.buffer

    name_b64 = base64.b64encode(filename.encode()).decode("ascii")
    data_b64 = base64.b64encode(data).decode("ascii")

    params = f"inline=1;size={len(data)};name={name_b64};width={width};height={height}"

    fp.write(_osc())
    fp.write(f"1337;File={params}:{data_b64}".encode("ascii"))
    fp.write(_st())
    fp.write(b"\n")
    fp.flush()
    return True


def get_video_thumbnail(video_path: str | Path) -> bytes | None:
    """
    Extract a thumbnail from a video file using ffmpeg.
    Returns JPEG bytes or None if ffmpeg isn't available.
    """
    video_path = Path(video_path)
    if not video_path.exists():
        return None

    try:
        result = subprocess.run(
            [
                "ffmpeg", "-i", str(video_path),
                "-ss", "00:00:01",      # 1 second in
                "-frames:v", "1",       # single frame
                "-f", "image2pipe",     # pipe output
                "-vcodec", "mjpeg",     # JPEG format
                "-vf", "scale=320:-1",  # 320px wide, auto height
                "-loglevel", "quiet",
                "pipe:1",
            ],
            capture_output=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def display_video_thumbnail(video_path: str | Path, width: str = "30",
                            height: str = "auto", fp=None) -> bool:
    """Extract and display a video thumbnail inline."""
    thumb = get_video_thumbnail(video_path)
    if thumb:
        return display_bytes(thumb, filename="video_thumb.jpg", width=width,
                             height=height, fp=fp)
    return False
