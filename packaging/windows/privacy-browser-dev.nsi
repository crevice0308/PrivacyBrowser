; Local development setup. Native Gecko is not independently source-built.
; The setup remains unsigned and is not approved for public product distribution.
Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "x64.nsh"
!include "WinVer.nsh"
!ifndef RUNTIME_DIR
  !error "Set RUNTIME_DIR to a validated AMD64 developer runtime"
!endif
!ifndef PAYLOAD_INCLUDE
  !error "Generate an exact payload include with tools/package_windows.py"
!endif
!ifndef OUTPUT_FILE
  !error "Set OUTPUT_FILE"
!endif
!include "${PAYLOAD_INCLUDE}"
Name "Privacy Browser Developer"
OutFile "${OUTPUT_FILE}"
InstallDir "$LOCALAPPDATA\Programs\PrivacyBrowser-Dev"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 32
ShowInstDetails show
ShowUninstDetails show
BrandingText "Privacy Browser 0.1.0-dev"
VIProductVersion "0.1.0.0"
VIAddVersionKey /LANG=1033 "ProductName" "Privacy Browser Developer Setup"
VIAddVersionKey /LANG=1033 "FileDescription" "Privacy Browser Developer Installer"
VIAddVersionKey /LANG=1033 "CompanyName" "Privacy Browser contributors"
VIAddVersionKey /LANG=1033 "FileVersion" "0.1.0-dev"
VIAddVersionKey /LANG=1033 "LegalCopyright" "MPL-2.0; upstream licenses retained"
!define MUI_ICON "${RUNTIME_DIR}/distribution/privacybrowser/assets/privacybrowser.ico"
!define MUI_UNICON "${RUNTIME_DIR}/distribution/privacybrowser/assets/privacybrowser.ico"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "Privacy Browser Developer Setup"
!define MUI_WELCOMEPAGE_TEXT "Install Privacy Browser 0.1.0-dev for this Windows account.$\r$\n$\r$\nIncludes a verified Firefox ESR runtime and experimental Privacy Browser features. Native Firefox identity is retained for local development.$\r$\n$\r$\nThis setup is unsigned. Windows runtime behavior and complete history non-persistence are still being validated."
!define MUI_FINISHPAGE_RUN
!define MUI_FINISHPAGE_RUN_FUNCTION LaunchPrivacyBrowser
!define MUI_FINISHPAGE_RUN_TEXT "Open Privacy Browser Developer"
!define MUI_FINISHPAGE_RUN_NOTCHECKED
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "${RUNTIME_DIR}/PRIVACY_BROWSER_NOTICES.txt"
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"
!insertmacro MUI_LANGUAGE "Japanese"

Function .onInit
  SetShellVarContext current
  SetRegView 64
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "Windows 11 x64 is required."
    Abort
  ${EndIf}
  ${IfNot} ${AtLeastWin10}
    MessageBox MB_ICONSTOP "Windows 11 x64 is required."
    Abort
  ${EndIf}
  ReadRegStr $0 HKLM "SOFTWARE\Microsoft\Windows NT\CurrentVersion" "CurrentBuildNumber"
  ${If} $0 < 22000
    MessageBox MB_ICONSTOP "Windows 11 build 22000 or newer is required."
    Abort
  ${EndIf}
  IfFileExists "$INSTDIR\*.*" 0 ready
  IfFileExists "$INSTDIR\PrivacyBrowser-Dev.install-marker" ready 0
    MessageBox MB_ICONSTOP "The installation folder already contains other files. No files have been changed."
    Abort
  ready:
  Call CheckBrowserClosed
FunctionEnd

Function CheckBrowserClosed
  System::Call 'kernel32::CreateFileW(w "$LOCALAPPDATA\PrivacyBrowser-Dev\Profile\parent.lock", i 0x80000000, i 0, p 0, i 3, i 0x80, p 0) p.r0 ?e'
  Pop $1
  ${If} $0 == -1
    ${If} $1 == 32
    ${OrIf} $1 == 33
      MessageBox MB_ICONSTOP "Close Privacy Browser Developer before installing."
      Abort
    ${EndIf}
  ${Else}
    System::Call 'kernel32::CloseHandle(p r0)'
  ${EndIf}
FunctionEnd

Function LaunchPrivacyBrowser
  CreateDirectory "$LOCALAPPDATA\PrivacyBrowser-Dev\Profile"
  ExecShell "open" "$INSTDIR\firefox.exe" '-no-remote -profile "$LOCALAPPDATA\PrivacyBrowser-Dev\Profile"'
FunctionEnd

Section "Privacy Browser Developer"
  SetShellVarContext current
  SetRegView 64
  SetOverwrite on
  !insertmacro InstallPayload
  SetOutPath "$INSTDIR"
  FileOpen $0 "$INSTDIR\PrivacyBrowser-Dev.install-marker" w
  FileWrite $0 "Privacy Browser Developer 0.1.0-dev"
  FileClose $0
  WriteUninstaller "$INSTDIR\uninstall.exe"
  CreateDirectory "$LOCALAPPDATA\PrivacyBrowser-Dev\Profile"
  CreateDirectory "$SMPROGRAMS\Privacy Browser Developer"
  CreateShortcut "$SMPROGRAMS\Privacy Browser Developer\Privacy Browser.lnk" "$INSTDIR\firefox.exe" '-no-remote -profile "$LOCALAPPDATA\PrivacyBrowser-Dev\Profile"' "$INSTDIR\distribution\privacybrowser\assets\privacybrowser.ico"
  CreateShortcut "$DESKTOP\Privacy Browser Developer.lnk" "$INSTDIR\firefox.exe" '-no-remote -profile "$LOCALAPPDATA\PrivacyBrowser-Dev\Profile"' "$INSTDIR\distribution\privacybrowser\assets\privacybrowser.ico"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PrivacyBrowser-Dev" "DisplayName" "Privacy Browser Developer"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PrivacyBrowser-Dev" "DisplayVersion" "0.1.0-dev"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PrivacyBrowser-Dev" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PrivacyBrowser-Dev" "DisplayIcon" "$INSTDIR\distribution\privacybrowser\assets\privacybrowser.ico"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PrivacyBrowser-Dev" "UninstallString" '$\"$INSTDIR\uninstall.exe$\"'
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PrivacyBrowser-Dev" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PrivacyBrowser-Dev" "NoRepair" 1
SectionEnd

Function un.onInit
  SetShellVarContext current
  SetRegView 64
  IfFileExists "$INSTDIR\PrivacyBrowser-Dev.install-marker" 0 invalid
  System::Call 'kernel32::CreateFileW(w "$LOCALAPPDATA\PrivacyBrowser-Dev\Profile\parent.lock", i 0x80000000, i 0, p 0, i 3, i 0x80, p 0) p.r0 ?e'
  Pop $1
  ${If} $0 == -1
    ${If} $1 == 32
    ${OrIf} $1 == 33
      MessageBox MB_ICONSTOP "Close Privacy Browser Developer before uninstalling."
      Abort
    ${EndIf}
  ${Else}
    System::Call 'kernel32::CloseHandle(p r0)'
  ${EndIf}
  Return
  invalid:
    MessageBox MB_ICONSTOP "Privacy Browser installation marker is missing. No files have been removed."
    Abort
FunctionEnd

Section "Uninstall"
  SetShellVarContext current
  SetRegView 64
  !insertmacro RemovePayload
  Delete "$INSTDIR\PrivacyBrowser-Dev.install-marker"
  Delete "$INSTDIR\uninstall.exe"
  RMDir "$INSTDIR"
  Delete "$DESKTOP\Privacy Browser Developer.lnk"
  Delete "$SMPROGRAMS\Privacy Browser Developer\Privacy Browser.lnk"
  RMDir "$SMPROGRAMS\Privacy Browser Developer"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\PrivacyBrowser-Dev"
  ; Unknown application files, profiles, bookmarks and downloads are retained.
SectionEnd
