; Unsigned PrivacyBrowser native source-build development candidate.
; Compiled only after static native identity and provenance checks.
; Per-user install: no default-browser registration or file associations.
Unicode true
!if ${NSIS_PTR_SIZE} != 4
  !error "Reviewed NT structure layouts require the x86 Unicode NSIS stub"
!endif
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "x64.nsh"
!include "WinVer.nsh"
!include "FileFunc.nsh"
Var LocalDataRoot
Var RoamingDataRoot
Var PurgeUserData
Var DirectoryLocks
Var DirectoryLockCount
Var InstallDirectoryLock
Var ProfileLocks
Var TreeMode
Var DirectoryAccess
Var LocalRootLock
Var RoamingRootLock
Var PayloadLocks
Var ProgramDirectoryLock
Var NativeAccess
Var NativeShare
Var NativeDisposition
Var NativeOptions
Var NativeError
Var OptionalDirectoryRemoval
Var ShortcutDelete
!define DATA_MARKER "PrivacyBrowser.userdata-owner"
!define DATA_PROTOCOL "PrivacyBrowser-UserData-v1"
!ifdef PB_SYNTHETIC_FIXTURE
  !if "${PB_SYNTHETIC_FIXTURE}" != "1"
    !error "Invalid synthetic fixture flag"
  !endif
  !if "${PB_SYNTHETIC_ROOT}" != "D:\pb\installer-syntax\execution"
    !error "Synthetic roots must use the exact isolated hosted-runner directory"
  !endif
  !ifndef PB_SYNTHETIC_INCLUDE
    !error "Synthetic builds require the reviewed fixture entry include"
  !endif
  Var SyntheticPhase
  Var SyntheticEvent
  Var SyntheticNtStatus
  Var SyntheticPathCharacters
  Var SyntheticTraceCount
!endif

!ifdef PB_SYNTHETIC_FIXTURE
!macro SyntheticPhase PREFIX NAME
  StrCpy $SyntheticPhase "${NAME}"
  StrCpy $SyntheticEvent "phase"
  Call ${PREFIX}TraceSyntheticState
!macroend
!endif
!ifndef RUNTIME_DIR
  !error "Set RUNTIME_DIR to a validated independently source-built AMD64 runtime"
!endif
!ifndef PAYLOAD_INCLUDE
  !error "Generate an exact payload include with tools/package_windows_native.py"
!endif
!ifndef OUTPUT_FILE
  !error "Set OUTPUT_FILE"
!endif
!include "${PAYLOAD_INCLUDE}"
Name "PrivacyBrowser"
OutFile "${OUTPUT_FILE}"
!ifdef PB_SYNTHETIC_FIXTURE
InstallDir "${PB_SYNTHETIC_ROOT}\app"
!else
InstallDir "$LOCALAPPDATA\Programs\PrivacyBrowser"
!endif
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 32
ShowInstDetails show
ShowUninstDetails show
BrandingText "PrivacyBrowser 0.1.0-dev"
VIProductVersion "0.1.0.0"
VIAddVersionKey /LANG=1033 "ProductName" "PrivacyBrowser Setup"
VIAddVersionKey /LANG=1033 "FileDescription" "PrivacyBrowser Installer"
VIAddVersionKey /LANG=1033 "CompanyName" "PrivacyBrowser contributors"
VIAddVersionKey /LANG=1033 "InternalName" "PrivacyBrowser Setup"
VIAddVersionKey /LANG=1033 "FileVersion" "0.1.0-dev"
VIAddVersionKey /LANG=1033 "ProductVersion" "0.1.0-dev"
VIAddVersionKey /LANG=1033 "OriginalFilename" "PrivacyBrowser-0.1.0-dev-win64-setup-UNSIGNED.exe"
VIAddVersionKey /LANG=1033 "LegalCopyright" "MPL-2.0; upstream licenses retained"
!define MUI_ICON "${RUNTIME_DIR}/distribution/privacybrowser/assets/privacybrowser.ico"
!define MUI_UNICON "${RUNTIME_DIR}/distribution/privacybrowser/assets/privacybrowser.ico"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "PrivacyBrowser Setup"
!define MUI_WELCOMEPAGE_TEXT "Install PrivacyBrowser 0.1.0-dev for this Windows account.$\r$\n$\r$\nIndependently source-built experimental browser based on Firefox ESR. Upstream copyright and license notices are retained.$\r$\n$\r$\nThis setup is unsigned. Automatic updates are disabled. Crash-proof history non-persistence is not guaranteed. This installer does not change your default browser or file associations."
!ifndef PB_SYNTHETIC_FIXTURE
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_FUNCTION LaunchPrivacyBrowser
!define MUI_FINISHPAGE_RUN_TEXT "Open PrivacyBrowser"
!define MUI_FINISHPAGE_RUN_NOTCHECKED
!endif
!insertmacro MUI_PAGE_WELCOME
; License and third-party notices remain in the payload; no new EULA is introduced.
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "Japanese"

; Only the two shell-derived product roots are managed. Environment/profile
; overrides and paths read from profile or launcher settings are never followed.
!macro OwnershipFunctions PREFIX
Function ${PREFIX}DataRefusal
!ifdef PB_SYNTHETIC_FIXTURE
  StrCpy $SyntheticEvent "refusal"
  Call ${PREFIX}TraceSyntheticState
!endif
  MessageBox MB_OK|MB_ICONSTOP "PrivacyBrowser user-data ownership or file-safety checks failed. No further files will be removed. Close the application and resolve the unsafe or locked path before retrying. Uninstall recovery information is retained." /SD IDOK
  SetErrorLevel 1
  Abort
FunctionEnd

!ifdef PB_SYNTHETIC_FIXTURE
; ASCII-only diagnostics go to the parent's already-open log handle. Never open
; a diagnostic path, alter a safety decision, or recurse into the native opener.
; At most 64 trace lines plus the terminal refusal are emitted per process.
Function ${PREFIX}TraceSyntheticState
  ${If} $SyntheticTraceCount >= 64
  ${AndIf} $SyntheticEvent != "refusal"
    Return
  ${EndIf}
  IntOp $SyntheticTraceCount $SyntheticTraceCount + 1
  Push $0
  Push $1
  Push $2
  Push $3
  Push $4
  StrCpy $1 "PB_SYNTHETIC phase=$SyntheticPhase event=$SyntheticEvent ntstatus=$SyntheticNtStatus native_error=$NativeError path_chars=$SyntheticPathCharacters access=$NativeAccess share=$NativeShare disposition=$NativeDisposition options=$NativeOptions$\r$\n"
  StrLen $2 $1
  System::Call 'kernel32::GetStdHandle(i -11) p.r0'
  ${If} $0 != 0
  ${AndIf} $0 != -1
    System::Call 'kernel32::WriteFile(p r0, m r1, i r2, *i.r3, p 0) i.r4'
  ${EndIf}
  Pop $4
  Pop $3
  Pop $2
  Pop $1
  Pop $0
FunctionEnd
!endif

; Open an absolute native path with the kernel's all-components no-reparse
; guarantee. Unlike CreateFileW OPEN_REPARSE_POINT alone, OBJ_DONT_REPARSE also
; refuses intermediate junctions inserted concurrently. All deletes use the
; resulting handle, never a later unguarded pathname operation.
Function ${PREFIX}OpenNoReparse
  Exch $0
  Push $1
  Push $2
  Push $3
  Push $4
  Push $5
  Push $6
  Push $7
  Push $8
  Push $9
  StrCpy $1 $0 2
  ${If} $1 == "\\"
    StrCpy $0 $0 "" 2
    StrCpy $0 "\??\UNC\$0"
  ${Else}
    StrCpy $0 "\??\$0"
  ${EndIf}
  StrLen $2 $0
!ifdef PB_SYNTHETIC_FIXTURE
  StrCpy $SyntheticPathCharacters $2
!endif
  ${If} $2 > 900
    Call ${PREFIX}DataRefusal
  ${EndIf}
  IntOp $2 $2 * 2
  IntOp $3 $2 + 2
  System::Call '*(&w${NSIS_MAX_STRLEN} r0) p.r1'
  System::Call '*(h r2, h r3, p r1) p.r4'
  System::Call '*(i 24, p 0, p r4, i 0x1040, p 0, p 0) p.r5'
  System::Alloc 8
  Pop $6
  ${If} $1 == 0
  ${OrIf} $4 == 0
  ${OrIf} $5 == 0
  ${OrIf} $6 == 0
    Call ${PREFIX}DataRefusal
  ${EndIf}
  IntOp $8 $NativeAccess | 0x100000
  IntOp $9 $NativeOptions | 0x00200020
  System::Call 'ntdll::NtCreateFile(*p.r7, i r8, p r5, p r6, p 0, i 0, i $NativeShare, i $NativeDisposition, i r9, p 0, i 0) i.r8'
!ifdef PB_SYNTHETIC_FIXTURE
  StrCpy $SyntheticNtStatus $8
!endif
  StrCpy $NativeError 0
  IntOp $9 $8 & 0x80000000
  ${If} $9 != 0
    System::Call 'ntdll::RtlNtStatusToDosError(i r8) i.r9'
    StrCpy $NativeError $9
    StrCpy $7 -1
  ${EndIf}
!ifdef PB_SYNTHETIC_FIXTURE
  StrCpy $SyntheticEvent "nt-open"
  Call ${PREFIX}TraceSyntheticState
!endif
  System::Free $6
  System::Free $5
  System::Free $4
  System::Free $1
  StrCpy $0 $7
  Pop $9
  Pop $8
  Pop $7
  Pop $6
  Pop $5
  Pop $4
  Pop $3
  Pop $2
  Pop $1
  Exch $0
FunctionEnd

Function ${PREFIX}InitializeDataPaths
  StrCpy $DirectoryLocks 0
  StrCpy $DirectoryLockCount 0
  StrCpy $ProfileLocks 0
  StrCpy $InstallDirectoryLock 0
  StrCpy $DirectoryAccess 0x81
  StrCpy $LocalRootLock 0
  StrCpy $RoamingRootLock 0
  StrCpy $PayloadLocks 0
  StrCpy $ProgramDirectoryLock 0
  StrCpy $OptionalDirectoryRemoval 0
  StrCpy $ShortcutDelete 0
  GetFullPathName $INSTDIR "$INSTDIR"
!ifdef PB_SYNTHETIC_FIXTURE
  StrCmpS $INSTDIR "${PB_SYNTHETIC_ROOT}\app" +2
    Call ${PREFIX}DataRefusal
  GetFullPathName $LocalDataRoot "${PB_SYNTHETIC_ROOT}\local\PrivacyBrowser"
  GetFullPathName $RoamingDataRoot "${PB_SYNTHETIC_ROOT}\roaming\PrivacyBrowser"
!else
  GetFullPathName $LocalDataRoot "$LOCALAPPDATA\PrivacyBrowser"
  GetFullPathName $RoamingDataRoot "$APPDATA\PrivacyBrowser"
!endif
  ; Stay below legacy Win32 path limits; never operate on truncated aliases.
  StrLen $0 $INSTDIR
  IntOp $1 $0 + ${NATIVE_MAX_RELATIVE_PATH}
  ${If} $1 > 240
    Call ${PREFIX}DataRefusal
  ${EndIf}
  ${If} $0 > 200
    Call ${PREFIX}DataRefusal
  ${EndIf}
  StrLen $0 $LocalDataRoot
  ${If} $0 > 200
    Call ${PREFIX}DataRefusal
  ${EndIf}
  StrLen $0 $RoamingDataRoot
  ${If} $0 > 200
    Call ${PREFIX}DataRefusal
  ${EndIf}
  ${If} $LocalDataRoot == $RoamingDataRoot
    Call ${PREFIX}DataRefusal
  ${EndIf}
!ifdef PB_SYNTHETIC_FIXTURE
  Push "${PB_SYNTHETIC_ROOT}\local"
  Call ${PREFIX}LockDirectoryChain
  Push "${PB_SYNTHETIC_ROOT}\roaming"
  Call ${PREFIX}LockDirectoryChain
!else
  Push "$LOCALAPPDATA"
  Call ${PREFIX}LockDirectoryChain
  Push "$APPDATA"
  Call ${PREFIX}LockDirectoryChain
!endif
  Push $0
  ${GetRoot} "$INSTDIR" $0
  ${If} $INSTDIR == $0
  ${OrIf} $INSTDIR == "$0\"
    Call ${PREFIX}DataRefusal
  ${EndIf}
  StrCpy $0 $INSTDIR
  StrCpy $1 $LocalDataRoot
  Call ${PREFIX}RejectOverlap
  StrCpy $0 $INSTDIR
  StrCpy $1 $RoamingDataRoot
  Call ${PREFIX}RejectOverlap
  StrCpy $0 $LocalDataRoot
  StrCpy $1 $RoamingDataRoot
  Call ${PREFIX}RejectOverlap
  Pop $0
FunctionEnd

; Walk components in both directions (NSIS comparisons are case-insensitive).
Function ${PREFIX}RejectOverlap
  Push $2
  Push $3
  Push $4
  StrCpy $4 0
  overlap_again:
  StrCpy $2 $0
  overlap_parent:
    ${If} $2 == $1
      Call ${PREFIX}DataRefusal
    ${EndIf}
    ${GetParent} "$2" $3
    ${If} $3 != ""
    ${AndIf} $3 != $2
      StrCpy $2 $3
      Goto overlap_parent
    ${EndIf}
  ${If} $4 == 0
    StrCpy $2 $0
    StrCpy $0 $1
    StrCpy $1 $2
    StrCpy $4 1
    Goto overlap_again
  ${EndIf}
  Pop $4
  Pop $3
  Pop $2
FunctionEnd

; Open the directory itself, never a junction target. The caller has already
; locked its parent chain. No FILE_SHARE_DELETE prevents directory replacement
; while descendants are checked or removed. Inspect attributes on this handle.
Function ${PREFIX}OpenSafeDirectory
  Exch $0
  Push $1
  Push $2
  Push $3
  StrCpy $3 3
  ; The primary purge-root handle already denies delete sharing. Secondary
  ; read handles must share DELETE with our own primary DELETE-capable handle.
  ${If} $0 == $LocalDataRoot
  ${AndIf} $LocalRootLock != 0
    StrCpy $3 7
  ${EndIf}
  ${If} $0 == $RoamingDataRoot
  ${AndIf} $RoamingRootLock != 0
    StrCpy $3 7
  ${EndIf}
  ${If} $0 == $INSTDIR
  ${AndIf} $InstallDirectoryLock != 0
    StrCpy $3 7
  ${EndIf}
  StrCpy $NativeAccess $DirectoryAccess
  StrCpy $NativeShare $3
  StrCpy $NativeDisposition 1
  StrCpy $NativeOptions 1
  Push $0
  Call ${PREFIX}OpenNoReparse
  Pop $1
  ${If} $1 == -1
    Call ${PREFIX}DataRefusal
  ${EndIf}
  System::Alloc 52
  Pop $2
  ${If} $2 == 0
    Call ${PREFIX}DataRefusal
  ${EndIf}
  System::Call 'kernel32::GetFileInformationByHandle(p r1, p r2) i.r3'
  ${If} $3 == 0
    Call ${PREFIX}DataRefusal
  ${EndIf}
  System::Call '*$2(i.r3)'
  System::Free $2
  IntOp $2 $3 & 0x400
  ${If} $2 != 0
    Call ${PREFIX}DataRefusal
  ${EndIf}
  IntOp $2 $3 & 0x10
  ${If} $2 == 0
    Call ${PREFIX}DataRefusal
  ${EndIf}
  StrCpy $0 $1
  Pop $3
  Pop $2
  Pop $1
  Exch $0
FunctionEnd

; Top-down ancestor locks remain open until this installer process exits.
; Nodes contain two pointers; 16 bytes accommodates either pointer width.
Function ${PREFIX}LockDirectoryChain
  Exch $0
  Push $1
  Push $2
  ${If} $0 == ""
    Call ${PREFIX}DataRefusal
  ${EndIf}
  StrLen $2 $0
  ${If} $2 == 2
    StrCpy $0 "$0\"
  ${EndIf}
  ${GetParent} "$0" $1
  ${GetRoot} "$0" $2
  ${If} $1 != ""
  ${AndIf} $1 != $0
  ${AndIf} $0 != $2
  ${AndIf} $0 != "$2\"
    Push $1
    Call ${PREFIX}LockDirectoryChain
  ${EndIf}
  IntOp $DirectoryLockCount $DirectoryLockCount + 1
  ${If} $DirectoryLockCount > 1024
    Call ${PREFIX}DataRefusal
  ${EndIf}
  Push $0
  Call ${PREFIX}OpenSafeDirectory
  Pop $1
  System::Alloc 16
  Pop $2
  ${If} $2 == 0
    Call ${PREFIX}DataRefusal
  ${EndIf}
  System::Call '*$2(p r1, p $DirectoryLocks)'
  StrCpy $DirectoryLocks $2
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

; A missing root is harmless (including a root removed by an earlier retry).
; An existing root must have our exact three-line marker, with no extra lines.
Function ${PREFIX}VerifyOwnedRoot
  Exch $0
  Push $1
  Push $2
  Push $3
  Push $4
  Push $5
  Push $6
  Push $7
  Push $8
  System::Call 'kernel32::GetFileAttributesW(w r0) i.r1 ?e'
  Pop $5
  ${If} $1 == -1
    ${If} $5 == 2
    ${OrIf} $5 == 3
      Goto owned_done
    ${EndIf}
    Call ${PREFIX}DataRefusal
  ${EndIf}
  Push $0
  Call ${PREFIX}OpenSafeDirectory
  Pop $6
  StrCpy $NativeAccess 0x80000000
  StrCpy $NativeShare 1
  StrCpy $NativeDisposition 1
  StrCpy $NativeOptions 0x40
  Push "$0\${DATA_MARKER}"
  Call ${PREFIX}OpenNoReparse
  Pop $7
  ${If} $7 == -1
    Call ${PREFIX}DataRefusal
  ${EndIf}
  System::Alloc 52
  Pop $8
  ${If} $8 == 0
    Call ${PREFIX}DataRefusal
  ${EndIf}
  System::Call 'kernel32::GetFileInformationByHandle(p r7, p r8) i.r1'
  ${If} $1 == 0
    Call ${PREFIX}DataRefusal
  ${EndIf}
  System::Call '*$8(i.r1)'
  ; The exact BOM-free byte length also rejects trailing garbage/odd UTF-16.
  StrCpy $3 "${DATA_PROTOCOL}$\r$\n$INSTDIR$\r$\n$0$\r$\n"
  StrLen $4 $3
  IntOp $4 $4 * 2
  System::Call 'kernel32::GetFileSizeEx(p r7, *l.r3) i.r5'
  ${If} $5 == 0
  ${OrIf} $3 != $4
    Call ${PREFIX}DataRefusal
  ${EndIf}
  System::Free $8
  IntOp $1 $1 & 0x410
  ${If} $1 != 0
    Call ${PREFIX}DataRefusal
  ${EndIf}
  ClearErrors
  FileOpen $2 "$0\${DATA_MARKER}" r
  IfErrors 0 +2
    Call ${PREFIX}DataRefusal
  FileReadUTF16LE $2 $3
  FileReadUTF16LE $2 $4
  FileReadUTF16LE $2 $5
  IfErrors 0 +2
    Call ${PREFIX}DataRefusal
  StrCmpS $3 "${DATA_PROTOCOL}$\r$\n" 0 owned_invalid
  StrCmpS $4 "$INSTDIR$\r$\n" 0 owned_invalid
  StrCmpS $5 "$0$\r$\n" 0 owned_invalid
  FileReadUTF16LE $2 $3
  FileClose $2
  ${If} $3 != ""
    Call ${PREFIX}DataRefusal
  ${EndIf}
  System::Call 'kernel32::CloseHandle(p r7)'
  System::Call 'kernel32::CloseHandle(p r6)'
  Goto owned_done
  owned_invalid:
  FileClose $2
  Call ${PREFIX}DataRefusal
  owned_done:
  Pop $8
  Pop $7
  Pop $6
  Pop $5
  Pop $4
  Pop $3
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

Function ${PREFIX}WriteOwnershipMarker
  Exch $0
  Push $1
  Push $2
  Push $0
  Call ${PREFIX}OpenSafeDirectory
  Pop $2
  Push $0
  Call ${PREFIX}WriteMarkerBytes
  System::Call 'kernel32::CloseHandle(p r2)'
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

; The caller holds the verified parent directory handle throughout creation.
Function ${PREFIX}WriteMarkerBytes
  Exch $0
  Push $1
  Push $2
  Push $3
  Push $4
  StrLen $1 $INSTDIR
  StrLen $2 $0
  IntOp $1 $1 + $2
  ${If} $1 > 800
    Call ${PREFIX}DataRefusal
  ${EndIf}
  ; CREATE_NEW is atomic: never truncate a marker that appeared concurrently.
  StrCpy $NativeAccess 0x40000000
  StrCpy $NativeShare 0
  StrCpy $NativeDisposition 2
  StrCpy $NativeOptions 0x40
  Push "$0\${DATA_MARKER}"
  Call ${PREFIX}OpenNoReparse
  Pop $1
  ${If} $1 == -1
    Call ${PREFIX}DataRefusal
  ${EndIf}
  ; WriteFile's explicit UTF-16LE buffer is BOM-free, exactly three CRLF lines.
  StrCpy $3 "${DATA_PROTOCOL}$\r$\n$INSTDIR$\r$\n$0$\r$\n"
  StrLen $4 $3
  IntOp $4 $4 * 2
  System::Call 'kernel32::WriteFile(p r1, w r3, i r4, *i.r3, p 0) i.r0'
  ${If} $0 == 0
  ${OrIf} $3 != $4
    Call ${PREFIX}DataRefusal
  ${EndIf}
  System::Call 'kernel32::CloseHandle(p r1)'
  Pop $4
  Pop $3
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

Function ${PREFIX}RejectExistingMetadata
  ; Refuse a second install before it can overwrite another copy's shortcuts.
  System::Call 'advapi32::RegOpenKeyExW(p 0x80000001, w "Software\Microsoft\Windows\CurrentVersion\Uninstall\PrivacyBrowser", i 0, i 0x20119, *p.r0) i.r1'
  ${If} $1 == 0
    System::Call 'advapi32::RegCloseKey(p r0)'
    Call ${PREFIX}DataRefusal
  ${ElseIf} $1 != 2
    Call ${PREFIX}DataRefusal
  ${EndIf}
  IfFileExists "$DESKTOP\PrivacyBrowser.lnk" 0 +2
    Call ${PREFIX}DataRefusal
  IfFileExists "$SMPROGRAMS\PrivacyBrowser" 0 +2
    Call ${PREFIX}DataRefusal
FunctionEnd

Function ${PREFIX}VerifyRegistration
  ReadRegStr $0 HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PrivacyBrowser" "InstallLocation"
  ReadRegStr $1 HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PrivacyBrowser" "UninstallString"
  ${If} $0 == ""
  ${AndIf} $1 == ""
    ; Only an in-progress owned cleanup can retry after its registration is gone.
    System::Call 'advapi32::RegOpenKeyExW(p 0x80000001, w "Software\Microsoft\Windows\CurrentVersion\Uninstall\PrivacyBrowser", i 0, i 0x20119, *p.r0) i.r1'
    ${If} $1 == 0
      System::Call 'advapi32::RegCloseKey(p r0)'
      Call ${PREFIX}DataRefusal
    ${ElseIf} $1 != 2
      Call ${PREFIX}DataRefusal
    ${EndIf}
    Call ${PREFIX}VerifyCleanupIntent
    Return
  ${EndIf}
  StrCmpS $0 $INSTDIR 0 registration_invalid
  StrCmpS $1 '$\"$INSTDIR\uninstall.exe$\"' 0 registration_invalid
  Return
  registration_invalid:
  Call ${PREFIX}DataRefusal
FunctionEnd

Function ${PREFIX}VerifyCleanupIntent
  Push $0
  Push $1
  StrCpy $0 "$INSTDIR\PrivacyBrowser.cleanup-intent"
  StrCpy $1 "PrivacyBrowser-Cleanup-v1"
  Call ${PREFIX}VerifyAsciiFile
  Pop $1
  Pop $0
FunctionEnd

; Fixed recovery protocols are exact-size ASCII and read from a no-follow handle.
Function ${PREFIX}VerifyAsciiFile
  Push $2
  Push $3
  Push $4
  Push $5
  Push $6
  Push $7
  StrLen $4 $1
  ${If} $4 > 63
    Call ${PREFIX}DataRefusal
  ${EndIf}
  StrCpy $NativeAccess 0x80000000
  StrCpy $NativeShare 1
  StrCpy $NativeDisposition 1
  StrCpy $NativeOptions 0x40
  Push $0
  Call ${PREFIX}OpenNoReparse
  Pop $2
  ${If} $2 == -1
    Call ${PREFIX}DataRefusal
  ${EndIf}
  System::Call 'kernel32::GetFileSizeEx(p r2, *l.r5) i.r6'
  ${If} $6 == 0
  ${OrIf} $5 != $4
    Call ${PREFIX}DataRefusal
  ${EndIf}
  System::Alloc 64
  Pop $3
  ${If} $3 == 0
    Call ${PREFIX}DataRefusal
  ${EndIf}
  System::Call 'kernel32::ReadFile(p r2, p r3, i r4, *i.r5, p 0) i.r6'
  ${If} $6 == 0
  ${OrIf} $5 != $4
    Call ${PREFIX}DataRefusal
  ${EndIf}
  IntOp $5 $3 + $4
  System::Call '*$5(&i1 0)'
  System::Call '*$3(&m64.r7)'
  System::Free $3
  System::Call 'kernel32::CloseHandle(p r2)'
  StrCmpS $7 $1 +2
    Call ${PREFIX}DataRefusal
  Pop $7
  Pop $6
  Pop $5
  Pop $4
  Pop $3
  Pop $2
FunctionEnd

; $0 path, $1 fixed ASCII contents. CREATE_NEW never overwrites another object.
Function ${PREFIX}WriteNewAsciiFile
  Push $2
  Push $3
  Push $4
  StrCpy $NativeAccess 0x40000000
  StrCpy $NativeShare 0
  StrCpy $NativeDisposition 2
  StrCpy $NativeOptions 0x40
  Push $0
  Call ${PREFIX}OpenNoReparse
  Pop $2
  ${If} $2 == -1
    Call ${PREFIX}DataRefusal
  ${EndIf}
  StrLen $3 $1
  System::Call 'kernel32::WriteFile(p r2, m r1, i r3, *i.r4, p 0) i.r1'
  ${If} $1 == 0
  ${OrIf} $3 != $4
    Call ${PREFIX}DataRefusal
  ${EndIf}
  System::Call 'kernel32::CloseHandle(p r2)'
  Pop $4
  Pop $3
  Pop $2
FunctionEnd

; $0 owned snapshot, $1 new shortcut. Both endpoints are opened without reparses;
; CREATE_NEW refuses a foreign destination and handle writes cannot follow a swap.
Function ${PREFIX}CopyNewShortcut
  Push $2
  Push $3
  Push $4
  Push $5
  Push $6
  Push $7
  StrCpy $NativeAccess 0x80000000
  StrCpy $NativeShare 1
  StrCpy $NativeDisposition 1
  StrCpy $NativeOptions 0x40
  Push $0
  Call ${PREFIX}OpenNoReparse
  Pop $2
  ${If} $2 == -1
    Call ${PREFIX}DataRefusal
  ${EndIf}
  StrCpy $NativeAccess 0x40000000
  StrCpy $NativeShare 0
  StrCpy $NativeDisposition 2
  StrCpy $NativeOptions 0x40
  Push $1
  Call ${PREFIX}OpenNoReparse
  Pop $3
  ${If} $3 == -1
    Call ${PREFIX}DataRefusal
  ${EndIf}
  System::Alloc 4096
  Pop $4
  ${If} $4 == 0
    Call ${PREFIX}DataRefusal
  ${EndIf}
  copy_shortcut_read:
  System::Call 'kernel32::ReadFile(p r2, p r4, i 4096, *i.r5, p 0) i.r6'
  ${If} $6 == 0
    Call ${PREFIX}DataRefusal
  ${EndIf}
  ${If} $5 == 0
    Goto copy_shortcut_done
  ${EndIf}
  System::Call 'kernel32::WriteFile(p r3, p r4, i r5, *i.r7, p 0) i.r6'
  ${If} $6 == 0
  ${OrIf} $7 != $5
    Call ${PREFIX}DataRefusal
  ${EndIf}
  Goto copy_shortcut_read
  copy_shortcut_done:
  System::Free $4
  System::Call 'kernel32::CloseHandle(p r3)'
  System::Call 'kernel32::CloseHandle(p r2)'
  Pop $7
  Pop $6
  Pop $5
  Pop $4
  Pop $3
  Pop $2
FunctionEnd

Function ${PREFIX}EnsureCleanupIntent
  IfFileExists "$INSTDIR\PrivacyBrowser.cleanup-intent" intent_exists
  StrCpy $0 "$INSTDIR\PrivacyBrowser.cleanup-intent"
  StrCpy $1 "PrivacyBrowser-Cleanup-v1"
  Call ${PREFIX}WriteNewAsciiFile
  intent_exists:
  Call ${PREFIX}VerifyCleanupIntent
FunctionEnd

Function ${PREFIX}LockShortcutParents
  Push "$DESKTOP"
  Call ${PREFIX}LockDirectoryChain
  Push "$SMPROGRAMS"
  Call ${PREFIX}LockDirectoryChain
  IfFileExists "$SMPROGRAMS\PrivacyBrowser" 0 shortcut_parent_done
    Push "$SMPROGRAMS\PrivacyBrowser"
    Call ${PREFIX}OpenSafeDirectory
    Pop $ProgramDirectoryLock
  shortcut_parent_done:
FunctionEnd

Function ${PREFIX}LockInstallDirectory
  ${GetParent} "$INSTDIR" $0
  Push $0
  Call ${PREFIX}LockDirectoryChain
  StrCpy $DirectoryAccess 0x10081
  Push $INSTDIR
  Call ${PREFIX}OpenSafeDirectory
  Pop $InstallDirectoryLock
  StrCpy $DirectoryAccess 0x81
FunctionEnd

!ifdef PB_SYNTHETIC_FIXTURE
Function ${PREFIX}RequireSyntheticEnvironment
  StrCpy $SyntheticTraceCount 0
  StrCpy $SyntheticNtStatus "not-called"
  StrCpy $NativeError "not-called"
  !insertmacro SyntheticPhase "${PREFIX}" "host-environment"
  ReadEnvStr $0 "GITHUB_ACTIONS"
  ReadEnvStr $1 "RUNNER_ENVIRONMENT"
  StrCmpS $0 "true" +2
    Call ${PREFIX}DataRefusal
  StrCmpS $1 "github-hosted" +2
    Call ${PREFIX}DataRefusal
  !insertmacro SyntheticPhase "${PREFIX}" "executable-directory"
  StrCmpS $EXEDIR "${PB_SYNTHETIC_ROOT}" fixture_directory_ok
  StrCmpS $EXEDIR "${PB_SYNTHETIC_ROOT}\app" fixture_directory_ok
  Call ${PREFIX}DataRefusal
  fixture_directory_ok:
  !insertmacro SyntheticPhase "${PREFIX}" "environment-accepted"
FunctionEnd
!endif
!macroend
!insertmacro OwnershipFunctions ""
!insertmacro OwnershipFunctions "un."
!ifdef PB_SYNTHETIC_FIXTURE
  !include "${PB_SYNTHETIC_INCLUDE}"
!endif

Function EnsureOwnedRoot
  Exch $0
  Push $1
  Push $0
  Call VerifyOwnedRoot
  IfFileExists "$0\${DATA_MARKER}" owned_existing
  ; Atomic create refuses a directory that appeared after ownership validation.
  StrCpy $NativeAccess 0x81
  StrCpy $NativeShare 3
  StrCpy $NativeDisposition 2
  StrCpy $NativeOptions 1
  Push $0
  Call OpenNoReparse
  Pop $1
  ${If} $1 == -1
    Call DataRefusal
  ${EndIf}
  Push $0
  Call WriteOwnershipMarker
  Push $0
  Call VerifyOwnedRoot
  Goto owned_pinned
  owned_existing:
  Push $0
  Call OpenSafeDirectory
  Pop $1
  Push $0
  Call VerifyOwnedRoot
  owned_pinned:
  ; Keep the created/verified exact root pinned until installation exits.
  ${If} $0 == $LocalDataRoot
    StrCpy $LocalRootLock $1
  ${ElseIf} $0 == $RoamingDataRoot
    StrCpy $RoamingRootLock $1
  ${Else}
    Call DataRefusal
  ${EndIf}
  Pop $1
  Pop $0
FunctionEnd

Function .onInit
!ifdef PB_SYNTHETIC_FIXTURE
  Call RequireSyntheticEnvironment
  Call NativeSafetyFixtureMain
  Quit
!else
  SetShellVarContext current
  SetRegView 64
  ${IfNot} ${RunningX64}
    MessageBox MB_OK|MB_ICONSTOP "Windows 11 x64 is required." /SD IDOK
    SetErrorLevel 1
    Abort
  ${EndIf}
  ${IfNot} ${AtLeastWin10}
    MessageBox MB_OK|MB_ICONSTOP "Windows 11 x64 is required." /SD IDOK
    SetErrorLevel 1
    Abort
  ${EndIf}
  ReadRegStr $0 HKLM "SOFTWARE\Microsoft\Windows NT\CurrentVersion" "CurrentBuildNumber"
  ${If} $0 < 22000
    MessageBox MB_OK|MB_ICONSTOP "Windows 11 build 22000 or newer is required." /SD IDOK
    SetErrorLevel 1
    Abort
  ${EndIf}
  IfFileExists "$INSTDIR\*.*" 0 ready
    MessageBox MB_OK|MB_ICONSTOP "The installation folder is not empty. Uninstall the existing copy or choose a new empty folder. No files have been changed." /SD IDOK
    SetErrorLevel 1
    Abort
  ready:
  Call CheckBrowserClosed
  Call InitializeDataPaths
  Call RejectExistingMetadata
  Push $LocalDataRoot
  Call VerifyOwnedRoot
  Push $RoamingDataRoot
  Call VerifyOwnedRoot
!endif
FunctionEnd

Function CheckBrowserClosed
  System::Call 'kernel32::CreateFileW(w "$LOCALAPPDATA\PrivacyBrowser\Profile\parent.lock", i 0x80000000, i 0, p 0, i 3, i 0x80, p 0) p.r0 ?e'
  Pop $1
  ${If} $0 == -1
    ${If} $1 == 32
    ${OrIf} $1 == 33
      MessageBox MB_OK|MB_ICONSTOP "Close PrivacyBrowser before installing." /SD IDOK
      SetErrorLevel 1
      Abort
    ${EndIf}
  ${Else}
    System::Call 'kernel32::CloseHandle(p r0)'
  ${EndIf}
FunctionEnd

!ifndef PB_SYNTHETIC_FIXTURE
Function LaunchPrivacyBrowser
  CreateDirectory "$LOCALAPPDATA\PrivacyBrowser\Profile"
  ExecShell "open" "$INSTDIR\privacybrowser.exe" '-no-remote -profile "$LOCALAPPDATA\PrivacyBrowser\Profile"'
FunctionEnd

Section "PrivacyBrowser"
  SetShellVarContext current
  SetRegView 64
  Call RejectExistingMetadata
  IfFileExists "$INSTDIR\*.*" 0 install_still_empty
    Call DataRefusal
  install_still_empty:
  ; Create only the empty application directory after validating its parents.
  ; For the default path, Programs may not yet exist on a fresh account.
  ${If} $INSTDIR == "$LOCALAPPDATA\Programs\PrivacyBrowser"
    CreateDirectory "$LOCALAPPDATA\Programs"
  ${EndIf}
  ${GetParent} "$INSTDIR" $0
  Push $0
  Call LockDirectoryChain
  CreateDirectory "$INSTDIR"
  Call LockInstallDirectory
  IfFileExists "$INSTDIR\*.*" 0 app_directory_pinned_empty
    Call DataRefusal
  app_directory_pinned_empty:
  StrCpy $0 "$INSTDIR\PrivacyBrowser.install-marker"
  StrCpy $1 "PrivacyBrowser 0.1.0-dev"
  Call WriteNewAsciiFile
  Push $LocalDataRoot
  Call EnsureOwnedRoot
  Push $RoamingDataRoot
  Call EnsureOwnedRoot
  SetOverwrite on
  !insertmacro InstallPayload
  SetOutPath "$INSTDIR"
  WriteUninstaller "$INSTDIR\uninstall.exe"
  CreateDirectory "$LOCALAPPDATA\PrivacyBrowser\Profile"
  Push "$DESKTOP"
  Call LockDirectoryChain
  Push "$SMPROGRAMS"
  Call LockDirectoryChain
  StrCpy $NativeAccess 0x81
  StrCpy $NativeShare 3
  StrCpy $NativeDisposition 2
  StrCpy $NativeOptions 1
  Push "$SMPROGRAMS\PrivacyBrowser"
  Call OpenNoReparse
  Pop $ProgramDirectoryLock
  ${If} $ProgramDirectoryLock == -1
    Call DataRefusal
  ${EndIf}
  ; Build owned snapshots first; fail-if-exists copies never replace foreign links.
  CreateShortcut "$INSTDIR\PrivacyBrowser.program-link" "$INSTDIR\privacybrowser.exe" '-no-remote -profile "$LOCALAPPDATA\PrivacyBrowser\Profile"' "$INSTDIR\distribution\privacybrowser\assets\privacybrowser.ico"
  CreateShortcut "$INSTDIR\PrivacyBrowser.desktop-link" "$INSTDIR\privacybrowser.exe" '-no-remote -profile "$LOCALAPPDATA\PrivacyBrowser\Profile"' "$INSTDIR\distribution\privacybrowser\assets\privacybrowser.ico"
  StrCpy $0 "$INSTDIR\PrivacyBrowser.desktop-link"
  StrCpy $1 "$DESKTOP\PrivacyBrowser.lnk"
  Call CopyNewShortcut
  StrCpy $0 "$INSTDIR\PrivacyBrowser.program-link"
  StrCpy $1 "$SMPROGRAMS\PrivacyBrowser\PrivacyBrowser.lnk"
  Call CopyNewShortcut
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PrivacyBrowser" "DisplayName" "PrivacyBrowser"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PrivacyBrowser" "DisplayVersion" "0.1.0-dev"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PrivacyBrowser" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PrivacyBrowser" "DisplayIcon" "$INSTDIR\distribution\privacybrowser\assets\privacybrowser.ico"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PrivacyBrowser" "UninstallString" '$\"$INSTDIR\uninstall.exe$\"'
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PrivacyBrowser" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PrivacyBrowser" "NoRepair" 1
SectionEnd
!else
Section "Synthetic safety fixture"
SectionEnd
!endif

Function un.onInit
!ifdef PB_SYNTHETIC_FIXTURE
  Call un.RequireSyntheticEnvironment
  Call un.NativeSafetyFixtureMain
  Quit
!else
  SetShellVarContext current
  SetRegView 64
  Call un.InitializeDataPaths
  Call un.LockInstallDirectory
  Call un.VerifyRegistration
  IfFileExists "$INSTDIR\PrivacyBrowser.install-marker" 0 invalid
  StrCpy $0 "$INSTDIR\PrivacyBrowser.install-marker"
  StrCpy $1 "PrivacyBrowser 0.1.0-dev"
  Call un.VerifyAsciiFile
  Call un.CheckBrowserClosed
  Push $LocalDataRoot
  Call un.VerifyOwnedRoot
  Push $RoamingDataRoot
  Call un.VerifyOwnedRoot
  StrCpy $PurgeUserData 0
  ${GetParameters} $0
  ClearErrors
  ${GetOptions} "$0" "/PURGEUSERDATA" $1
  IfErrors no_purge_flag
  ${If} $1 != ""
    Call un.DataRefusal
  ${EndIf}
  StrCpy $PurgeUserData 1
  no_purge_flag:
  IfSilent consent_done
  ; A GUI invocation always asks, even when a command-line purge flag is present.
  MessageBox MB_YESNOCANCEL|MB_ICONEXCLAMATION|MB_DEFBUTTON2 "Permanently delete this installation's PrivacyBrowser profiles, saved passwords, bookmarks, history, settings and cache in:$\r$\n$LocalDataRoot$\r$\n$RoamingDataRoot$\r$\n$\r$\nThis cannot be undone. Downloaded files outside these owned folders and other browsers are preserved.$\r$\nChoose Yes to delete, No to keep user data, or Cancel to stop uninstalling." /SD IDCANCEL IDYES consent_yes IDNO consent_no
  SetErrorLevel 1
  Abort
  consent_yes:
    StrCpy $PurgeUserData 1
    Goto consent_done
  consent_no:
    StrCpy $PurgeUserData 0
  consent_done:
  Return
  invalid:
    MessageBox MB_OK|MB_ICONSTOP "PrivacyBrowser installation marker is missing or invalid. No files have been removed." /SD IDOK
    SetErrorLevel 1
    Abort
!endif
FunctionEnd

; Return an exclusive, no-follow DELETE/GENERIC_WRITE handle to a normal file.
; This is also the profile-lock check: locks anywhere in either tree are checked.
Function un.OpenSafeFile
  Exch $0
  Push $1
  Push $2
  Push $3
  StrCpy $NativeAccess 0xC0010080
  StrCpy $NativeShare 0
  StrCpy $NativeDisposition 1
  StrCpy $NativeOptions 0x40
  Push $0
  Call un.OpenNoReparse
  Pop $1
  ${If} $1 == -1
    Call un.DataRefusal
  ${EndIf}
  System::Alloc 52
  Pop $2
  ${If} $2 == 0
    Call un.DataRefusal
  ${EndIf}
  System::Call 'kernel32::GetFileInformationByHandle(p r1, p r2) i.r3'
  ${If} $3 == 0
    Call un.DataRefusal
  ${EndIf}
  System::Call '*$2(i.r3)'
  System::Free $2
  ; Reject readonly, directory and every reparse-point file before mutation.
  IntOp $2 $3 & 0x411
  ${If} $2 != 0
    Call un.DataRefusal
  ${EndIf}
  StrCpy $0 $1
  Pop $3
  Pop $2
  Pop $1
  Exch $0
FunctionEnd

; Set disposition on the exact opened object; no path is followed during delete.
Function un.DeleteOpenedObject
  Exch $0
  Push $1
  Push $2
  System::Call '*(i 1) p.r1'
  ${If} $1 == 0
    Call un.DataRefusal
  ${EndIf}
  System::Call 'kernel32::SetFileInformationByHandle(p r0, i 4, p r1, i 1) i.r2 ?e'
  Pop $NativeError
  System::Free $1
  StrCpy $0 $2
  Pop $2
  Pop $1
  Exch $0
FunctionEnd

; Three passes: 0 preflight all descendants, 1 delete ordinary files, 2 remove
; empty descendant directories. Root markers survive all three passes. Existing
; parent.lock files stay exclusively held from preflight until ordinary files
; are gone, blocking other binaries from opening those profiles during purge.
Function un.WalkOwnedTree
  Exch $0
  Push $1
  Push $2
  Push $3
  Push $4
  Push $5
  Push $6
  Push $7
  Push $8
  Push $9
  System::Call 'kernel32::GetFileAttributesW(w r0) i.r1 ?e'
  Pop $2
  ${If} $1 == -1
    ${If} $2 == 2
    ${OrIf} $2 == 3
      Goto walk_done
    ${EndIf}
    Call un.DataRefusal
  ${EndIf}
  Push $0
  Call un.OpenSafeDirectory
  Pop $7
  ; Extra WCHAR padding makes fixed-width System copies safe for a short last
  ; record; explicit filename length still bounds the actual string used.
  System::Alloc 66056
  Pop $6
  ${If} $6 == 0
    Call un.DataRefusal
  ${EndIf}
  ; FILE_FULL_DIR_INFO: attributes offset56, name length60, WCHAR name68.
  ; Enumerate the held directory object, never a path that could become a link.
  System::Call 'kernel32::GetFileInformationByHandleEx(p r7, i 15, p r6, i 65536) i.r5 ?e'
  Pop $9
  ${If} $5 == 0
    ${If} $9 == 18
      Goto walk_close
    ${EndIf}
    Call un.DataRefusal
  ${EndIf}
  StrCpy $1 $6
  walk_entry:
    System::Call '*$1(i.r9, &v52, i.r4, i.r8)'
    ${If} $8 > 510
    ${OrIf} $8 == 0
      Call un.DataRefusal
    ${EndIf}
    IntOp $5 $1 - $6
    IntOp $5 $5 + 68
    IntOp $5 $5 + $8
    ${If} $5 > 65536
      Call un.DataRefusal
    ${EndIf}
    IntOp $5 $8 & 1
    ${If} $5 != 0
      Call un.DataRefusal
    ${EndIf}
    IntOp $5 $1 + 68
    System::Call '*$5(&w260.r2)'
    IntOp $8 $8 / 2
    StrCpy $2 $2 $8
    ${If} $2 == "."
    ${OrIf} $2 == ".."
      Goto walk_next
    ${EndIf}
    StrLen $3 $0
    StrLen $5 $2
    IntOp $3 $3 + $5
    ; Win32/NSIS path truncation is a refusal, never a shortened deletion path.
    ${If} $3 > 240
      Call un.DataRefusal
    ${EndIf}
    StrCpy $3 "$0\$2"
    IntOp $5 $4 & 0x400
    ${If} $5 != 0
      Call un.DataRefusal
    ${EndIf}
    IntOp $5 $4 & 0x10
    ${If} $5 != 0
      Push $3
      Call un.WalkOwnedTree
      ${If} $TreeMode == 2
        Push $3
        Call un.RemoveEmptyDirectory
      ${EndIf}
    ${Else}
      ${If} $3 == "$LocalDataRoot\${DATA_MARKER}"
      ${OrIf} $3 == "$RoamingDataRoot\${DATA_MARKER}"
        ${If} $TreeMode == 0
          Push $3
          Call un.OpenSafeFile
          Pop $8
          System::Call 'kernel32::CloseHandle(p r8)'
        ${EndIf}
        Goto walk_next
      ${EndIf}
      ${If} $TreeMode == 2
        ; New/remaining files mean another writer or an incomplete deletion.
        Call un.DataRefusal
      ${EndIf}
      ${If} $TreeMode == 1
      ${AndIf} $2 == "parent.lock"
        Goto walk_next
      ${EndIf}
      Push $3
      Call un.OpenSafeFile
      Pop $8
      ${If} $TreeMode == 0
      ${AndIf} $2 == "parent.lock"
        System::Alloc 16
        Pop $5
        ${If} $5 == 0
          Call un.DataRefusal
        ${EndIf}
        System::Call '*$5(p r8, p $ProfileLocks)'
        StrCpy $ProfileLocks $5
        Goto walk_next
      ${EndIf}
      ${If} $TreeMode == 1
        Push $8
        Call un.DeleteOpenedObject
        Pop $5
        ${If} $5 == 0
          Call un.DataRefusal
        ${EndIf}
      ${EndIf}
      System::Call 'kernel32::CloseHandle(p r8)'
    ${EndIf}
  walk_next:
    ${If} $9 != 0
      IntOp $1 $1 + $9
      IntOp $5 $1 - $6
      ${If} $5 < 0
      ${OrIf} $5 > 65466
        Call un.DataRefusal
      ${EndIf}
      Goto walk_entry
    ${EndIf}
    System::Call 'kernel32::GetFileInformationByHandleEx(p r7, i 14, p r6, i 65536) i.r5 ?e'
    Pop $9
    ${If} $5 != 0
      StrCpy $1 $6
      Goto walk_entry
    ${EndIf}
    ${If} $9 != 18
      Call un.DataRefusal
    ${EndIf}
  walk_close:
  System::Free $6
  System::Call 'kernel32::CloseHandle(p r7)'
  walk_done:
  Pop $9
  Pop $8
  Pop $7
  Pop $6
  Pop $5
  Pop $4
  Pop $3
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

Function un.DeleteHeldProfileLocks
  ${DoWhile} $ProfileLocks != 0
    System::Call '*$ProfileLocks(p.r0, p.r1)'
    Push $0
    Call un.DeleteOpenedObject
    Pop $2
    ${If} $2 == 0
      Call un.DataRefusal
    ${EndIf}
    System::Call 'kernel32::CloseHandle(p r0)'
    System::Free $ProfileLocks
    StrCpy $ProfileLocks $1
  ${Loop}
FunctionEnd

Function un.RemoveEmptyDirectory
  Exch $0
  Push $1
  Push $2
  Push $3
  StrCpy $NativeAccess 0x10081
  StrCpy $NativeShare 3
  StrCpy $NativeDisposition 1
  StrCpy $NativeOptions 1
  Push $0
  Call un.OpenNoReparse
  Pop $1
  ${If} $1 == -1
    ${If} $NativeError == 2
    ${OrIf} $NativeError == 3
      Goto empty_directory_done
    ${EndIf}
    Call un.DataRefusal
  ${EndIf}
  System::Alloc 52
  Pop $2
  ${If} $2 == 0
    Call un.DataRefusal
  ${EndIf}
  System::Call 'kernel32::GetFileInformationByHandle(p r1, p r2) i.r3'
  ${If} $3 == 0
    Call un.DataRefusal
  ${EndIf}
  System::Call '*$2(i.r3)'
  System::Free $2
  IntOp $2 $3 & 0x400
  ${If} $2 != 0
    Call un.DataRefusal
  ${EndIf}
  Push $1
  Call un.DeleteOpenedObject
  Pop $2
  StrCpy $3 $NativeError
  System::Call 'kernel32::CloseHandle(p r1)'
  ${If} $2 == 0
    ${If} $OptionalDirectoryRemoval == 1
    ${AndIf} $3 == 145
      Goto empty_directory_done
    ${EndIf}
    Call un.DataRefusal
  ${EndIf}
  empty_directory_done:
  Pop $3
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

Function un.RemovePayloadDirectory
  StrCpy $OptionalDirectoryRemoval 1
  Call un.RemoveEmptyDirectory
  StrCpy $OptionalDirectoryRemoval 0
FunctionEnd

Function un.FinalizeOwnedRoot
  Exch $0
  Push $1
  Push $2
  Push $3
  Push $0
  Call un.VerifyOwnedRoot
  IfFileExists "$0\${DATA_MARKER}" 0 finalize_done
  ; DELETE access on the same protected directory handle avoids close/RMDir
  ; substitution and lets us restore the marker on the identical failed root.
  StrCpy $1 $LocalRootLock
  ${If} $0 == $RoamingDataRoot
    StrCpy $1 $RoamingRootLock
  ${EndIf}
  ${If} $1 == 0
    Call un.DataRefusal
  ${EndIf}
  Push "$0\${DATA_MARKER}"
  Call un.OpenSafeFile
  Pop $2
  Push $2
  Call un.DeleteOpenedObject
  Pop $3
  System::Call 'kernel32::CloseHandle(p r2)'
  ${If} $3 == 0
    Call un.DataRefusal
  ${EndIf}
  Push $1
  Call un.DeleteOpenedObject
  Pop $3
  ${If} $3 == 0
    Push $0
    Call un.WriteMarkerBytes
    Call un.DataRefusal
  ${EndIf}
  System::Call 'kernel32::CloseHandle(p r1)'
  ${If} $0 == $LocalDataRoot
    StrCpy $LocalRootLock 0
  ${Else}
    StrCpy $RoamingRootLock 0
  ${EndIf}
  finalize_done:
  Pop $3
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

Function un.PurgeOwnedData
  Push $LocalDataRoot
  Call un.VerifyOwnedRoot
  Push $RoamingDataRoot
  Call un.VerifyOwnedRoot
  ; Hold both root objects until marker-last deletion. They cannot be replaced
  ; between full-tree preflight and any later removal pass.
  StrCpy $DirectoryAccess 0x10081
  IfFileExists "$LocalDataRoot\${DATA_MARKER}" 0 +4
    Push $LocalDataRoot
    Call un.OpenSafeDirectory
    Pop $LocalRootLock
  IfFileExists "$RoamingDataRoot\${DATA_MARKER}" 0 +4
    Push $RoamingDataRoot
    Call un.OpenSafeDirectory
    Pop $RoamingRootLock
  StrCpy $DirectoryAccess 0x81
  Push $LocalDataRoot
  Call un.VerifyOwnedRoot
  Push $RoamingDataRoot
  Call un.VerifyOwnedRoot
  StrCpy $TreeMode 0
  Push $LocalDataRoot
  Call un.WalkOwnedTree
  Push $RoamingDataRoot
  Call un.WalkOwnedTree
  ; Both complete trees passed before any user-data deletion begins.
  StrCpy $TreeMode 1
  Push $LocalDataRoot
  Call un.VerifyOwnedRoot
  Push $RoamingDataRoot
  Call un.VerifyOwnedRoot
  Push $LocalDataRoot
  Call un.WalkOwnedTree
  Push $RoamingDataRoot
  Call un.WalkOwnedTree
  Call un.DeleteHeldProfileLocks
  StrCpy $TreeMode 2
  Push $LocalDataRoot
  Call un.WalkOwnedTree
  Push $RoamingDataRoot
  Call un.WalkOwnedTree
  Push $LocalDataRoot
  Call un.FinalizeOwnedRoot
  Push $RoamingDataRoot
  Call un.FinalizeOwnedRoot
FunctionEnd

Function un.CheckBrowserClosed
  ; GENERIC_WRITE, share mode 0, OPEN_EXISTING. A running image cannot be
  ; opened this way, regardless of its chosen/default profile. Never kill it.
  ; Any failure (including access denied or a missing EXE) fails closed.
  IfFileExists "$INSTDIR\privacybrowser.exe" browser_present
  Call un.VerifyCleanupIntent
  !insertmacro VerifyNativePayloadAbsent
  Return
  browser_present:
  System::Call 'kernel32::CreateFileW(w "$INSTDIR\privacybrowser.exe", i 0x40000000, i 0, p 0, i 3, i 0x80, p 0) p.r0'
  ${If} $0 == -1
    MessageBox MB_OK|MB_ICONSTOP "PrivacyBrowser cannot be safely removed. Close every instance and check access to the installed application, then retry. No files have been removed." /SD IDOK
    SetErrorLevel 1
    Abort
  ${EndIf}
  System::Call 'kernel32::CloseHandle(p r0) i.r1'
  ${If} $1 == 0
    MessageBox MB_OK|MB_ICONSTOP "The application access check failed. No files have been removed." /SD IDOK
    SetErrorLevel 1
    Abort
  ${EndIf}
FunctionEnd

Function un.LockPayloadDirectory
  Exch $0
  Push $1
  Push $2
  System::Call 'kernel32::GetFileAttributesW(w r0) i.r1 ?e'
  Pop $2
  ${If} $1 == -1
    ${If} $2 == 2
    ${OrIf} $2 == 3
      Goto payload_directory_done
    ${EndIf}
    Call un.DataRefusal
  ${EndIf}
  Push $0
  Call un.OpenSafeDirectory
  Pop $1
  System::Alloc 16
  Pop $2
  ${If} $2 == 0
    Call un.DataRefusal
  ${EndIf}
  System::Call '*$2(p r1, p $PayloadLocks)'
  StrCpy $PayloadLocks $2
  payload_directory_done:
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

Function un.ValidatePayloadFile
  Exch $0
  Push $1
  Push $2
  System::Call 'kernel32::GetFileAttributesW(w r0) i.r1 ?e'
  Pop $2
  ${If} $1 == -1
    ${If} $2 == 2
    ${OrIf} $2 == 3
      Goto payload_file_done
    ${EndIf}
    Call un.DataRefusal
  ${EndIf}
  Push $0
  Call un.OpenSafeFile
  Pop $1
  System::Call 'kernel32::CloseHandle(p r1)'
  payload_file_done:
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

Function un.DeletePayloadFile
  Exch $0
  Push $1
  Push $2
  IfFileExists "$0" 0 delete_payload_done
  Push $0
  Call un.OpenSafeFile
  Pop $1
  Push $1
  Call un.DeleteOpenedObject
  Pop $2
  System::Call 'kernel32::CloseHandle(p r1)'
  ${If} $2 == 0
    StrCpy $R9 1
  ${EndIf}
  delete_payload_done:
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

Function un.ClosePayloadDirectoryLocks
  ${DoWhile} $PayloadLocks != 0
    System::Call '*$PayloadLocks(p.r0, p.r1)'
    System::Call 'kernel32::CloseHandle(p r0)'
    System::Free $PayloadLocks
    StrCpy $PayloadLocks $1
  ${Loop}
FunctionEnd

; $0 is the current link and $1 its immutable install-time byte snapshot.
; Both files are opened exclusively/no-follow; deletion uses that same handle.
Function un.RemoveOwnedShortcut
  Push $2
  Push $3
  Push $4
  Push $5
  Push $6
  Push $7
  Push $8
  Push $9
  System::Call 'kernel32::GetFileAttributesW(w r0) i.r2 ?e'
  Pop $3
  ${If} $2 == -1
    ${If} $3 == 2
    ${OrIf} $3 == 3
      Goto shortcut_done
    ${EndIf}
    Call un.DataRefusal
  ${EndIf}
  Push $0
  Call un.OpenSafeFile
  Pop $2
  Push $1
  Call un.OpenSafeFile
  Pop $3
  System::Alloc 4096
  Pop $4
  System::Alloc 4096
  Pop $5
  ${If} $4 == 0
  ${OrIf} $5 == 0
    Call un.DataRefusal
  ${EndIf}
  shortcut_read:
    System::Call 'kernel32::ReadFile(p r2, p r4, i 4096, *i.r6, p 0) i.r8'
    System::Call 'kernel32::ReadFile(p r3, p r5, i 4096, *i.r7, p 0) i.r9'
    ${If} $8 == 0
    ${OrIf} $9 == 0
    ${OrIf} $6 != $7
      Call un.DataRefusal
    ${EndIf}
    ${If} $6 == 0
      Goto shortcut_equal
    ${EndIf}
    System::Call 'msvcrt::memcmp(p r4, p r5, i r6) i.r8 ?c'
    ${If} $8 != 0
      Call un.DataRefusal
    ${EndIf}
    Goto shortcut_read
  shortcut_equal:
  System::Free $4
  System::Free $5
  System::Call 'kernel32::CloseHandle(p r3)'
  ${If} $ShortcutDelete == 0
    System::Call 'kernel32::CloseHandle(p r2)'
    Goto shortcut_done
  ${EndIf}
  Push $2
  Call un.DeleteOpenedObject
  Pop $8
  System::Call 'kernel32::CloseHandle(p r2)'
  ${If} $8 == 0
    Call un.DataRefusal
  ${EndIf}
  shortcut_done:
  Pop $9
  Pop $8
  Pop $7
  Pop $6
  Pop $5
  Pop $4
  Pop $3
  Pop $2
FunctionEnd

; $0 exact product key, $1 exact install-bound value. Missing is success;
; access errors and present values are failures, regardless of registry type.
Function un.RequireRegistryValueAbsent
  Push $2
  Push $3
  System::Call 'advapi32::RegOpenKeyExW(p 0x80000001, w r0, i 0, i 0x20119, *p.r2) i.r3'
  ${If} $3 == 2
    Goto reg_value_done
  ${EndIf}
  ${If} $3 != 0
    Call un.DataRefusal
  ${EndIf}
  System::Call 'advapi32::RegQueryValueExW(p r2, w r1, p 0, p 0, p 0, p 0) i.r3'
  System::Call 'advapi32::RegCloseKey(p r2)'
  ${If} $3 != 2
    Call un.DataRefusal
  ${EndIf}
  reg_value_done:
  Pop $3
  Pop $2
FunctionEnd

Function un.RequireRegistryAbsentOrNonempty
  Push $1
  Push $2
  Push $3
  Push $4
  System::Call 'advapi32::RegOpenKeyExW(p 0x80000001, w r0, i 0, i 0x20119, *p.r1) i.r2'
  ${If} $2 == 2
    Goto reg_key_done
  ${EndIf}
  ${If} $2 != 0
    Call un.DataRefusal
  ${EndIf}
  System::Call 'advapi32::RegQueryInfoKeyW(p r1, p 0, p 0, p 0, *i.r3, p 0, p 0, *i.r4, p 0, p 0, p 0, p 0) i.r2'
  System::Call 'advapi32::RegCloseKey(p r1)'
  ${If} $2 != 0
    Call un.DataRefusal
  ${EndIf}
  ${If} $3 == 0
  ${AndIf} $4 == 0
    Call un.DataRefusal
  ${EndIf}
  reg_key_done:
  Pop $4
  Pop $3
  Pop $2
  Pop $1
FunctionEnd

!macro VerifyRemovedRuntimeValue KEY SUFFIX
  StrCpy $0 "${KEY}"
  StrCpy $1 "$INSTDIR\privacybrowser.exe|${SUFFIX}"
  Call un.RequireRegistryValueAbsent
!macroend

Function un.RemoveOwnedRuntimeRegistry
  ; Delete exact install-bound value names, never a path stored in a value.
  !define LAUNCHER_KEY "SOFTWARE\PrivacyBrowser\PrivacyBrowser\Launcher"
  !define SKELETON_KEY "SOFTWARE\PrivacyBrowser\PrivacyBrowser\PreXULSkeletonUISettings"
  DeleteRegValue HKCU "${LAUNCHER_KEY}" "$INSTDIR\privacybrowser.exe|Launcher"
  DeleteRegValue HKCU "${LAUNCHER_KEY}" "$INSTDIR\privacybrowser.exe|Browser"
  DeleteRegValue HKCU "${LAUNCHER_KEY}" "$INSTDIR\privacybrowser.exe|Image"
  DeleteRegValue HKCU "${LAUNCHER_KEY}" "$INSTDIR\privacybrowser.exe|Telemetry"
  DeleteRegValue HKCU "${LAUNCHER_KEY}" "$INSTDIR\privacybrowser.exe|Blocklist"
  DeleteRegValue HKCU "${LAUNCHER_KEY}" "$INSTDIR\privacybrowser.exe|LauncherCrashTime"
  DeleteRegValue HKCU "${SKELETON_KEY}" "$INSTDIR\privacybrowser.exe|Enabled"
  DeleteRegValue HKCU "${SKELETON_KEY}" "$INSTDIR\privacybrowser.exe|ScreenX"
  DeleteRegValue HKCU "${SKELETON_KEY}" "$INSTDIR\privacybrowser.exe|ScreenY"
  DeleteRegValue HKCU "${SKELETON_KEY}" "$INSTDIR\privacybrowser.exe|Width"
  DeleteRegValue HKCU "${SKELETON_KEY}" "$INSTDIR\privacybrowser.exe|Height"
  DeleteRegValue HKCU "${SKELETON_KEY}" "$INSTDIR\privacybrowser.exe|Maximized"
  DeleteRegValue HKCU "${SKELETON_KEY}" "$INSTDIR\privacybrowser.exe|UrlbarCSSSpan"
  DeleteRegValue HKCU "${SKELETON_KEY}" "$INSTDIR\privacybrowser.exe|CssToDevPixelScaling"
  DeleteRegValue HKCU "${SKELETON_KEY}" "$INSTDIR\privacybrowser.exe|SearchbarCSSSpan"
  DeleteRegValue HKCU "${SKELETON_KEY}" "$INSTDIR\privacybrowser.exe|SpringsCSSSpan"
  DeleteRegValue HKCU "${SKELETON_KEY}" "$INSTDIR\privacybrowser.exe|Theme"
  DeleteRegValue HKCU "${SKELETON_KEY}" "$INSTDIR\privacybrowser.exe|Flags"
  DeleteRegValue HKCU "${SKELETON_KEY}" "$INSTDIR\privacybrowser.exe|Progress"
  !insertmacro VerifyRemovedRuntimeValue "${LAUNCHER_KEY}" "Launcher"
  !insertmacro VerifyRemovedRuntimeValue "${LAUNCHER_KEY}" "Browser"
  !insertmacro VerifyRemovedRuntimeValue "${LAUNCHER_KEY}" "Image"
  !insertmacro VerifyRemovedRuntimeValue "${LAUNCHER_KEY}" "Telemetry"
  !insertmacro VerifyRemovedRuntimeValue "${LAUNCHER_KEY}" "Blocklist"
  !insertmacro VerifyRemovedRuntimeValue "${LAUNCHER_KEY}" "LauncherCrashTime"
  !insertmacro VerifyRemovedRuntimeValue "${SKELETON_KEY}" "Enabled"
  !insertmacro VerifyRemovedRuntimeValue "${SKELETON_KEY}" "ScreenX"
  !insertmacro VerifyRemovedRuntimeValue "${SKELETON_KEY}" "ScreenY"
  !insertmacro VerifyRemovedRuntimeValue "${SKELETON_KEY}" "Width"
  !insertmacro VerifyRemovedRuntimeValue "${SKELETON_KEY}" "Height"
  !insertmacro VerifyRemovedRuntimeValue "${SKELETON_KEY}" "Maximized"
  !insertmacro VerifyRemovedRuntimeValue "${SKELETON_KEY}" "UrlbarCSSSpan"
  !insertmacro VerifyRemovedRuntimeValue "${SKELETON_KEY}" "CssToDevPixelScaling"
  !insertmacro VerifyRemovedRuntimeValue "${SKELETON_KEY}" "SearchbarCSSSpan"
  !insertmacro VerifyRemovedRuntimeValue "${SKELETON_KEY}" "SpringsCSSSpan"
  !insertmacro VerifyRemovedRuntimeValue "${SKELETON_KEY}" "Theme"
  !insertmacro VerifyRemovedRuntimeValue "${SKELETON_KEY}" "Flags"
  !insertmacro VerifyRemovedRuntimeValue "${SKELETON_KEY}" "Progress"
  DeleteRegKey /ifempty HKCU "${LAUNCHER_KEY}"
  DeleteRegKey /ifempty HKCU "${SKELETON_KEY}"
  DeleteRegKey /ifempty HKCU "SOFTWARE\PrivacyBrowser\PrivacyBrowser"
  DeleteRegKey /ifempty HKCU "SOFTWARE\PrivacyBrowser"
  StrCpy $0 "${LAUNCHER_KEY}"
  Call un.RequireRegistryAbsentOrNonempty
  StrCpy $0 "${SKELETON_KEY}"
  Call un.RequireRegistryAbsentOrNonempty
  StrCpy $0 "SOFTWARE\PrivacyBrowser\PrivacyBrowser"
  Call un.RequireRegistryAbsentOrNonempty
  StrCpy $0 "SOFTWARE\PrivacyBrowser"
  Call un.RequireRegistryAbsentOrNonempty
FunctionEnd

; Idempotent metadata file removal; caller's application directory is pinned.
Function un.DeleteMetadataFile
  Exch $0
  Push $1
  Push $2
  IfFileExists "$0" 0 metadata_file_done
  Push $0
  Call un.OpenSafeFile
  Pop $1
  Push $1
  Call un.DeleteOpenedObject
  Pop $2
  System::Call 'kernel32::CloseHandle(p r1)'
  ${If} $2 == 0
    Call un.DataRefusal
  ${EndIf}
  metadata_file_done:
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

; Restore only missing fixed recovery markers while the same app dir is pinned.
Function un.RestoreRecoveryMarkers
  IfFileExists "$INSTDIR\PrivacyBrowser.install-marker" app_marker_present
  StrCpy $0 "$INSTDIR\PrivacyBrowser.install-marker"
  StrCpy $1 "PrivacyBrowser 0.1.0-dev"
  Call un.WriteNewAsciiFile
  app_marker_present:
  Call un.EnsureCleanupIntent
FunctionEnd

Function un.FinalizeMetadata
  ; Snapshots may be absent on retries after both owned shortcuts were removed.
  Push "$INSTDIR\PrivacyBrowser.desktop-link"
  Call un.DeleteMetadataFile
  Push "$INSTDIR\PrivacyBrowser.program-link"
  Call un.DeleteMetadataFile
  Call un.VerifyRegistration
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PrivacyBrowser"
  System::Call 'advapi32::RegOpenKeyExW(p 0x80000001, w "Software\Microsoft\Windows\CurrentVersion\Uninstall\PrivacyBrowser", i 0, i 0x20119, *p.r0) i.r1'
  ${If} $1 == 0
    System::Call 'advapi32::RegCloseKey(p r0)'
    Call un.DataRefusal
  ${ElseIf} $1 != 2
    Call un.DataRefusal
  ${EndIf}
  ; Acquire all remaining file handles before removing either recovery marker.
  Push "$INSTDIR\uninstall.exe"
  Call un.OpenSafeFile
  Pop $6
  Push "$INSTDIR\PrivacyBrowser.install-marker"
  Call un.OpenSafeFile
  Pop $7
  Push "$INSTDIR\PrivacyBrowser.cleanup-intent"
  Call un.OpenSafeFile
  Pop $8
  Push $7
  Call un.DeleteOpenedObject
  Pop $9
  System::Call 'kernel32::CloseHandle(p r7)'
  ${If} $9 == 0
    System::Call 'kernel32::CloseHandle(p r8)'
    Call un.RestoreRecoveryMarkers
    Call un.DataRefusal
  ${EndIf}
  Push $8
  Call un.DeleteOpenedObject
  Pop $9
  System::Call 'kernel32::CloseHandle(p r8)'
  ${If} $9 == 0
    Call un.RestoreRecoveryMarkers
    Call un.DataRefusal
  ${EndIf}
  Push $6
  Call un.DeleteOpenedObject
  Pop $9
  System::Call 'kernel32::CloseHandle(p r6)'
  ${If} $9 == 0
    Call un.RestoreRecoveryMarkers
    Call un.DataRefusal
  ${EndIf}
FunctionEnd

!ifndef PB_SYNTHETIC_FIXTURE
Section "Uninstall"
  SetShellVarContext current
  SetRegView 64
  ; Check again after any user interaction in the confirmation page.
  Call un.CheckBrowserClosed
  Call un.VerifyRegistration
  Call un.LockShortcutParents
  !insertmacro ValidateNativePayload
  Push "$INSTDIR\PrivacyBrowser.install-marker"
  Call un.ValidatePayloadFile
  Push "$INSTDIR\uninstall.exe"
  Call un.ValidatePayloadFile
  StrCpy $ShortcutDelete 0
  StrCpy $0 "$DESKTOP\PrivacyBrowser.lnk"
  StrCpy $1 "$INSTDIR\PrivacyBrowser.desktop-link"
  Call un.RemoveOwnedShortcut
  StrCpy $0 "$SMPROGRAMS\PrivacyBrowser\PrivacyBrowser.lnk"
  StrCpy $1 "$INSTDIR\PrivacyBrowser.program-link"
  Call un.RemoveOwnedShortcut
  ${If} $PurgeUserData == 1
    Call un.PurgeOwnedData
  ${EndIf}
  StrCpy $ShortcutDelete 1
  StrCpy $0 "$DESKTOP\PrivacyBrowser.lnk"
  StrCpy $1 "$INSTDIR\PrivacyBrowser.desktop-link"
  Call un.RemoveOwnedShortcut
  StrCpy $0 "$SMPROGRAMS\PrivacyBrowser\PrivacyBrowser.lnk"
  StrCpy $1 "$INSTDIR\PrivacyBrowser.program-link"
  Call un.RemoveOwnedShortcut
  System::Call 'kernel32::CloseHandle(p $ProgramDirectoryLock)'
  Push "$SMPROGRAMS\PrivacyBrowser"
  Call un.RemovePayloadDirectory
  Call un.RemoveOwnedRuntimeRegistry
  ; Intent is durable before the EXE-last step, closing its crash/retry gap.
  Call un.EnsureCleanupIntent
  !insertmacro RemoveNativePayload
  ${If} $R9 != 0
    MessageBox MB_OK|MB_ICONSTOP "Some packaged application files could not be removed. Close applications using these files and retry. The uninstall marker and registration have been retained." /SD IDOK
    SetErrorLevel 1
    Abort
  ${EndIf}
  Call un.FinalizeMetadata
  Push $InstallDirectoryLock
  Call un.DeleteOpenedObject
  Pop $0
  StrCpy $1 $NativeError
  System::Call 'kernel32::CloseHandle(p $InstallDirectoryLock)'
  ${If} $0 == 0
  ${AndIf} $1 != 145
    MessageBox MB_OK|MB_ICONSTOP "The application and its uninstall metadata were removed, but the final installation directory could not be removed. Check the remaining directory manually." /SD IDOK
    SetErrorLevel 1
    Abort
  ${EndIf}
  ; Unknown application files and downloads outside the owned roots remain.
SectionEnd
!else
Section "Uninstall"
SectionEnd
!endif
