"""Build a canonical VoiceForge reference library without duplicating WAV data."""
from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path, help="Existing folder with ref_candidates.json and refs/")
    parser.add_argument("output", type=Path, help="New canonical library folder")
    args = parser.parse_args()

    source = args.source.resolve()
    output = args.output.resolve()
    manifest = source / "ref_candidates.json"
    refs_source = source / "refs"
    if not manifest.is_file() or not refs_source.is_dir():
        raise FileNotFoundError("Source must contain ref_candidates.json and refs/")
    if output.exists() and any(output.iterdir()):
        raise ValueError(f"Output must be new or empty: {output}")

    data = json.loads(manifest.read_text(encoding="utf-8-sig"))
    missing = []
    clip_count = 0
    for vt, clips in data.items():
        for clip in clips:
            clip_count += 1
            voice = clip.get("source_voice_type", vt)
            path = refs_source / voice / clip["file"]
            if not path.is_file() or not str(clip.get("text", "")).strip():
                missing.append(str(path))
    if missing:
        raise ValueError("Invalid references:\n" + "\n".join(missing[:20]))

    output.mkdir(parents=True, exist_ok=True)
    shutil.copy2(manifest, output / "ref_candidates.json")
    linked = 0
    copied = 0
    total_bytes = 0
    for source_file in refs_source.rglob("*"):
        if not source_file.is_file():
            continue
        target = output / "refs" / source_file.relative_to(refs_source)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(source_file, target)
            linked += 1
        except OSError:
            shutil.copy2(source_file, target)
            copied += 1
        total_bytes += source_file.stat().st_size

    report = {
        "voice_types": len(data),
        "reference_entries": clip_count,
        "physical_audio_files": linked + copied,
        "hardlinked_files": linked,
        "copied_files": copied,
        "logical_audio_bytes": total_bytes,
        "source_manifest": str(manifest),
    }
    (output / "library-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
