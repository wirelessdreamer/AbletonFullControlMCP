"""MCP tools for the mix-aware shaping stack.

Exposes the L2-L5 + KB layers (built as Python modules in PRs #24-#30)
to MCP clients. Every tool wraps an underlying async function from one
of the ``ableton_mcp.mix_*`` modules and applies the standard error
envelope ``{"status": "error", "error": "..."}`` on failure.

Tool surface (all prefixed ``mix_``):

  Discovery / vocabulary:
    mix_list_intents             — enumerate descriptor words
    mix_describe_intent          — get band + action for one word
    mix_classify_track_by_name   — instrument family from a track name

  Analysis (read-only; bounce + DSP):
    mix_spectrum_at_region       — per-track third-octave energy (L2.1)
    mix_masking_at_region        — focal money bands + competitors (L2.2)

  Action (write to Live):
    mix_propose_at_region        — structured EQ proposal (L4.1)
    mix_apply_proposal           — push proposal to Live (L4.2)

  Verification (round-trip):
    mix_snapshot_for_verification — baseline before mix_apply
    mix_verify_intent             — diff baseline vs after (L5)

Conversational loop the LLM is expected to run::

    1. arrangement_find_sections(focal_track) -> region
    2. mix_propose_at_region(focal, intent, *region) -> proposal
    3. (show proposal to user, get confirmation)
    4. mix_snapshot_for_verification(focal, *region) -> baseline
    5. mix_apply_proposal(proposal) -> applied
    6. mix_verify_intent(focal, intent, *region, baseline) -> result
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ..mix_analysis import mix_spectrum_at_region as _mix_spectrum_at_region
from ..mix_apply import mix_apply_proposal as _mix_apply_proposal
from ..mix_descriptors import (
    list_descriptors as _list_descriptors,
    resolve_descriptor as _resolve_descriptor,
)
from ..mix_knowledge import (
    classify_track_by_name as _classify_track_by_name,
)
from ..mix_masking import mix_masking_at_region as _mix_masking_at_region
from ..mix_propose import mix_propose_at_region as _mix_propose_at_region
from ..mix_verify import (
    mix_snapshot_for_verification as _mix_snapshot_for_verification,
    mix_verify_intent as _mix_verify_intent,
)


def _err(exc: Exception) -> dict[str, Any]:
    """Standard error envelope used across the MCP tool surface."""
    return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


def register(mcp: FastMCP) -> None:

    # ============================================================
    # Vocabulary / discovery
    # ============================================================

    @mcp.tool()
    async def mix_list_intents() -> dict[str, Any]:
        """List every mix-intent descriptor the propose / verify layers
        understand (cuts_through, buried, muddy, harsh, airy, ...).

        Use this to discover what words the user can ask for. Each
        descriptor has its band range, sign, action class, and a short
        human-readable description for context.
        """
        try:
            return {
                "status": "ok",
                "descriptors": [
                    {
                        "name": d.name,
                        "aliases": list(d.aliases),
                        "band_low_hz": d.band_low_hz,
                        "band_high_hz": d.band_high_hz,
                        "sign": d.sign,
                        "action_class": d.action_class,
                        "description": d.description,
                    }
                    for d in _list_descriptors()
                ],
            }
        except Exception as e:
            return _err(e)

    @mcp.tool()
    async def mix_describe_intent(intent: str) -> dict[str, Any]:
        """Look up one mix-intent descriptor by canonical name or alias.

        ``intent`` is case- and whitespace-tolerant: "Cut Through",
        "cut-through", "cuts_through" all resolve to the same descriptor.
        Returns ``{"status": "error", "error": ...}`` if the word isn't
        a recognised intent.
        """
        try:
            d = _resolve_descriptor(intent)
        except KeyError as e:
            return {"status": "error", "error": str(e)}
        except Exception as e:
            return _err(e)
        return {
            "status": "ok",
            "name": d.name,
            "aliases": list(d.aliases),
            "band_low_hz": d.band_low_hz,
            "band_high_hz": d.band_high_hz,
            "sign": d.sign,
            "action_class": d.action_class,
            "description": d.description,
        }

    @mcp.tool()
    async def mix_classify_track_by_name(track_name: str) -> dict[str, Any]:
        """Best-effort instrument-family classification from a track name.

        Returns the matched ``InstrumentMoneyBands`` entry (kick, bass,
        snare, hihat, lead_vocal, lead_guitar, rhythm_guitar, piano) or
        ``{"matched": false}`` if no alias fires. Most-specific match
        wins ("Bass Drum" -> kick, "Lead Guitar" -> lead_guitar).
        """
        try:
            inst = _classify_track_by_name(track_name)
        except Exception as e:
            return _err(e)
        if inst is None:
            return {"status": "ok", "matched": False,
                    "queried_name": track_name}
        return {
            "status": "ok", "matched": True,
            "queried_name": track_name,
            "name": inst.name,
            "aliases": list(inst.aliases),
            "body_low_hz": inst.body_low_hz,
            "body_high_hz": inst.body_high_hz,
            "presence_low_hz": inst.presence_low_hz,
            "presence_high_hz": inst.presence_high_hz,
            "notes": inst.notes,
        }

    # ============================================================
    # Analysis (read-only; bounce + DSP)
    # ============================================================

    @mcp.tool()
    async def mix_spectrum_at_region(
        start_beats: float,
        end_beats: float,
        output_dir: str | None = None,
        target_sr: int = 22050,
        warmup_sec: float = 0.0,
    ) -> dict[str, Any]:
        """Per-track third-octave spectrum for every active track over a beat region.

        Bounces every un-muted, audio-producing track for the region
        (via L1.3), runs third-octave spectral analysis on each stem,
        and returns ``{region, bands, tracks: [{track_index, name,
        energy_db_per_band, peak_db, rms_db, spectral_centroid_hz,
        top_bands}, ...]}``.

        Layer 2.1 of the mix-aware shaping stack. The downstream layers
        build on this output to compute masking and propose changes.
        """
        try:
            r = await _mix_spectrum_at_region(
                start_beats=float(start_beats),
                end_beats=float(end_beats),
                output_dir=output_dir, target_sr=int(target_sr),
                warmup_sec=float(warmup_sec),
            )
            r["status"] = "ok"
            return r
        except Exception as e:
            return _err(e)

    @mcp.tool()
    async def mix_masking_at_region(
        focal_track_index: int,
        start_beats: float,
        end_beats: float,
        output_dir: str | None = None,
        target_sr: int = 22050,
        top_n_money_bands: int = 5,
        warmup_sec: float = 0.0,
    ) -> dict[str, Any]:
        """For a focal track + region, score how much each other track is masking it.

        Bounces every active track for the region, identifies the focal's
        top ``top_n_money_bands`` highest-energy bands, then for each
        competitor scores per-band overlap weighted by perceptual band
        importance (presence band 2-5 kHz weighted highest).

        Returns ``{focal_track, focal_money_bands, competing_tracks:
        [{track_index, name, masking_score, per_band: [...]}, ...]}``.
        Layer 2.2 of the mix-aware shaping stack.
        """
        try:
            r = await _mix_masking_at_region(
                focal_track_index=int(focal_track_index),
                start_beats=float(start_beats),
                end_beats=float(end_beats),
                output_dir=output_dir, target_sr=int(target_sr),
                top_n_money_bands=int(top_n_money_bands),
                warmup_sec=float(warmup_sec),
            )
            r["status"] = "ok"
            return r
        except ValueError as e:
            return {"status": "error", "error": str(e)}
        except Exception as e:
            return _err(e)

    # ============================================================
    # Proposal (no Live write)
    # ============================================================

    @mcp.tool()
    async def mix_propose_at_region(
        focal_track_index: int,
        intent: str,
        start_beats: float,
        end_beats: float,
        output_dir: str | None = None,
        target_sr: int = 22050,
        warmup_sec: float = 0.0,
    ) -> dict[str, Any]:
        """Generate a structured EQ proposal for a mix intent over a region.

        ``intent`` is a word like ``"cuts_through"``, ``"buried"``,
        ``"muddy"``, ``"harsh"``, ``"airy"``, ... (see
        :func:`mix_list_intents`). Case + whitespace tolerant.

        Bounces, analyzes masking, then dispatches by the descriptor's
        ``action_class`` to produce 0-N concrete actions (eq_cut,
        eq_boost, high_pass, high_shelf, low_shelf, ...). Each action
        carries a rationale string referencing the actual masking
        numbers. **Does NOT apply** -- pass the result to
        ``mix_apply_proposal``.

        Layer 4.1 of the mix-aware shaping stack.
        """
        try:
            r = await _mix_propose_at_region(
                focal_track_index=int(focal_track_index),
                intent=intent,
                start_beats=float(start_beats),
                end_beats=float(end_beats),
                output_dir=output_dir, target_sr=int(target_sr),
                warmup_sec=float(warmup_sec),
            )
            r["status"] = "ok"
            return r
        except KeyError as e:
            return {"status": "error", "error": str(e)}
        except ValueError as e:
            return {"status": "error", "error": str(e)}
        except Exception as e:
            return _err(e)

    # ============================================================
    # Application (writes to Live)
    # ============================================================

    @mcp.tool()
    async def mix_apply_proposal(
        proposal: dict[str, Any],
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Apply a proposal from ``mix_propose_at_region`` to Live.

        Per action, locates (or inserts) an EQ Eight on the target
        track, picks a free band, and sets filter type / frequency /
        gain / Q / on. ``dry_run=True`` returns the full plan WITHOUT
        making OSC writes -- ideal for confirming the change with the
        user before committing.

        Action kinds without a v1 apply path (``de_ess``,
        ``compress_attack``) land in the ``skipped`` list with a reason
        rather than failing the whole apply.

        Layer 4.2 of the mix-aware shaping stack.
        """
        try:
            r = await _mix_apply_proposal(
                proposal=proposal, dry_run=bool(dry_run),
            )
            r["status"] = "ok"
            return r
        except Exception as e:
            return _err(e)

    # ============================================================
    # Verification (round-trip)
    # ============================================================

    @mcp.tool()
    async def mix_snapshot_for_verification(
        focal_track_index: int,
        start_beats: float,
        end_beats: float,
        output_dir: str | None = None,
        target_sr: int = 22050,
        warmup_sec: float = 0.0,
    ) -> dict[str, Any]:
        """Take a baseline masking snapshot to be diffed later by ``mix_verify_intent``.

        Call this BEFORE ``mix_apply_proposal`` so you have a reference
        to compare against. Returns the same shape as
        ``mix_masking_at_region``. Pass the result back as the
        ``baseline_snapshot`` argument to ``mix_verify_intent``.
        """
        try:
            r = await _mix_snapshot_for_verification(
                focal_track_index=int(focal_track_index),
                start_beats=float(start_beats),
                end_beats=float(end_beats),
                output_dir=output_dir, target_sr=int(target_sr),
                warmup_sec=float(warmup_sec),
            )
            if isinstance(r, dict):
                r["status"] = "ok"
            return r
        except ValueError as e:
            return {"status": "error", "error": str(e)}
        except Exception as e:
            return _err(e)

    @mcp.tool()
    async def mix_verify_intent(
        focal_track_index: int,
        intent: str,
        start_beats: float,
        end_beats: float,
        baseline_snapshot: dict[str, Any] | None = None,
        output_dir: str | None = None,
        target_sr: int = 22050,
        warmup_sec: float = 0.0,
    ) -> dict[str, Any]:
        """Verify whether a mix intent was achieved after ``mix_apply_proposal``.

        Re-bounces the region, computes the same masking analysis as
        before, and diffs against ``baseline_snapshot`` (the result of
        ``mix_snapshot_for_verification`` taken BEFORE the apply).
        Returns ``{intent_achieved, regressed, summary,
        per_competitor_diffs, ...}`` with a natural-language summary
        suitable for showing the user.

        If ``baseline_snapshot`` is omitted the function returns the
        after-snapshot alone with ``baseline_missing=True`` -- no diff
        is possible without a baseline.

        Layer 5 of the mix-aware shaping stack.
        """
        try:
            r = await _mix_verify_intent(
                focal_track_index=int(focal_track_index),
                intent=intent,
                start_beats=float(start_beats),
                end_beats=float(end_beats),
                baseline_snapshot=baseline_snapshot,
                output_dir=output_dir, target_sr=int(target_sr),
                warmup_sec=float(warmup_sec),
            )
            if isinstance(r, dict):
                r["status"] = "ok"
            return r
        except KeyError as e:
            return {"status": "error", "error": str(e)}
        except ValueError as e:
            return {"status": "error", "error": str(e)}
        except Exception as e:
            return _err(e)
