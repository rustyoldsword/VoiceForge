# VoiceForge 0.4.3

A Windows desktop studio that voices Skyrim dialogue with Fish Audio: it takes your mod's lines, has Fish speak them in
the voice you choose, and builds game-ready FUZ files (LIP lip-sync + XWM audio) at the exact paths the game reads.

Made by Dwemer Mods — https://dwemermods.com

## What it does

- **Turns an xEdit export into a project in one step**: drop the export folder onto the window (or onto
  `VoiceForge.exe`). Voices that only ended up in the export through broad conditions are skipped, and creatures that
  really speak get a voice you pick from your library. The included xEdit script (`xEdit Scripts\`) writes that
  folder straight from a loaded plugin. A `lines.csv` + `paths.csv` pair or a `jobs.json` manifest can still be
  imported by hand.
- **Comes with a voice library**: `voices\` holds 440 reference clips with exact transcripts for 126 vanilla voice
  types of Skyrim, Dawnguard and Dragonborn (all the common voices and many unique characters), so vanilla voices are
  cloned out of the box. Your own
  library (`ref_candidates.json` + `refs\`) or a Fish library voice (`reference_id`) can be used instead.
- **Builds FUZ** with your own LipGenerator and xWMAEncode: every LipGenerator worker runs its own private copy, a short
  lead silence keeps the lips in time, a lip that starts late is rebuilt with another pause, and clips too short for
  LipGenerator are padded.
- **Keeps a persistent queue**: pause, stop, resume after a crash, play any WAV, regenerate selected lines (the old
  files are backed up first), verify every FUZ, export the result as a mod `.zip`.
- **Watches resources**: CPU target with feedback control, optional temperature limit, RAM and free disk.
- **Interface** in English and Russian.

Identical text for the same voice type is generated once and written to every path that uses it.

## Requirements

- Windows 10/11, 64-bit. No Python install is needed for the release build.
- A Fish Audio API key (`FISH_API_KEY` as an environment variable, or a local file you select in Settings). The key
  is never shown and never copied into the project. The model is fixed to `s2.1-pro-free`; availability and pricing
  are decided by Fish Audio. HTTP 401/402 stops the queue — there is no automatic switch to a paid model.
- A tools folder with **LipGenerator.exe** (and its FonixData.cdf), **xWMAEncode.exe** and **ffmpeg.exe**. LipGenerator
  ships with the Creation Kit (`Tools\LipGen\LipGenerator`). None of these tools are included.
- Nothing else for vanilla voices: their reference clips ship in `voices\`. For custom voices (a follower with its
  own voice type) bring your own clips.

## Quick start

1. Unpack the archive anywhere and run `VoiceForge.exe`. Keep the `_internal` folder next to it.
2. **Settings**: choose the tools folder and your API key file, then **Save settings**. This works before any
   project exists, and every project you create or open later gets them too.
3. **Drop the xEdit export folder** (`VoiceForge-YYYYMMDD-HHMMSS`, see below) onto the VoiceForge window or onto
   `VoiceForge.exe`, or press **From an xEdit export…**. The bundled vanilla library (`voices\`) is used
   automatically. Check the project dialog (name, child voices, voices for speaking creatures) and press
   **Create project**: the project is created next to the export folder and opens. Manual import is still there: **+ New project** with `lines.csv` + `paths.csv` or `jobs.json`.
4. **Voice references** shows every voice type of the project and its clips. To use your own library instead, put
   its `ref_candidates.json` (with `refs\` next to it) into the export folder, or choose it on that page once:
   VoiceForge remembers it for the next projects.
5. Press **5 sample WAVs** and listen in **Line queue** before the full run.
6. **Run pipeline**. Results go to `output\` inside the project; **Verify all FUZ** checks every file, and
   **Export mod .zip** packs them as an installable mod.

Input formats, column by column: `templates\README-RU.md` (Russian) and the example files next to it. Every plugin
name, FormID and file name in the examples is fictional.

## Project from an xEdit export

Drop the folder the xEdit script wrote (it holds `lines.csv` and `paths.csv`) onto the window, onto `VoiceForge.exe`,
or choose it with **From an xEdit export…**. VoiceForge checks every line whose voice type has no reference in your
library:

- the line also has a voice type **with** references → the reference-less one only came from a broad condition (for
  example "any male" also matches creature voices) and is skipped;
- the line has no speech once stage directions are removed (`*bark*`, `(growl)`, `[roars]`) → skipped;
- otherwise that voice really speaks, for example a talking dog or a spriggan. The project dialog lists these voices
  with their lines; pick a library voice to clone each one from, or leave it on "do not voice". The FUZ files still go
  to the creature's own voice folder, where the game looks for them; only the reference clips are borrowed.

The dialog also has **Do not voice child voices (FemaleChild, MaleChild)**. The full breakdown is written to
`voice-plan.txt` next to the export and your choices to `voice-substitutions.json` (offered again next time). The
project itself only records which library voice each creature borrows and takes the clips from the library (the
bundled `voices\` folder), so the export folder can be deleted once the project exists.

## xEdit exporter (experimental)

`xEdit Scripts\FishAI - Export VoiceForge CSV.pas` + `FishAIExportCore.pas` — copy **both** into xEdit's
`Edit Scripts` folder. In xEdit select the whole plugin, right-click → **Apply Script** → *FishAI - Export VoiceForge
CSV*, and pick an output folder. It writes `lines.csv`, `paths.csv`, `review.csv` and `EXPORT-REPORT.txt` into a new
time-stamped folder. Read `EXPORT-REPORT.txt` and `review.csv` first.

The script only reads records; it never changes a plugin. Voice types are resolved from speakers, GetIsID /
GetIsVoiceType conditions, quest aliases and scene actors; shared lines without a clear speaker are listed for review
instead of being guessed. Voice types come **only from the selected plugin and its masters**, so the export does not
depend on how many other mods are loaded in xEdit (another mod's follower voice can never end up in it). Child voice
types are included; skip them in the project dialog if you need to. Stage directions (`[..]`, `<..>`, `*..*`) are
removed from the spoken text; the subtitle stays as in the game. A large dialogue mod (4,300 INFO, 220,000 FUZ paths)
exports in about three minutes.

This is the first public version of the exporter. It has not yet been run on many plugins — please report
anything it gets wrong, with the last lines of xEdit's Messages tab. Details: `xEdit Scripts\README.md`.

## Build from source

Python 3.14 on Windows:

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller
.venv\Scripts\python.exe main.py                  # run
.venv\Scripts\python.exe -m unittest discover tests  # tests
.venv\Scripts\python.exe tools\build_release.py    # release folder + zip in dist\
```

`tools/build_release.py` scans the release for private strings listed one per line in `tools/private_strings.txt`
(a local file you create; it is not part of the repository). `tools/screenshots.py` renders every page on a
fictional demo project.

## Your data

Projects store paths, dialogue text, job states and errors in the folder you create. Recent project and monitor
paths are remembered in Qt settings. Do not publish your projects, key files, logs, queue databases or screenshots
that show private paths.

## Limits

- LipGenerator works in US English; other languages get audio, but lip-sync quality is not checked.
- The format checks do not replace listening to the result and testing in the game.
- VoiceForge does not read ESP/ESM/ESL or BSA files itself (use the xEdit exporter) and does not extract reference
  audio from BSA archives.

## Credits and licenses

See `licenses\`. Qt for Python (PySide6) is LGPLv3. The Atelier UI font is Inter 4.1 renamed, SIL Open Font
License 1.1. Fish Audio is a third-party service with its own terms.

## Changes

**0.4.3**
- Restarting a long run starts at once. 0.4.2 reopened every finished WAV and FUZ before the first new request,
  so after 63,000 finished lines **Run pipeline** looked dead for about ten minutes (JOBS / MIN stayed at 0).
  Finished lines whose FUZ files are still there are now skipped in seconds; the event log says how many. A line
  whose file was deleted is done again. File contents are still checked at the end of a full run and by
  **Verify all FUZ**.
- Fixed: a run could stop at once with "FISH_API_KEY is missing" although the key file was fine. Requests that
  started at the same moment saw an empty key while the first one was still reading the file; the key is now read
  once and handed to every request.
- xEdit exporter: scene lines are voiced. The script read the topic from the wrong field of a scene action, so
  every line spoken in a scene went to `review.csv` with no voice and created no job. Scene actors are now found by
  topic. When the game picks the scene actor at runtime (an alias filled by Papyrus or by conditions), the line gets
  the voice types that allow default dialogue, narrowed by the alias conditions, and is listed in `review.csv` for a
  coverage check. Copy both PAS files again.

**0.4.2**
- Up to 50 Fish requests at once (Settings → Fish requests; 12 for new projects). 0.4.1 allowed only 3, and since
  every request mostly waits for the network the run stayed near 35 lines per minute with the CPU idle; on the free
  model 12 requests gave about 200 lines per minute without a single HTTP 429. Fish's documented account limits
  (5 under $100 prepaid, 15 from $100, 50 from $1,000) may apply to paid models: if the event log keeps showing
  HTTP 429, lower the number.

**0.4.1**
- The tools folder and the API key file are remembered: set them once in Settings (with or without a project open)
  and every new project gets them. 0.4.0 created each project from an xEdit export with an empty tools folder, so the
  run stopped with "Missing tools". Opening such a project now fills them in from your settings.
- The tools error says which folder was checked and points to Settings.
- A project with talking creatures no longer depends on the xEdit export folder: it records which library voice each
  creature borrows and takes the clips from the bundled `voices\` folder. In 0.4.0 the borrowed clips were copied into
  the export folder, and deleting it stopped the run with "No such file ... ref_candidates.json". Projects made with
  0.4.0 are converted when opened, as long as their export folder is still there.
- A project made with the bundled library finds it next to VoiceForge.exe even after the program folder is moved.

**0.4.0**
- Vanilla voice library included: 440 reference clips with exact transcripts for the 126 vanilla voice types
  (`voices\`). No library to build before the first project.
- Project from an xEdit export in one step: drop the export folder onto the window or onto VoiceForge.exe, or use
  **From an xEdit export…**. The reference library is remembered.
- Voices without references are planned per line: candidates from broad conditions and barks/growls are skipped;
  creatures that really speak get a library voice you pick in the project dialog (their FUZ stays in their own
  folder). Optional: skip child voices. Breakdown in `voice-plan.txt`.
- Opening a large project is much faster (a 190,000-file project: 2 minutes before, 4 seconds now).
- xEdit exporter: voice types come only from the selected plugin and its masters; no more voices from other loaded
  mods.
- xEdit exporter: large plugins export in minutes instead of an hour (path lookups were linear).

**0.3.1**
- xEdit exporter: fixed "Undeclared Identifier 'IncludeTrailingPathDelimiter'" on start; it now runs in xEdit and writes the CSV files.

**0.3.0** — first public release
- Dwemer Atelier look: graphite surfaces, brass accent, Atelier UI font, Dwemer Mods logo and window icon.
- Every LipGenerator worker runs a private copy, so parallel runs never read each other's audio.
- Lead silence for every clip and automatic rebuild of a late lip; clips under 0.4 s are padded.
- VoiceType names in `ref_candidates.json` are matched without regard to case.
- English and Russian interface.
- xEdit exporter for `lines.csv` + `paths.csv`.
