; Nonproduction entry points for the isolated hosted-runner safety preflight.
; The main template compiles out the production sections in this mode and
; restricts every entry to its fixed synthetic roots before reaching this file.
!ifndef PB_SYNTHETIC_FIXTURE
  !error "The synthetic safety entry points must never enter a production setup"
!endif

Function NativeSafetyFixtureMain
  ; Verify the reviewed NT ABI on an ordinary Unicode file before any cleanup.
  !insertmacro SyntheticPhase "" "ordinary-unicode-open"
  StrCpy $NativeAccess 0x80000000
  StrCpy $NativeShare 1
  StrCpy $NativeDisposition 1
  StrCpy $NativeOptions 0x40
  Push "${PB_SYNTHETIC_ROOT}\ordinary-日本語.txt"
  Call OpenNoReparse
  Pop $0
  ${If} $0 == -1
    Call DataRefusal
  ${EndIf}
  !insertmacro SyntheticPhase "" "ordinary-unicode-close"
  System::Call 'kernel32::CloseHandle(p r0) i.r1'
  ${If} $1 == 0
    Call DataRefusal
  ${EndIf}
  !insertmacro SyntheticPhase "" "initialize-data-paths"
  Call InitializeDataPaths
  !insertmacro SyntheticPhase "" "create-fresh-application-directory"
  Call CreateFreshInstallDirectory
  ; Production payload extraction sets this path while the create handle is held.
  !insertmacro SyntheticPhase "" "set-application-output-path"
  ClearErrors
  SetOutPath "$INSTDIR"
  IfErrors 0 +2
    Call DataRefusal
  !insertmacro SyntheticPhase "" "verify-application-current-directory"
  System::Call 'kernel32::GetCurrentDirectoryW(i ${NSIS_MAX_STRLEN}, w .r0) i.r1'
  ${If} $1 == 0
  ${OrIf} $1 >= ${NSIS_MAX_STRLEN}
    Call DataRefusal
  ${EndIf}
  StrCmpS $0 $INSTDIR +2
    Call DataRefusal
  !insertmacro SyntheticPhase "" "ensure-owned-local-root"
  Push $LocalDataRoot
  Call EnsureOwnedRoot
  !insertmacro SyntheticPhase "" "ensure-owned-roaming-root"
  Push $RoamingDataRoot
  Call EnsureOwnedRoot
  ; This generated uninstaller invokes only the paired synthetic entry below.
  !insertmacro SyntheticPhase "" "write-synthetic-uninstaller"
  ClearErrors
  WriteUninstaller "$INSTDIR\uninstall.exe"
  IfErrors 0 +2
    Call DataRefusal
  !insertmacro SyntheticPhase "" "initialization-complete"
  SetErrorLevel 0
FunctionEnd

Function un.NativeSafetyFixtureMain
  !insertmacro SyntheticPhase "un." "uninstall-initialize-data-paths"
  Call un.InitializeDataPaths
  !insertmacro SyntheticPhase "un." "uninstall-lock-application"
  Call un.LockInstallDirectory
  !insertmacro SyntheticPhase "un." "uninstall-verify-local-root"
  Push $LocalDataRoot
  Call un.VerifyOwnedRoot
  !insertmacro SyntheticPhase "un." "uninstall-verify-roaming-root"
  Push $RoamingDataRoot
  Call un.VerifyOwnedRoot
  ; Reuse the complete production traversal and marker-last cleanup routines.
  ; Registration, shortcuts, payload files and browsers are never involved.
  !insertmacro SyntheticPhase "un." "uninstall-purge-owned-data"
  Call un.PurgeOwnedData
  !insertmacro SyntheticPhase "un." "uninstall-complete"
  SetErrorLevel 0
FunctionEnd
