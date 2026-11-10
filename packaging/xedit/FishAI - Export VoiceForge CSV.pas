unit FishAIExport;
uses FishAIExportCore;
var ActiveExport: boolean;
function Initialize: integer;
var parent, folder: string;
begin
  Result := 0; ActiveExport := False;
  parent := SelectDirectory('FishAI - choose a folder for CSV export', '', ScriptsPath, nil);
  if parent = '' then begin Result := 1; Exit; end;
  folder := IncludeTrailingBackslash(parent) + 'VoiceForge-' + FormatDateTime('yyyymmdd-hhnnss', Now);
  if DirectoryExists(folder) then begin
    AddMessage('Output directory already exists. Try again.'); Result := 1; Exit;
  end;
  StartExport(folder); ActiveExport := True;
end;
function Process(e: IInterface): integer;
begin
  Result := 0;
  if ActiveExport then ExportInfo(e);
end;
function Finalize: integer;
begin
  Result := 0;
  if ActiveExport then FinishExport;
end;
end.
