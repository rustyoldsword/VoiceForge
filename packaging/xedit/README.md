# xEdit export for VoiceForge

Script: **FishAI - Export VoiceForge CSV**. First public version, so far checked on only a few plugins. If something
comes out wrong, please report it with the last lines of xEdit's Messages tab.

## Install

Copy **both** PAS files together into your xEdit `Edit Scripts` folder (the one next to the xEdit EXE you actually
start, for example from Mod Organizer 2). `FishAIExportCore.pas` is a library used by the script; you never run it on
its own.

## Run

1. Start xEdit (for Skyrim SE: SSEEdit.exe / SSEEdit64.exe), load the plugin and its masters. Plugins that hold the
   NPCs, aliases and scenes it uses should be loaded too. Keep Reference Info building on and wait for loading to
   finish.
2. Select the **whole plugin** on the left, right-click → **Apply Script** → *FishAI - Export VoiceForge CSV* → OK.
3. Pick an output folder. The script creates a new `VoiceForge-YYYYMMDD-HHMMSS` folder inside it and never
   overwrites an earlier export.
4. Read `EXPORT-REPORT.txt` and `review.csv` first. Then drop that `VoiceForge-...` folder onto the VoiceForge window
   or onto `VoiceForge.exe` (VoiceForge 0.4.0 or newer): the project is created next to it.

Selecting dialogue topics or single INFO records limits the export to them. Selecting one quest does not select its
INFO records; for a first export select the whole plugin.

## Output

- `lines.csv` — INFO, the real response number (TRDT), spoken text, `needs_voice`, the original subtitle.
- `paths.csv` — Data-relative `Sound/Voice/Plugin/VoiceType/file.fuz`, the line key and voice type, plus how the voice
  was found.
- `review.csv` — unresolved voices, aliases and parents, text to check by hand, shared-voice candidates, overrides
  whose audio ownership is unclear.
- `EXPORT-REPORT.txt` — counts and limits.

CSV files are UTF-8; quotes, commas and line breaks are escaped. File names use the local FormID (ESL included) and the
same Quest/Topic shortening as xEdit's own `Export dialogues.pas`. Topics without an EditorID get
`<quest>__<FormID>_<n>.fuz`, as in the game. Shared Info is followed through DNAM.

## How voices are found, and where to check

Explicit speakers, positive GetIsID / GetIsVoiceType, part of the race / class / faction / sex filters, INFO and quest
dialogue conditions with AND/OR groups, static quest aliases and scene actors are used; form lists and the Use Traits
template flag are followed. Voice types come **only from the selected plugin and its masters**, so the result does not
depend on which other mods are loaded (another mod's follower voice never ends up in it). Child voice types are
included under the same conditions as the others; VoiceForge can skip them when it creates the project.

This is a projection of conditions onto voice types, not a game simulator: an intersection of two voice sets can be
wider than the NPCs that really meet both conditions. Shared lines without a clear speaker use voice types that allow
default dialogue and are marked in `review.csv` — check coverage before you voice them in bulk. A scene line whose
actor the game picks at runtime (an alias filled by Papyrus or by conditions) gets the same default-dialogue voice
types, narrowed by the alias conditions, and is marked the same way. Voices reachable only through LinkTo chains are
not reconstructed. A scene line whose topic no scene speaks is skipped and reported. For an INFO overridden by another plugin the audio path is not guessed: the line stays in `lines.csv`, the
reason goes to `review.csv`. **Lines in `lines.csv` without a row in `paths.csv` create no job.**

Empty text and pure punctuation get `needs_voice=no`. In `voice_text` (what Fish speaks) stage directions `[..]`,
`<..>` and `*..*` are removed; `subtitle_text` stays as in the game. Such lines, and lines with parentheses, are
listed in `review.csv`. A written path does not prove that a sound file exists or that the voice set is complete.

The exporter only reads: it changes no plugin, makes no network calls, needs no API key and does not extract audio
from BSA archives. Choose the voice library (`ref_candidates.json` + `refs`) or a Fish `reference_id` in VoiceForge.

## If an error appears

Keep the last lines of the Messages tab with the line number / error. Never save plugin changes for the sake of the
CSV — the exporter makes none. After a partial export keep that folder for diagnosis and export again into a new one.
Do not rename the CSV headers.

xEdit scripting documentation: https://tes5edit.github.io/docs/13-Scripting-Functions.html
