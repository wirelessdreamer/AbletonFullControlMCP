"""CLI companion to the MCP tools: python -m ableton_mcp.project_import."""

from __future__ import annotations

import argparse
import asyncio
import json

from . import jobs
from .pipeline import prepare_project


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare portable Ableton projects from stems or a recording")
    sub = parser.add_subparsers(dest="operation", required=True)
    for operation in ("prepare", "inspect"):
        command = sub.add_parser(operation)
        command.add_argument("sources", nargs="+")
        command.add_argument("--destination", required=True)
        command.add_argument("--mode", default="auto", choices=["auto", "prepared_stems", "separate_recording_6stems"])
        command.add_argument("--bpm", type=float)
        command.add_argument("--meter", default=None, help="e.g. 4/4 or 6/8")
        command.add_argument("--downbeat", type=float, help="Source time in seconds of a known bar downbeat")
        command.add_argument("--reference")
        command.add_argument("--template")
        command.add_argument("--settings", help="JSON with offsets_seconds, midi_pairings, audio_roles and/or tempo_map")
        command.add_argument("--no-live", action="store_true", help="Build only; readiness remains needs_review")
    for operation in ("status", "cancel", "resume"):
        command = sub.add_parser(operation)
        command.add_argument("job_id")
        if operation == "resume":
            command.add_argument("--destination")
    verify = sub.add_parser("verify")
    verify.add_argument("project")
    args = parser.parse_args()
    try:
        if args.operation in {"prepare", "inspect"}:
            request = {"source_paths": args.sources, "destination": args.destination,
                       "source_mode": args.mode, "bpm": args.bpm,
                       "time_signature": [int(v) for v in args.meter.split("/")] if args.meter else None,
                       "downbeat_sec": args.downbeat, "reference_path": args.reference,
                       "template_als_path": args.template}
            if args.settings:
                from pathlib import Path
                settings = json.loads(Path(args.settings).read_text(encoding="utf-8"))
                allowed = {"offsets_seconds", "midi_pairings", "tempo_map", "audio_roles"}
                if not isinstance(settings, dict) or set(settings) - allowed:
                    raise ValueError("Settings support offsets_seconds, midi_pairings, audio_roles and tempo_map only")
                request.update(settings)
            if args.operation == "inspect":
                result = prepare_project(**request, inspect_only=True)
            else:
                result = jobs.start({**request, "verify_live": not args.no_live})
        elif args.operation == "verify":
            from .verify import verify_project
            result = asyncio.run(verify_project(args.project))
        elif args.operation == "resume":
            result = jobs.resume(args.job_id, args.destination)
        else:
            result = getattr(jobs, args.operation)(args.job_id)
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Error: {exc}\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
