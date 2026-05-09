"""Phase 6 tools: AI music generation + stem splitting + Live import.

Exposes a generic `gen_*` surface backed by a pluggable Generator registry
(Suno / MusicGen / Stable Audio / ...) plus a Demucs stem-splitter and a
helper that creates one fresh audio track per stem.

Backwards-compat aliases `suno_generate` and `suno_import_stems` keep the
existing test contract green while routing through the new plumbing.

NOTE: actually loading a wav into a clip on the new track requires the
Phase 2 browser/sample bridge (`browser_load_sample`). Until that lands,
these tools return the audio paths + new track indices and a clear
"load via browser_load_sample once available" hint — they do NOT block.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from ..generators import GeneratorError, GeneratorNotConfigured
from ..generators import registry as gen_registry
from ..osc_client import get_client
from ..stems import demucs as stems_demucs

DEFAULT_OUTPUT_DIR = Path("data/generated")


def _result_to_dict(res: Any) -> dict[str, Any]:
    return {
        "audio_path": res.audio_path,
        "duration": res.duration,
        "lyrics": res.lyrics,
        "provider": res.provider,
        "metadata": res.metadata,
    }


async def _create_audio_track(at_index: int, name: str) -> int:
    """Create an audio track (matching tools/tracks.py semantics). Returns new index."""
    client = await get_client()
    client.send("/live/song/create_audio_track", int(at_index))
    n = int((await client.request("/live/song/get/num_tracks"))[0])
    new_index = n - 1 if at_index < 0 else at_index
    client.send("/live/track/set/name", new_index, name)
    return new_index


async def _project_tempo() -> float:
    client = await get_client()
    args = await client.request("/live/song/get/tempo")
    return float(args[0])


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def gen_list_providers() -> dict[str, Any]:
        """[Phase 6] List registered AI music generators and whether each is configured.

        A provider is "ready" when its required env var or local dependency is
        present — calling gen_generate with a non-ready provider raises
        GeneratorNotConfigured with a clear hint about what to set.
        """
        out: list[dict[str, Any]] = []
        for name in gen_registry.list_names():
            cls = gen_registry.REGISTRY[name]
            try:
                inst = cls()
                ready = bool(inst.is_configured())
            except Exception as exc:  # pragma: no cover - defensive
                ready = False
                inst = None
                err: str | None = str(exc)
            else:
                err = None
            out.append(
                {
                    "name": name,
                    "ready": ready,
                    "class": cls.__name__,
                    "error": err,
                }
            )
        return {"providers": out, "count": len(out)}

    @mcp.tool()
    async def gen_generate(
        provider: str,
        prompt: str,
        lyrics: str | None = None,
        duration: float | None = None,
        output_path: str | None = None,
    ) -> dict[str, Any]:
        """[Phase 6] Generate audio using the named provider.

        `provider` is one of the names from gen_list_providers (suno, musicgen,
        stable_audio). The resulting wav/mp3 is saved under `data/generated/`
        unless `output_path` is given. Raises a structured error dict with
        `status: not_configured` if the provider's env var/dep is missing.
        """
        try:
            gen = gen_registry.get(provider)
        except KeyError as exc:
            return {"status": "unknown_provider", "error": str(exc)}

        try:
            kwargs: dict[str, Any] = {}
            if output_path is not None:
                kwargs["output_path"] = output_path
            result = await gen.generate(
                prompt, lyrics=lyrics, duration=duration, **kwargs
            )
        except GeneratorNotConfigured as exc:
            return {
                "status": "not_configured",
                "provider": provider,
                "error": str(exc),
            }
        except GeneratorError as exc:
            return {"status": "error", "provider": provider, "error": str(exc)}

        return {"status": "ok", **_result_to_dict(result)}

    @mcp.tool()
    async def stems_split(
        audio_path: str,
        n_stems: int = 6,
        model: str | None = None,
    ) -> dict[str, Any]:
        """[Phase 6] Run Demucs to split an audio file into stems.

        Defaults to ``n_stems=6`` (``htdemucs_6s``) which produces
        drums/bass/other/vocals + guitar + piano. The 6-stem split
        gives real per-instrument output and is preferred for downstream
        practice-track / boosted-stem workflows. Pass ``n_stems=4`` for
        the older ``htdemucs`` (drums/bass/other/vocals) if you want a
        smaller / faster split. Pass ``model="htdemucs_ft"`` (or any
        other Demucs model name) to override the n_stems default.

        Demucs runs on GPU when available (``torch.cuda.is_available()``)
        and falls back to CPU automatically. To enable GPU, install a
        CUDA torch build — see the docstring on
        ``ableton_mcp.stems.demucs.split_stems`` for the install commands.

        Output is written under ``data/stems/<model>/<basename>/<stem>.wav``.
        If demucs isn't installed in this venv, returns a structured error
        with the pip install hint.
        """
        if model is None:
            if int(n_stems) == 4:
                model = "htdemucs"
            elif int(n_stems) == 6:
                model = "htdemucs_6s"
            else:
                return {
                    "status": "error",
                    "error": f"n_stems must be 4 or 6 (got {n_stems}); pass an explicit model= for other Demucs variants",
                }
        try:
            stems = await stems_demucs.split_stems(audio_path, model=model)
        except stems_demucs.DemucsNotInstalled as exc:
            return {"status": "not_configured", "error": str(exc)}
        except stems_demucs.StemsError as exc:
            return {"status": "error", "error": str(exc)}
        return {
            "status": "ok",
            "model": model,
            "n_stems": int(n_stems) if model in ("htdemucs", "htdemucs_6s") else len(stems),
            "audio_path": audio_path,
            "stems": [{"name": s.name, "path": s.path} for s in stems],
        }

    @mcp.tool()
    async def stems_import_to_live(
        stems: list[dict[str, Any]], at_track_index: int = -1
    ) -> dict[str, Any]:
        """[Phase 6] Create a fresh audio track per stem and report the mapping.

        `stems` is a list of dicts with keys `name` (vocals/drums/bass/other)
        and `path` (wav). For each, a new audio track named `Stem: <name>` is
        created in Live at the project tempo. Returns a list of
        {name, path, track_index} entries.

        Loading the wav INTO a clip on the new track requires the browser
        bridge (`browser_load_sample`) which is owned by another agent — the
        returned dict includes a clear `next_step` hint until that ships.
        """
        if not stems:
            return {"status": "error", "error": "stems list is empty"}

        try:
            tempo = await _project_tempo()
        except Exception as exc:
            return {"status": "error", "error": f"failed to read project tempo: {exc}"}

        mapping: list[dict[str, Any]] = []
        cursor = at_track_index
        for stem in stems:
            name = str(stem.get("name") or "stem")
            path = str(stem.get("path") or "")
            if not path:
                continue
            new_index = await _create_audio_track(cursor, f"Stem: {name}")
            mapping.append(
                {"name": name, "path": path, "track_index": new_index}
            )
            # If user asked for a specific insertion index, walk forward so
            # later stems land in adjacent slots.
            if cursor >= 0:
                cursor = new_index + 1

        return {
            "status": "partial",
            "tempo": tempo,
            "tracks": mapping,
            "next_step": (
                "Each stem has a fresh audio track ready. Call "
                "browser_load_sample(path, track_index) for every entry once "
                "the Phase 2 browser bridge is available to put the wav into a clip."
            ),
        }

    # --- Backwards-compat aliases (keep test_server_registers.py green) -------

    @mcp.tool()
    async def suno_generate(
        prompt: str, lyrics: str | None = None
    ) -> dict[str, Any]:
        """[Phase 6] Generate a song with Suno. Alias for gen_generate("suno", ...)."""
        return await gen_generate(prompt=prompt, lyrics=lyrics, provider="suno")

    @mcp.tool()
    async def suno_import_stems(
        audio_path: str,
        target_track_start: int = -1,
        demucs_model: str = "htdemucs",
    ) -> dict[str, Any]:
        """[Phase 6] Demucs-split an audio file and create one audio track per stem.

        Backwards-compat alias: combines stems_split + stems_import_to_live.
        Loading the wav into a clip still needs the Phase 2 browser bridge.
        """
        split = await stems_split(audio_path=audio_path, model=demucs_model)
        if split.get("status") != "ok":
            return split
        return await stems_import_to_live(
            stems=split["stems"], at_track_index=target_track_start
        )
