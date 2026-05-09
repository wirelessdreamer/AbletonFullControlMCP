"""Conversational song-flow: analyze · transpose · stem · variations.

Composable building blocks for "look at the active Ableton song, transpose
it, stem-separate it, and produce instrument-up variations". Each public
function corresponds to one MCP tool registered in
:mod:`ableton_mcp.tools.song_flow`.

The conversational layer is the LLM client itself: it chains these tools
based on the user's request. There is no monolithic pipeline tool — keeping
the surface orthogonal lets the user say "just transpose" or "just remove
vocals" without wading through unused steps.
"""

from .analyze import analyze_song
from .import_to_live import import_variations_to_live
from .key import PITCH_CLASS, normalize_key, semitone_delta
from .load_to_arrangement import load_wav_to_arrangement
from .transpose import transpose_song
from .variations import make_variations

__all__ = [
    "PITCH_CLASS",
    "analyze_song",
    "import_variations_to_live",
    "load_wav_to_arrangement",
    "make_variations",
    "normalize_key",
    "semitone_delta",
    "transpose_song",
]
