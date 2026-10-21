; Nonproduction entry points for the isolated hosted-runner safety preflight.
; The main template compiles out the production sections in this mode and
; restricts every entry to its fixed synthetic roots before reaching this file.
!ifndef PB_SYNTHETIC_FIXTURE
  !error "The synthetic safety entry points must never enter a production setup"
!endif


; These helpers exist only in the diagnostic compilation. Directory handles
; remain pinned until the synthetic process exits; the bounded three cases
; deliberately keep their own parents rather than opening system shell folders.
Var SyntheticShortcutDirectoryLocks
!macro SyntheticNewShortcutDirectory PATH
  StrCpy $NativeAccess 0x81
  StrCpy $NativeShare 3
  StrCpy $NativeDisposition 2
  StrCpy $NativeOptions 1
  Push "${PATH}"
  Call OpenNoReparse
  Pop $0
  ${If} $0 == 0
  ${OrIf} $0 == -1
    Call DataRefusal
  ${EndIf}
  System::Alloc 52
  Pop $1
  ${If} $1 == 0
    Call DataRefusal
  ${EndIf}
  System::Call 'kernel32::GetFileInformationByHandle(p r0, p r1) i.r2'
  ${If} $2 == 0
    Call DataRefusal
  ${EndIf}
  System::Call '*$1(i.r2)'
  System::Free $1
  IntOp $2 $2 & 0x410
  ${If} $2 != 16
    Call DataRefusal
  ${EndIf}
  StrCpy $SyntheticShortcutDirectoryLocks "$0|$SyntheticShortcutDirectoryLocks"
!macroend

; Preserve the production writer, including its custom-extension behavior.
; Refuse existing leaves, including reparses, inside freshly created/pinned
; directories. IPersistFile.Save is a pathname writer, not atomic CREATE_NEW;
; this diagnostic does not claim to eliminate its absence-check/write race.
!macro SyntheticNewShortcut PATH TARGET ARGUMENTS ICON
  StrCpy $NativeAccess 0x80000000
  StrCpy $NativeShare 1
  StrCpy $NativeDisposition 1
  StrCpy $NativeOptions 0x40
  Push "${PATH}"
  Call OpenNoReparse
  Pop $0
  ${If} $0 != -1
  ${OrIf} $NativeError != 2
    Call DataRefusal
  ${EndIf}
  ClearErrors
  CreateShortcut "${PATH}" "${TARGET}" '${ARGUMENTS}' "${ICON}" 0
  IfErrors 0 +2
    Call DataRefusal
  ; Verify the newly written object without following any reparse component.
  Push "${PATH}"
  Call OpenNoReparse
  Pop $0
  ${If} $0 == -1
    Call DataRefusal
  ${EndIf}
  System::Call 'kernel32::CloseHandle(p r0) i.r1'
  ${If} $1 == 0
    Call DataRefusal
  ${EndIf}
!macroend

!macro SyntheticShortcutCase CASE_ID FOLDER PROFILE
  !insertmacro SyntheticNewShortcutDirectory "$INSTDIR\shortcut-probe\${FOLDER}"
  !insertmacro SyntheticNewShortcutDirectory "$INSTDIR\shortcut-probe\${FOLDER}\relocated"
  ; Keep link-file path encoding independent of the unchanged target/arguments.
  !insertmacro SyntheticNewShortcutDirectory "$INSTDIR\shortcut-probe\ascii-link-copies-${CASE_ID}"
  StrCpy $0 "$INSTDIR\shortcut-probe\${FOLDER}\privacybrowser.exe"
  StrCpy $1 "Inert text target. Never execute."
  Call WriteNewAsciiFile
  StrCpy $0 "$INSTDIR\shortcut-probe\${FOLDER}\reference.ico"
  StrCpy $1 "Inert icon path reference. Never render."
  Call WriteNewAsciiFile
  ; NSIS CreateShortcut records the current $OUTDIR as working directory.
  ClearErrors
  SetOutPath "$INSTDIR\shortcut-probe\${FOLDER}"
  IfErrors 0 +2
    Call DataRefusal
  !insertmacro SyntheticNewShortcut "$INSTDIR\shortcut-probe\${FOLDER}\direct.lnk" "$INSTDIR\shortcut-probe\${FOLDER}\privacybrowser.exe" '-no-remote -profile "$INSTDIR\shortcut-probe\profiles\${PROFILE}"' "$INSTDIR\shortcut-probe\${FOLDER}\reference.ico"
  !insertmacro SyntheticNewShortcut "$INSTDIR\shortcut-probe\${FOLDER}\PrivacyBrowser.desktop-link" "$INSTDIR\shortcut-probe\${FOLDER}\privacybrowser.exe" '-no-remote -profile "$INSTDIR\shortcut-probe\profiles\${PROFILE}"' "$INSTDIR\shortcut-probe\${FOLDER}\reference.ico"
  !insertmacro SyntheticNewShortcut "$INSTDIR\shortcut-probe\${FOLDER}\PrivacyBrowser.program-link" "$INSTDIR\shortcut-probe\${FOLDER}\privacybrowser.exe" '-no-remote -profile "$INSTDIR\shortcut-probe\profiles\${PROFILE}"' "$INSTDIR\shortcut-probe\${FOLDER}\reference.ico"
  StrCpy $0 "$INSTDIR\shortcut-probe\${FOLDER}\PrivacyBrowser.desktop-link"
  StrCpy $1 "$INSTDIR\shortcut-probe\${FOLDER}\relocated\desktop.lnk"
  Call CopyNewShortcut
  StrCpy $0 "$INSTDIR\shortcut-probe\${FOLDER}\PrivacyBrowser.program-link"
  StrCpy $1 "$INSTDIR\shortcut-probe\${FOLDER}\relocated\program.lnk"
  Call CopyNewShortcut
  StrCpy $0 "$INSTDIR\shortcut-probe\${FOLDER}\PrivacyBrowser.desktop-link"
  StrCpy $1 "$INSTDIR\shortcut-probe\ascii-link-copies-${CASE_ID}\desktop.lnk"
  Call CopyNewShortcut
  StrCpy $0 "$INSTDIR\shortcut-probe\${FOLDER}\PrivacyBrowser.program-link"
  StrCpy $1 "$INSTDIR\shortcut-probe\ascii-link-copies-${CASE_ID}\program.lnk"
  Call CopyNewShortcut
!macroend

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
  ; All probes remain below the fresh owned app root, never Desktop/Programs.
  !insertmacro SyntheticPhase "" "create-shortcut-roundtrip-fixtures"
  !insertmacro SyntheticNewShortcutDirectory "$INSTDIR\shortcut-probe"
  !insertmacro SyntheticShortcutCase "ascii" "PrivacyBrowser-ASCII" "ascii profile"
  !insertmacro SyntheticShortcutCase "unicode" "PrivacyBrowser-日本語" "日本語 profile"
  !insertmacro SyntheticShortcutCase "unicode-space" "PrivacyBrowser 日本語 Space" "日本語 space profile"
  ClearErrors
  SetOutPath "$INSTDIR"
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
  ; Registration, system shortcuts, production payloads and browsers are never involved.
  !insertmacro SyntheticPhase "un." "uninstall-purge-owned-data"
  Call un.PurgeOwnedData
  !insertmacro SyntheticPhase "un." "uninstall-complete"
  SetErrorLevel 0
FunctionEnd
