unit FishAIExportCore;

// Read-only xEdit exporter. Original filename truncation follows xEdit's
// bundled Export dialogues.pas. No plugins are changed and no network is used.
var
  Lines, Paths, Issues, Audit, LineKeys, PathKeys, Seen, Voices, Defaults: TStringList;
  Recursion: TStringList;
  OutputDir: string;
  InfoCount, ResponseCount, TargetCount: integer;
  VoiceUncertain: boolean;

function NewSet: TStringList;
begin
  Result := TStringList.Create;
  Result.Sorted := True;
  Result.Duplicates := dupIgnore;
end;

function Csv(s: string): string;
begin
  Result := '"' + StringReplace(s, '"', '""', [rfReplaceAll]) + '"';
end;

function StableKey(e: IInterface): string;
var m: IInterface;
begin
  Result := '';
  if not Assigned(e) then Exit;
  m := MasterOrSelf(e);
  Result := IntToHex(FormID(m) and $FFFFFF, 6) + ':' + GetFileName(m);
end;

function Link(e: IInterface; p: string): IInterface;
begin
  Result := nil;
  if not Assigned(e) then Exit;
  Result := LinksTo(ElementByPath(e, p));
  if Assigned(Result) then Result := WinningOverride(Result);
end;

procedure Intersect(a, b: TStringList);
var i: integer;
begin
  for i := Pred(a.Count) downto 0 do
    if b.IndexOf(a[i]) < 0 then a.Delete(i);
end;

function SameSet(a, b: TStringList): boolean;
begin
  Result := a.Text = b.Text;
end;

procedure Issue(info: IInterface; n: integer; kind, detail: string);
begin
  Issues.Add(Csv(StableKey(info)) + ',' + IntToStr(n) + ',' + Csv(kind) + ',' + Csv(detail));
end;

procedure RecordVoices(e: IInterface; dest: TStringList; depth: integer);
var i, flags: integer; sig, key: string; child, entries: IInterface;
begin
  if not Assigned(e) then Exit;
  if depth > 32 then Exit;
  e := WinningOverride(e);
  key := StableKey(e);
  if Recursion.IndexOf(key) >= 0 then Exit;
  Recursion.Add(key);
  if GetIsDeleted(e) then Exit;
  sig := Signature(e);
  if (sig = 'ACHR') or (sig = 'REFR') then begin
    RecordVoices(BaseRecord(e), dest, depth + 1);
  end else if sig = 'VTYP' then begin
    key := LowerCase(EditorID(e));
    if (key <> '') and (Voices.IndexOf(key) >= 0) then dest.Add(key);
  end else if sig = 'NPC_' then begin
    flags := GetElementNativeValues(e, 'ACBS\Template Flags');
    child := Link(e, 'TPLT');
    if Assigned(child) and ((flags and 1) <> 0) then
      RecordVoices(child, dest, depth + 1)
    else begin
      child := Link(e, 'VTCK');
      if Assigned(child) then RecordVoices(child, dest, depth + 1);
    end;
  end else if sig = 'TACT' then
    RecordVoices(Link(e, 'VNAM'), dest, depth + 1)
  else if sig = 'FLST' then begin
    entries := ElementByPath(e, 'FormIDs');
    for i := 0 to Pred(ElementCount(entries)) do
      RecordVoices(LinksTo(ElementByIndex(entries, i)), dest, depth + 1);
  end else if sig = 'LVLN' then begin
    entries := ElementByPath(e, 'Leveled List Entries');
    for i := 0 to Pred(ElementCount(entries)) do
      RecordVoices(Link(ElementByIndex(entries, i), 'LVLO\Reference'), dest, depth + 1);
  end else if (sig = 'FACT') or (sig = 'CLAS') or (sig = 'RACE') then begin
    for i := 0 to Pred(ReferencedByCount(e)) do begin
      child := WinningOverride(ReferencedByIndex(e, i));
      if Signature(child) = 'NPC_' then RecordVoices(child, dest, depth + 1);
    end;
  end;
end;

procedure GetVoices(e: IInterface; dest: TStringList);
begin
  Recursion.Clear;
  RecordVoices(e, dest, 0);
end;

// xEdit resolves functions declared later in the unit (as in its bundled exporter).

procedure AliasVoices(quest: IInterface; id: integer; info: IInterface; dest: TStringList; depth: integer);
var aliases, a, e: IInterface; i: integer; limit: TStringList;
begin
  if not Assigned(quest) then Exit;
  if depth > 16 then begin Issue(info, 0, 'unresolved_alias', 'Alias recursion limit'); Exit; end;
  aliases := ElementByPath(quest, 'Aliases');
  for i := 0 to Pred(ElementCount(aliases)) do begin
    a := ElementByIndex(aliases, i);
    if GetNativeValue(ElementByIndex(a, 0)) <> id then Continue;
    if ElementExists(a, 'ALFR') then GetVoices(Link(a, 'ALFR'), dest)
    else if ElementExists(a, 'ALUA') then GetVoices(Link(a, 'ALUA'), dest)
    else if ElementExists(a, 'External Alias Reference') then
      AliasVoices(Link(a, 'External Alias Reference\ALEQ'),
        GetElementNativeValues(a, 'External Alias Reference\ALEA'), info, dest, depth + 1)
    else if ElementExists(a, 'Conditions') then
      ConditionsVoices(ElementByPath(a, 'Conditions'), quest, info, dest, depth + 1);
    e := Link(a, 'VTCK');
    if Assigned(e) then begin
      limit := NewSet;
      GetVoices(e, limit);
      if dest.Count = 0 then dest.AddStrings(limit) else Intersect(dest, limit);
      limit.Free;
    end;
    Exit;
  end;
end;

// Voice-set projection of CTDA. Unknown predicates map to the entire universe,
// preserving OR semantics. Negative actor/faction predicates cannot be inverted
// at voice granularity (many actors share a voice), so remain unbounded.
procedure ConditionVoices(c, quest, info: IInterface; dest: TStringList; depth: integer);
var fn, p: string; typ, op, i, sex: integer; cmp: double;
    e: IInterface; known, want, voiceOnly: boolean; matches: TStringList;
begin
  dest.AddStrings(Voices);
  typ := GetElementNativeValues(c, 'CTDA\Type');
  op := typ and $E0;
  if (typ and $16) <> 0 then Exit; // comparison uses a runtime global
  if GetElementNativeValues(c, 'CTDA\Run On') <> 0 then Exit;
  cmp := GetElementNativeValues(c, 'CTDA\Comparison Value');
  // Only boolean == 1/0 or != 1/0 can be safely projected here.
  if (op <> 0) and (op <> $20) then Exit;
  if (cmp <> 0) and (cmp <> 1) then Exit;
  want := cmp = 1;
  if op = $20 then want := not want;
  fn := GetElementEditValues(c, 'CTDA\Function');
  p := ''; known := True; voiceOnly := False;
  if fn = 'GetIsID' then p := 'CTDA\Referenceable Object'
  else if fn = 'GetIsVoiceType' then begin p := 'CTDA\Voice Type'; voiceOnly := True; end
  else if fn = 'GetInFaction' then p := 'CTDA\Faction'
  else if fn = 'GetIsClass' then p := 'CTDA\Class'
  else if fn = 'GetIsRace' then p := 'CTDA\Race'
  else if (fn <> 'GetIsAliasRef') and (fn <> 'GetIsSex') then known := False;
  if not known then Exit;
  if not want and not voiceOnly and (fn <> 'GetIsSex') then Exit;
  matches := NewSet;
  try
    if p <> '' then begin
      e := LinksTo(ElementByIndex(ElementByPath(c, 'CTDA'), 5));
      if Assigned(e) then e := WinningOverride(e);
      if not Assigned(e) then begin VoiceUncertain := True; Issue(info, 0, 'unresolved_condition', fn); Exit; end;
      GetVoices(e, matches);
      // An empty set from a known VTYP (e.g. a deleted voice type) is a real empty set.
      if (matches.Count = 0) and (Signature(e) <> 'VTYP') then begin VoiceUncertain := True; Issue(info, 0, 'unresolved_condition', fn); Exit; end;
    end else if fn = 'GetIsAliasRef' then begin
      AliasVoices(quest, GetElementNativeValues(c, 'CTDA\Alias'), info, matches, depth + 1);
      if matches.Count = 0 then begin
        VoiceUncertain := True;
        Issue(info, 0, 'unresolved_alias', GetElementEditValues(c, 'CTDA'));
        Exit;
      end;
    end else if fn = 'GetIsSex' then begin
      sex := GetElementNativeValues(c, 'CTDA\Sex');
      for i := 0 to Pred(Voices.Count) do begin
        e := ObjectToElement(Voices.Objects[i]);
        if ((GetElementNativeValues(e, 'DNAM') shr 1) and 1) = sex then matches.Add(Voices[i]);
      end;
    end;
    if want then begin dest.Clear; dest.AddStrings(matches); end
    else for i := Pred(dest.Count) downto 0 do
      if matches.IndexOf(dest[i]) >= 0 then dest.Delete(i);
  finally matches.Free; end;
end;

procedure ConditionsVoices(conditions, quest, info: IInterface; dest: TStringList; depth: integer);
var groupSet, one: TStringList; c: IInterface; i, typ: integer;
begin
  if depth > 16 then Exit;
  dest.AddStrings(Voices);
  if not Assigned(conditions) then Exit;
  groupSet := NewSet; one := NewSet;
  try
    for i := 0 to Pred(ElementCount(conditions)) do begin
      c := ElementByIndex(conditions, i);
      one.Clear;
      ConditionVoices(c, quest, info, one, depth);
      groupSet.AddStrings(one);
      typ := GetElementNativeValues(c, 'CTDA\Type');
      if ((typ and 1) = 0) or (i = Pred(ElementCount(conditions))) then begin
        Intersect(dest, groupSet);
        groupSet.Clear;
      end;
    end;
  finally groupSet.Free; one.Free; end;
end;

procedure InfoVoices(info: IInterface; dest: TStringList; var method: string);
var topic, quest, e, scene, actions, action: IInterface;
    qset, tmp, actors: TStringList; i, j: integer; isScene: boolean;
begin
  topic := Link(info, 'Topic');
  quest := Link(topic, 'QNAM');
  qset := NewSet; tmp := NewSet; actors := NewSet;
  try
    ConditionsVoices(ElementByPath(info, 'Conditions'), quest, info, dest, 0);
    ConditionsVoices(ElementByPath(quest, 'Quest Dialogue Conditions\Conditions'), quest, info, qset, 0);
    Intersect(dest, qset);
    e := Link(info, 'ANAM');
    if Assigned(e) then begin
      GetVoices(e, tmp); Intersect(dest, tmp);
      method := 'speaker_and_conditions';
      Exit;
    end;
    isScene := GetElementEditValues(topic, 'DATA\Category') = 'Scene';
    if isScene then begin
      e := MasterOrSelf(topic);
      for i := 0 to Pred(ReferencedByCount(e)) do begin
        scene := WinningOverride(ReferencedByIndex(e, i));
        if Signature(scene) <> 'SCEN' then Continue;
        if GetIsDeleted(scene) then Continue;
        actions := ElementByPath(scene, 'Actions');
        for j := 0 to Pred(ElementCount(actions)) do begin
          action := ElementByIndex(actions, j);
          if (GetElementEditValues(action, 'ANAM') = 'Dialogue') and
             (StableKey(Link(action, 'DATA')) = StableKey(topic)) then begin
            tmp.Clear;
            AliasVoices(Link(scene, 'PNAM'), GetElementNativeValues(action, 'ALID'), info, tmp, 0);
            if tmp.Count = 0 then begin
              Issue(info, 0, 'unresolved_scene_actor', Name(scene));
              dest.Clear; method := 'unresolved_scene'; Exit;
            end;
            actors.AddStrings(tmp);
          end;
        end;
      end;
      if actors.Count = 0 then begin
        Issue(info, 0, 'unresolved_scene_actor', 'No static scene actor for topic');
        dest.Clear; method := 'unresolved_scene'; Exit;
      end;
      Intersect(dest, actors); method := 'scene_actor_and_conditions';
    end else if SameSet(dest, Voices) then begin
      Intersect(dest, Defaults); method := 'default_dialogue_candidates';
      if VoiceUncertain then begin
        dest.Clear; method := 'unresolved_voice_constraint';
      end else
        Issue(info, 0, 'candidate_coverage', 'No finite speaker constraint: Allow Default Dialog voice types; review coverage.');
    end else method := 'condition_voice_candidates';
  finally qset.Free; tmp.Free; actors.Free; end;
end;

function VoiceFileName(questID, topicID: string; source: IInterface; n: integer): string;
var qlen, dlen: integer;
begin
  qlen := Length(questID); dlen := Length(topicID);
  if qlen + dlen > 25 then begin
    if qlen > 10 then begin qlen := 10; dlen := 15; end
    else dlen := 25 - qlen;
  end;
  Result := LowerCase(Copy(questID, 1, qlen) + '_' + Copy(topicID, 1, dlen) + '_' +
    IntToHex(FormID(MasterOrSelf(source)) and $FFFFFF, 8) + '_' + IntToStr(n) + '.fuz');
end;

function HasSpeech(s: string): boolean;
var i: integer;
begin
  Result := False;
  for i := 1 to Length(s) do
    if Pos(Copy(s, i, 1), ' .,!?:;-_'+#9+#10+#13) = 0 then begin Result := True; Exit; end;
end;

// Spoken text for TTS: removes [stage directions], <directions> and *actions*, and collapses
// whitespace. An unclosed direction keeps its words and loses only the marker characters, so a
// line that is entirely '[Whispering something' is not silently emptied.
function SpokenText(s: string): string;
var i: integer; c, closer: string;
begin
  Result := ''; closer := '';
  for i := 1 to Length(s) do begin
    c := Copy(s, i, 1);
    if closer <> '' then begin
      if c = closer then closer := '';
      Continue;
    end;
    if c = '[' then closer := ']'
    else if c = '<' then closer := '>'
    else if c = '*' then closer := '*'
    else Result := Result + c;
  end;
  if closer <> '' then begin
    Result := s;
    for i := 1 to 5 do
      Result := StringReplace(Result, Copy('[]<>*', i, 1), ' ', [rfReplaceAll]);
  end;
  Result := StringReplace(Result, #9, ' ', [rfReplaceAll]);
  Result := StringReplace(Result, #13, ' ', [rfReplaceAll]);
  Result := StringReplace(Result, #10, ' ', [rfReplaceAll]);
  while Pos('  ', Result) > 0 do
    Result := StringReplace(Result, '  ', ' ', [rfReplaceAll]);
  Result := Trim(Result);
end;

procedure ExportInfo(info: IInterface);
var source, topic, quest, responses, response, nextInfo, m: IInterface;
    setVoice, visited: TStringList;
    i, v, n, idx, countBefore: integer;
    key, rowkey, text, voiceText, needs, fileName, target, method, plugin, notes: string;
begin
  if Signature(info) <> 'INFO' then Exit;
  key := StableKey(info);
  if Seen.IndexOf(key) >= 0 then Exit;
  Seen.Add(key);
  if Audit.IndexOf('Selected source: ' + GetFileName(info)) < 0 then
    Audit.Add('Selected source: ' + GetFileName(info));
  info := WinningOverride(info);
  if GetIsDeleted(info) then Exit;
  Inc(InfoCount);
  source := info;
  visited := NewSet;
  try
    while Assigned(Link(source, 'DNAM')) do begin
      if visited.IndexOf(StableKey(source)) >= 0 then begin
        Issue(info, 0, 'shared_cycle', 'Shared INFO cycle'); Exit;
      end;
      visited.Add(StableKey(source)); source := Link(source, 'DNAM');
    end;
  finally visited.Free; end;
  responses := ElementByPath(source, 'Responses');
  if not Assigned(responses) then begin Issue(info, 0, 'no_responses', 'No response text'); Exit; end;
  topic := Link(source, 'Topic'); quest := Link(topic, 'QNAM');
  if not Assigned(topic) or not Assigned(quest) then begin
    Issue(info, 0, 'unresolved_parent', 'Missing DIAL/QUST'); Exit;
  end;
  setVoice := NewSet;
  try
    method := ''; VoiceUncertain := False;
    InfoVoices(info, setVoice, method);
    // Shared response owners must be reached via their actual users, not treated
    // as generic dialogue on their own. Otherwise they acquire unrelated voices.
    if (GetElementEditValues(topic, 'DATA\Subtype') = 'SharedInfo') and
       (StableKey(source) = key) then begin
      Issue(info, 0, 'shared_owner', 'Shared text is exported through INFO users.'); Exit;
    end;
    for i := 0 to Pred(ElementCount(responses)) do begin
      response := ElementByIndex(responses, i);
      if not ElementExists(response, 'TRDT\Response number') then begin
        Issue(source, 0, 'missing_response_number', 'Cannot construct an audio target'); Continue;
      end;
      n := GetElementNativeValues(response, 'TRDT\Response number');
      text := GetElementEditValues(response, 'NAM1');
      voiceText := SpokenText(text); notes := '';
      if HasSpeech(voiceText) then needs := 'yes' else needs := 'no';
      if voiceText <> Trim(text) then begin
        notes := 'Stage directions removed from voice_text; subtitle unchanged.';
        Issue(source, n, 'text_review', notes);
      end else if Pos('(', text) > 0 then begin
        notes := 'Parentheses kept in voice_text; check whether they are spoken.';
        Issue(source, n, 'text_review', notes);
      end;
      rowkey := StableKey(source) + '#' + IntToStr(n);
      idx := LineKeys.IndexOf(rowkey);
      if idx < 0 then begin
        LineKeys.Add(rowkey); Inc(ResponseCount);
        Lines.Add(Csv('line_' + IntToStr(ResponseCount)) + ',' + Csv(StableKey(source)) + ',' +
          IntToStr(n) + ',' + needs + ',' + Csv(voiceText) + ',' + Csv(text) + ',' + Csv(notes));
      end;
      if setVoice.Count = 0 then begin Issue(source, n, 'unresolved_voice', method); Continue; end;
      // A topic without an EditorID is normal (many vanilla generic topics): the game then names
      // the file <quest>__<formid>_<n>.fuz. Only a missing quest EditorID is unresolved.
      if EditorID(quest) = '' then begin
        Issue(source, n, 'unresolved_filename', 'Missing quest EditorID'); Continue;
      end;
      // Overrides of another plugin's INFO may redirect audio ownership; do not
      // guess a replacement path. Existing exact asset mapping is required.
      m := MasterOrSelf(source);
      if GetFileName(m) <> GetFileName(source) then begin
        Issue(source, n, 'override_audio_owner', 'Overridden INFO: verify audio folder against existing assets before export.');
        Continue;
      end;
      plugin := GetFileName(m);
      fileName := VoiceFileName(EditorID(quest), EditorID(topic), source, n);
      for v := 0 to Pred(setVoice.Count) do begin
        target := 'Sound/Voice/' + plugin + '/' + setVoice[v] + '/' + fileName;
        idx := PathKeys.IndexOfName(LowerCase(target));
        if idx >= 0 then begin
          if PathKeys.ValueFromIndex[idx] <> rowkey then
            Issue(source, n, 'target_conflict', target);
          Continue;
        end;
        PathKeys.Add(LowerCase(target) + '=' + rowkey);
        Paths.Add(Csv(target) + ',' + Csv(StableKey(source)) + ',' + IntToStr(n) + ',' +
          Csv(setVoice[v]) + ',' + Csv(method) + ',' + Csv(StableKey(info)));
        Inc(TargetCount);
      end;
    end;
  finally setVoice.Free; end;
end;

procedure StartExport(folder: string);
var f, g, e: IInterface; i, j, k, flags: integer; s: string;
begin
  InfoCount := 0; ResponseCount := 0; TargetCount := 0;
  OutputDir := IncludeTrailingBackslash(folder);
  Lines := TStringList.Create; Paths := TStringList.Create; Issues := NewSet;
  Audit := TStringList.Create; LineKeys := NewSet; PathKeys := NewSet;
  Seen := NewSet; Voices := NewSet; Defaults := NewSet; Recursion := NewSet;
  Lines.Add('id,info_formid,response_number,needs_voice,voice_text (stage directions removed),subtitle_text (as in game unchanged),export_notes');
  Paths.Add('game_path (relative to Data),info_formid,response_number,voice_type,resolution,context_info');
  // All available VTYP are collected, including custom voices, not a fixed list.
  for i := 0 to Pred(FileCount) do begin
    f := FileByIndex(i); g := GroupBySignature(f, 'VTYP');
    for j := 0 to Pred(ElementCount(g)) do begin
      e := WinningOverride(ElementByIndex(g, j));
      s := LowerCase(EditorID(e));
      if GetIsDeleted(e) or (s = '') then Continue;
      Voices.AddObject(s, e);
      flags := GetElementNativeValues(e, 'DNAM');
      if (flags and 1) <> 0 then Defaults.Add(s);
    end;
  end;
  AddMessage('FishAI: voice types ' + IntToStr(Voices.Count) + ', default dialogue ' + IntToStr(Defaults.Count));
end;

// xEdit's JvInterpreter TStrings.SaveToFile adapter accepts only one
// argument and uses the system ANSI encoding. TBinaryWriter.Write(string)
// uses UTF-8, with a 7-bit byte-length prefix. Copy only the UTF-8 payload.
procedure SaveUtf8(list: TStringList; filename: string);
var stream: TMemoryStream; writer: TBinaryWriter; reader: TBinaryReader;
    output: TFileStream; b: integer;
begin
  stream := TMemoryStream.Create;
  try
    writer := TBinaryWriter.Create(stream);
    try writer.Write(list.Text); finally writer.Free; end;
    stream.Position := 0;
    reader := TBinaryReader.Create(stream);
    try
      repeat b := reader.ReadByte; until b < 128;
    finally reader.Free; end;
    output := TFileStream.Create(filename, fmCreate);
    try
      if stream.Size > stream.Position then
        output.CopyFrom(stream, stream.Size - stream.Position);
    finally output.Free; end;
  finally stream.Free; end;
end;

procedure FinishExport;
var report: TStringList;
begin
  ForceDirectories(OutputDir);
  SaveUtf8(Lines, OutputDir + 'lines.csv');
  SaveUtf8(Paths, OutputDir + 'paths.csv');
  Issues.Sorted := False;
  Issues.Insert(0, 'info_formid,response_number,reason,detail');
  SaveUtf8(Issues, OutputDir + 'review.csv');
  report := TStringList.Create;
  report.Add('FishAI / VoiceForge xEdit export');
  report.Add('INFO contexts: ' + IntToStr(InfoCount));
  report.Add('Unique responses: ' + IntToStr(ResponseCount));
  report.Add('Target paths: ' + IntToStr(TargetCount));
  report.Add('Review entries: ' + IntToStr(Issues.Count - 1));
  report.AddStrings(Audit);
  report.Add('Source: selected INFOs, effective winning overrides in this xEdit session.');
  report.Add('Read only. No plugins modified; no AI/network/Python required.');
  report.Add('Import lines.csv + paths.csv as Pair CSV in VoiceForge.');
  report.Add('Review review.csv first. Rows without paths do not create jobs.');
  report.Add('Default/condition voice sets are conservative candidates, not a full runtime CTDA evaluator.');
  report.Add('Dynamic aliases, LinkTo-only speakers and overridden audio ownership may need manual mapping.');
  report.Add('All voice types are included, children too. voice_text has [..], <..> and *..* removed; subtitle_text is unchanged.');
  SaveUtf8(report, OutputDir + 'EXPORT-REPORT.txt');
  report.Free;
  AddMessage('FishAI: saved ' + OutputDir);
  AddMessage('FishAI: responses=' + IntToStr(ResponseCount) + ', targets=' + IntToStr(TargetCount) + ', review=' + IntToStr(Issues.Count - 1));
  Lines.Free; Paths.Free; Issues.Free; Audit.Free; LineKeys.Free; PathKeys.Free;
  Seen.Free; Voices.Free; Defaults.Free; Recursion.Free;
end;

end.
