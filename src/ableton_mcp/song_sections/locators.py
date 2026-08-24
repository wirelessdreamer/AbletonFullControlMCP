"""Idempotent Ableton arrangement-locator writer for detected sections.

Live's LOM has no "add cue at time" call — ``Song.set_or_delete_cue()``
TOGGLES a cue point at the current playhead position (deleting any cue
already there), and ``CuePoint.time`` is read-only. The write recipe is
therefore: stop transport → seek to the target beat → toggle → re-fetch the
cue list → find the new cue by time → rename it. AbletonOSC's cue list is
in CREATION order, not time order, so every rename re-fetches and diffs by
time rather than trusting stale indices.

Idempotency contract: a second run over the same sections is a no-op — a
cue with the planned name within ``tolerance_beats`` of the target is
skipped; a differently-named cue sitting within ``RENAME_EPSILON_BEATS`` of
the target is renamed in place (adding there would DELETE it).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

log = logging.getLogger(__name__)

RENAME_EPSILON_BEATS = 0.25
"""A foreign cue this close to a target is renamed in place — toggling at
its position would delete it, and two locators a hair apart help nobody."""

MATCH_EPSILON_BEATS = 0.01
"""Time equality tolerance when diffing cue lists to find a fresh cue."""

TEMPO_TOLERANCE_BPM = 1.0


@dataclass
class LocatorReport:
    added: list[dict[str, Any]] = field(default_factory=list)
    renamed: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    deleted: list[dict[str, Any]] = field(default_factory=list)
    failed: list[dict[str, Any]] = field(default_factory=list)
    tempo_check: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "added": self.added,
            "renamed": self.renamed,
            "skipped": self.skipped,
            "deleted": self.deleted,
            "failed": self.failed,
            "tempo_check": self.tempo_check,
            "warnings": self.warnings,
        }


async def _list_cues(osc_client: Any) -> list[dict[str, Any]]:
    """Cue points as ``[{index, name, time_beats}]`` in AbletonOSC list order."""
    args = await osc_client.request("/live/song/get/cue_points")
    out: list[dict[str, Any]] = []
    for i in range(0, len(args) - 1, 2):
        out.append(
            {"index": i // 2, "name": str(args[i]), "time_beats": float(args[i + 1])}
        )
    return out


async def _toggle_at(osc_client: Any, beats: float) -> None:
    osc_client.send("/live/song/set/current_song_time", float(beats))
    osc_client.send("/live/song/cue_point/add_or_delete")


def _find_at(
    cues: Sequence[dict[str, Any]], beats: float, epsilon: float
) -> dict[str, Any] | None:
    hits = [c for c in cues if abs(c["time_beats"] - beats) <= epsilon]
    return min(hits, key=lambda c: abs(c["time_beats"] - beats)) if hits else None


def check_tempo(
    live_tempo: float, bpm_estimate: float | None, tolerance: float = TEMPO_TOLERANCE_BPM
) -> dict[str, Any]:
    """Compare Live's tempo with the detected BPM (halved/doubled too)."""
    if bpm_estimate is None:
        return {"ok": True, "live_tempo": live_tempo, "bpm_estimate": None,
                "note": "no BPM estimate; check skipped"}
    candidates = {
        "1x": bpm_estimate,
        "0.5x": bpm_estimate / 2.0,
        "2x": bpm_estimate * 2.0,
    }
    best = min(candidates.items(), key=lambda kv: abs(kv[1] - live_tempo))
    ok = best[0] == "1x" and abs(bpm_estimate - live_tempo) <= tolerance
    result: dict[str, Any] = {
        "ok": ok,
        "live_tempo": live_tempo,
        "bpm_estimate": bpm_estimate,
    }
    if not ok:
        if best[0] != "1x" and abs(best[1] - live_tempo) <= tolerance:
            result["note"] = (
                f"Live tempo {live_tempo:.1f} looks like {best[0]} the detected "
                f"{bpm_estimate:.1f} BPM — beat_this may have found the "
                f"half/double-time pulse; set Live to {bpm_estimate:.1f} or pass force=True"
            )
        else:
            result["note"] = (
                f"Live tempo {live_tempo:.1f} != detected {bpm_estimate:.1f} BPM; "
                f"set the Live tempo to match (locator beat positions assume it) "
                f"or pass force=True"
            )
    return result


async def write_section_locators(
    sections: Sequence[dict[str, Any]],
    *,
    bpm_estimate: float | None = None,
    arrangement_start_beats: float = 0.0,
    tolerance_beats: float = 0.5,
    clear_existing: bool = False,
    force: bool = False,
    osc_client: Any = None,
) -> LocatorReport:
    """Write one named locator per section, idempotently.

    Args:
        sections: dicts with ``display_name`` + ``start_beats`` (the
            ``sections`` array of a sections.json).
        bpm_estimate: detected BPM for the tempo guard.
        arrangement_start_beats: where beat 0 of the WAV sits in Live's
            arrangement (default 1.1.1).
        tolerance_beats: same-name cues within this of the target are
            skipped (idempotency).
        clear_existing: first delete existing cues whose name matches a
            planned name but sit outside tolerance (stale positions).
        force: write even when the tempo guard fails.
        osc_client: explicit client (tests); default from get_client().
    """
    if osc_client is None:
        from ..osc_client import get_client

        osc_client = await get_client()

    report = LocatorReport()

    planned: list[tuple[str, float]] = []
    for s in sections:
        name = str(s.get("display_name") or s.get("label") or "").strip()
        beats = s.get("start_beats")
        if not name or beats is None:
            report.warnings.append(f"section without display_name/start_beats skipped: {s!r}")
            continue
        planned.append((name, arrangement_start_beats + float(beats)))

    # Tempo guard.
    live_tempo = float((await osc_client.request("/live/song/get/tempo"))[0])
    report.tempo_check = check_tempo(live_tempo, bpm_estimate)
    if not report.tempo_check["ok"] and not force:
        report.warnings.append("tempo check failed — nothing written (pass force=True to override)")
        return report

    # Locator toggling is playhead-based: stop the transport first.
    playing = bool((await osc_client.request("/live/song/get/is_playing"))[0])
    if playing:
        osc_client.send("/live/song/stop_playing")

    cues = await _list_cues(osc_client)

    if clear_existing:
        planned_names = {name.lower() for name, _ in planned}
        for cue in list(cues):
            if cue["name"].lower() in planned_names:
                target = next(b for n, b in planned if n.lower() == cue["name"].lower())
                if abs(cue["time_beats"] - target) > tolerance_beats:
                    await _toggle_at(osc_client, cue["time_beats"])
                    cues = await _list_cues(osc_client)
                    if _find_at(cues, cue["time_beats"], MATCH_EPSILON_BEATS) is None:
                        report.deleted.append(cue)
                    else:
                        report.failed.append({**cue, "error": "delete toggle did not remove cue"})

    for name, beats in planned:
        same_name = [
            c for c in cues
            if c["name"].strip().lower() == name.lower()
            and abs(c["time_beats"] - beats) <= tolerance_beats
        ]
        if same_name:
            report.skipped.append({"name": name, "time_beats": beats, "existing": same_name[0]})
            continue

        nearby = _find_at(cues, beats, RENAME_EPSILON_BEATS)
        if nearby is not None:
            # Adding here would DELETE the existing cue — rename it instead.
            fresh = await _list_cues(osc_client)
            target = _find_at(fresh, nearby["time_beats"], MATCH_EPSILON_BEATS)
            if target is None:
                report.failed.append({"name": name, "time_beats": beats,
                                      "error": "nearby cue vanished before rename"})
                continue
            osc_client.send("/live/song/cue_point/set/name", int(target["index"]), name)
            report.renamed.append({"name": name, "time_beats": target["time_beats"],
                                   "previous_name": target["name"]})
            cues = await _list_cues(osc_client)
            continue

        before_times = [c["time_beats"] for c in cues]
        await _toggle_at(osc_client, beats)
        cues = await _list_cues(osc_client)
        new = [
            c for c in cues
            if abs(c["time_beats"] - beats) <= tolerance_beats
            and not any(abs(c["time_beats"] - t) <= MATCH_EPSILON_BEATS for t in before_times)
        ]
        if not new:
            report.failed.append({"name": name, "time_beats": beats,
                                  "error": "toggle did not create a cue at the target"})
            continue
        cue = min(new, key=lambda c: abs(c["time_beats"] - beats))
        osc_client.send("/live/song/cue_point/set/name", int(cue["index"]), name)
        report.added.append({"name": name, "time_beats": cue["time_beats"]})
        cues = await _list_cues(osc_client)

    return report
