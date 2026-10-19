; Nonproduction entry points for the isolated hosted-runner safety preflight.
; The main template compiles out the production sections in this mode and
; restricts every entry to its fixed synthetic roots before reaching this file.
!ifndef PB_SYNTHETIC_FIXTURE
  !error "The synthetic safety entry points must never enter a production setup"
!endif

Function NativeSafetyFixtureMain
  ; Verify the reviewed NT ABI on an ordinary Unicode file before any cleanup.
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
  System::Call 'kernel32::CloseHandle(p r0) i.r1'
  ${If} $1 == 0
    Call DataRefusal
  ${EndIf}
  Call InitializeDataPaths
  IfFileExists "$INSTDIR\*.*" 0 +2
    Call DataRefusal
  Push "${PB_SYNTHETIC_ROOT}"
  Call LockDirectoryChain
  CreateDirectory "$INSTDIR"
  Call LockInstallDirectory
  Push $LocalDataRoot
  Call EnsureOwnedRoot
  Push $RoamingDataRoot
  Call EnsureOwnedRoot
  ; This generated uninstaller invokes only the paired synthetic entry below.
  ClearErrors
  WriteUninstaller "$INSTDIR\uninstall.exe"
  IfErrors 0 +2
    Call DataRefusal
  SetErrorLevel 0
FunctionEnd

Function un.NativeSafetyFixtureMain
  Call un.InitializeDataPaths
  Call un.LockInstallDirectory
  Push $LocalDataRoot
  Call un.VerifyOwnedRoot
  Push $RoamingDataRoot
  Call un.VerifyOwnedRoot
  ; Reuse the complete production traversal and marker-last cleanup routines.
  ; Registration, shortcuts, payload files and browsers are never involved.
  Call un.PurgeOwnedData
  SetErrorLevel 0
FunctionEnd
