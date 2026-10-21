# Diagnostic-only reads of three fixed, freshly authored synthetic link cases.
# No arguments, user paths, shell folders, registry, resolution, saving or launch.
[CmdletBinding()]
param()
# Binding rejects undeclared inputs; reject bound common parameters below too.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Root = 'D:\pb\installer-syntax\execution\app\shortcut-probe'
$Output = 'D:\pb\evidence\installer-synthetic-shortcuts.json'
$MaxString = 1024
$MaxReportBytes = 1048576

function Assert-True([bool]$Condition) {
    if (-not $Condition) { throw [InvalidOperationException]::new('Synthetic shortcut invariant failed.') }
}
Assert-True ($IsWindows -and $env:GITHUB_ACTIONS -ceq 'true' -and $env:RUNNER_ENVIRONMENT -ceq 'github-hosted')
Assert-True ($PSBoundParameters.Count -eq 0)

# These are built-in Windows COM/.NET APIs only. Uncalled interface slots are
# required to preserve the native vtable order; no setter is ever invoked.
Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Text;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using Microsoft.Win32.SafeHandles;
namespace PrivacyBrowserSynthetic {
    [ComImport, Guid("000214F9-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IShellLinkW {
        [PreserveSig] int GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder path, int count, IntPtr findData, uint flags);
        [PreserveSig] int GetIDList(out IntPtr pidl);
        [PreserveSig] int SetIDList(IntPtr pidl);
        [PreserveSig] int GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder value, int count);
        [PreserveSig] int SetDescription([MarshalAs(UnmanagedType.LPWStr)] string value);
        [PreserveSig] int GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder value, int count);
        [PreserveSig] int SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string value);
        [PreserveSig] int GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder value, int count);
        [PreserveSig] int SetArguments([MarshalAs(UnmanagedType.LPWStr)] string value);
        [PreserveSig] int GetHotkey(out ushort value);
        [PreserveSig] int SetHotkey(ushort value);
        [PreserveSig] int GetShowCmd(out int value);
        [PreserveSig] int SetShowCmd(int value);
        [PreserveSig] int GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder path, int count, out int index);
    }
    [ComImport, Guid("0000010B-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IPersistFileRead {
        [PreserveSig] int GetClassID(out Guid clsid);
        [PreserveSig] int IsDirty();
        [PreserveSig] int Load([MarshalAs(UnmanagedType.LPWStr)] string path, uint mode);
    }
    public sealed class WideResult {
        public string target, arguments, icon, working_directory, error_type;
        public int? icon_index, error_hresult, load_hresult, target_hresult, arguments_hresult, icon_hresult, working_directory_hresult;
        public uint load_mode = 0, path_flags = 0;
        public bool strings_within_bound = true;
    }
    public static class LinkReader {
        const int Limit = 1024;
        static string Bounded(StringBuilder value, WideResult result) {
            string text = value.ToString();
            if (text.Length > Limit) { result.strings_within_bound = false; return text.Substring(0, Limit); }
            return text;
        }
        public static WideResult Read(string path) {
            var result = new WideResult();
            object instance = null;
            try {
                instance = Activator.CreateInstance(Type.GetTypeFromCLSID(new Guid("00021401-0000-0000-C000-000000000046"), true));
                var persist = (IPersistFileRead)instance;
                var link = (IShellLinkW)instance;
                // STGM_READ = 0; no Resolve or persistence-writing method exists here.
                result.load_hresult = persist.Load(path, 0);
                if (result.load_hresult != 0) return result;
                var text = new StringBuilder(Limit + 2);
                result.target_hresult = link.GetPath(text, text.Capacity, IntPtr.Zero, 0);
                result.target = Bounded(text, result);
                text.Clear();
                result.arguments_hresult = link.GetArguments(text, text.Capacity);
                result.arguments = Bounded(text, result);
                text.Clear();
                int iconIndex;
                result.icon_hresult = link.GetIconLocation(text, text.Capacity, out iconIndex);
                result.icon = Bounded(text, result);
                result.icon_index = iconIndex;
                text.Clear();
                result.working_directory_hresult = link.GetWorkingDirectory(text, text.Capacity);
                result.working_directory = Bounded(text, result);
            } catch (Exception error) {
                result.error_type = error.GetType().FullName;
                result.error_hresult = error.HResult;
            } finally {
                // The two interfaces alias one RCW. Release that object once.
                if (instance != null && Marshal.IsComObject(instance)) Marshal.FinalReleaseComObject(instance);
            }
            return result;
        }
    }
    public sealed class FileObservation {
        public long bytes;
        public string sha256, identity;
    }
    public static class FileSafety {
        [StructLayout(LayoutKind.Sequential)]
        struct Information {
            public uint Attributes;
            public System.Runtime.InteropServices.ComTypes.FILETIME Creation, Access, Write;
            public uint Volume, SizeHigh, SizeLow, Links, IndexHigh, IndexLow;
        }
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        static extern SafeFileHandle CreateFileW(string name, uint access, uint share, IntPtr security, uint disposition, uint flags, IntPtr template);
        [DllImport("kernel32.dll", SetLastError=true)]
        [return: MarshalAs(UnmanagedType.Bool)]
        static extern bool GetFileInformationByHandle(SafeFileHandle handle, out Information information);
        public static SafeFileHandle PinDirectory(string path) {
            // Top-down LIST_DIRECTORY/read-attribute handles pin names. No
            // delete sharing; inspect the opened directory, not its path.
            var handle = CreateFileW(path, 0x81, 3, IntPtr.Zero, 3, 0x02200000, IntPtr.Zero);
            if (handle.IsInvalid) { int error = Marshal.GetLastWin32Error(); handle.Dispose(); throw new Win32Exception(error); }
            Information info;
            if (!GetFileInformationByHandle(handle, out info)) { int error = Marshal.GetLastWin32Error(); handle.Dispose(); throw new Win32Exception(error); }
            if ((info.Attributes & 0x410) != 0x10) { handle.Dispose(); throw new InvalidOperationException(); }
            return handle;
        }
        static FileStream PinFile(string path) {
            // All ancestors have already been pinned. OPEN_REPARSE_POINT plus
            // handle attributes rejects reparse leaves before COM sees a path.
            var handle = CreateFileW(path, 0x80000000, 1, IntPtr.Zero, 3, 0x00200000, IntPtr.Zero);
            if (handle.IsInvalid) { int error = Marshal.GetLastWin32Error(); handle.Dispose(); throw new Win32Exception(error); }
            Information info;
            if (!GetFileInformationByHandle(handle, out info)) { int error = Marshal.GetLastWin32Error(); handle.Dispose(); throw new Win32Exception(error); }
            if ((info.Attributes & 0x410) != 0 || info.Links != 1 || info.SizeHigh != 0 || info.SizeLow == 0 || info.SizeLow > 65536) {
                handle.Dispose(); throw new InvalidOperationException();
            }
            return new FileStream(handle, FileAccess.Read);
        }
        public static FileObservation ObserveFile(string path) {
            // Close this read-only handle before any COM load. Holding it
            // during WSH could manufacture a sharing failure not in the real
            // installer test. Parent pins remain; this is not a race proof.
            using (var stream = PinFile(path)) {
                Information info;
                if (!GetFileInformationByHandle(stream.SafeFileHandle, out info)) throw new Win32Exception(Marshal.GetLastWin32Error());
                using (var sha = SHA256.Create()) {
                    return new FileObservation {
                        bytes = stream.Length,
                        sha256 = BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant(),
                        identity = info.Volume.ToString("x8") + ":" + info.IndexHigh.ToString("x8") + ":" + info.IndexLow.ToString("x8")
                    };
                }
            }
        }
    }
}
'@

function Bounded-String($Value) {
    $text = [string]$Value
    if ($text.Length -gt $MaxString) { return $text.Substring(0, $MaxString) }
    return $text
}
function Read-Wsh([string]$Path) {
    $result = [ordered]@{
        target = $null; arguments = $null; icon = $null; icon_index = $null; icon_location = $null
        working_directory = $null; strings_within_bound = $true; read_succeeded = $false
        error_type = $null; error_hresult = $null
    }
    $shell = $null
    $shortcut = $null
    try {
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut($Path)
        # CreateShortcut loads the existing final .lnk. Never invoke Save.
        $values = @([string]$shortcut.TargetPath, [string]$shortcut.Arguments,
                    [string]$shortcut.IconLocation, [string]$shortcut.WorkingDirectory)
        foreach ($value in $values) {
            if ($value.Length -gt $MaxString) { $result.strings_within_bound = $false }
        }
        $result.target = Bounded-String $values[0]
        $result.arguments = Bounded-String $values[1]
        $result.icon_location = Bounded-String $values[2]
        $result.working_directory = Bounded-String $values[3]
        # Parse only the final index delimiter; retain the raw WSH value too.
        $separator = $values[2].LastIndexOf(',')
        Assert-True ($separator -ge 0)
        $result.icon = Bounded-String $values[2].Substring(0, $separator)
        $index = 0
        Assert-True ([int]::TryParse($values[2].Substring($separator + 1).Trim(), [ref]$index))
        $result.icon_index = $index
        $result.read_succeeded = $true
    } catch {
        $result.error_type = $_.Exception.GetType().FullName
        $result.error_hresult = $_.Exception.HResult
    } finally {
        if ($null -ne $shortcut) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($shortcut) }
        if ($null -ne $shell) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($shell) }
    }
    return $result
}
function Matches-Expected($Actual, $Expected) {
    # Keep the real gate's case-insensitive target requirement, not normalized,
    # basename-only, raw-path-only or preferred-reader fallback comparisons.
    return ($Actual.strings_within_bound -and $null -eq $Actual.error_type -and
        $Actual.target -ieq $Expected.target -and $Actual.arguments -ceq $Expected.arguments -and
        $Actual.icon -ceq $Expected.icon -and $Actual.icon_index -eq $Expected.icon_index -and
        $Actual.working_directory -ceq $Expected.working_directory)
}

$report = [ordered]@{
    schema_version = 1; purpose = 'isolated synthetic shortcut round-trip readback'
    fixture_root = $Root; case_count = 3; passed = $false; cases = @()
    error_type = $null; error_hresult = $null
    get_path_flags = 0; persist_load_mode = 0; process_bits = [IntPtr]::Size * 8
    powershell_version = $PSVersionTable.PSVersion.ToString(); browser_executed = $false
    shortcut_executed = $false; resolve_called = $false; save_called = $false
    production_installer_tested = $false; release_eligible = $false
    limitations = 'Synthetic link semantics only. No icon rendering, browser execution or concurrent-writer proof. A passing probe does not explain the earlier real installer failure.'
}
$directories = [Collections.Generic.List[IDisposable]]::new()
$outputStream = $null
$exitCode = 1
try {
    # Pin every existing ancestor top-down before opening its descendants. The
    # evidence directory must already exist; this reader never creates folders.
    foreach ($path in @('D:\', 'D:\pb', 'D:\pb\evidence')) {
        $directories.Add([PrivacyBrowserSynthetic.FileSafety]::PinDirectory($path))
    }
    $outputStream = [IO.File]::Open($Output, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    foreach ($path in @('D:\pb\installer-syntax', 'D:\pb\installer-syntax\execution',
                       'D:\pb\installer-syntax\execution\app', $Root)) {
        $directories.Add([PrivacyBrowserSynthetic.FileSafety]::PinDirectory($path))
    }
    $definitions = @(
        @{ id = 'ascii'; folder = 'PrivacyBrowser-ASCII'; profile = 'ascii profile' },
        @{ id = 'unicode'; folder = 'PrivacyBrowser-日本語'; profile = '日本語 profile' },
        @{ id = 'unicode-space'; folder = 'PrivacyBrowser 日本語 Space'; profile = '日本語 space profile' }
    )
    foreach ($definition in $definitions) {
        $caseRoot = $Root + '\' + $definition.folder
        $case = [ordered]@{
            id = $definition.id; passed = $false; error_type = $null; error_hresult = $null
            expected = [ordered]@{
                target = $caseRoot + '\privacybrowser.exe'
                arguments = '-no-remote -profile "' + $Root + '\profiles\' + $definition.profile + '"'
                icon = $caseRoot + '\reference.ico'; icon_index = 0; working_directory = $caseRoot
            }
            links = @(); copies_identical = $false; files_unchanged = $false
        }
        $report.cases += $case
        try {
            $directories.Add([PrivacyBrowserSynthetic.FileSafety]::PinDirectory($caseRoot))
            $directories.Add([PrivacyBrowserSynthetic.FileSafety]::PinDirectory($caseRoot + '\relocated'))
            # Validate inert references without holding file locks during COM.
            foreach ($path in @($case.expected.target, $case.expected.icon)) {
                [void][PrivacyBrowserSynthetic.FileSafety]::ObserveFile($path)
            }
            $entries = @(
                @{ kind = 'direct'; relative = 'direct.lnk'; wsh = $true },
                @{ kind = 'desktop-snapshot'; relative = 'PrivacyBrowser.desktop-link'; wsh = $false },
                @{ kind = 'program-snapshot'; relative = 'PrivacyBrowser.program-link'; wsh = $false },
                @{ kind = 'desktop-copy'; relative = 'relocated\desktop.lnk'; wsh = $true },
                @{ kind = 'program-copy'; relative = 'relocated\program.lnk'; wsh = $true }
            )
            foreach ($entry in $entries) {
                $path = $caseRoot + '\' + $entry.relative
                $before = [PrivacyBrowserSynthetic.FileSafety]::ObserveFile($path)
                $case.links += [ordered]@{
                    kind = $entry.kind; path = $path; bytes = $before.bytes
                    identity_before = $before.identity; identity_after = $null
                    sha256_before = $before.sha256; sha256_after = $null
                    wide = $null; wsh = $null; wsh_required = $entry.wsh; passed = $false
                }
            }
            foreach ($link in $case.links) {
                $link.wide = [PrivacyBrowserSynthetic.LinkReader]::Read($link.path)
                $wideOk = Matches-Expected $link.wide $case.expected
                foreach ($name in @('load_hresult', 'target_hresult', 'arguments_hresult', 'icon_hresult', 'working_directory_hresult')) {
                    $wideOk = $wideOk -and $null -ne $link.wide.$name -and $link.wide.$name -eq 0
                }
                $wshOk = $true
                if ($link.wsh_required) {
                    $link.wsh = Read-Wsh $link.path
                    $wshOk = $link.wsh.read_succeeded -and (Matches-Expected $link.wsh $case.expected)
                }
                $link.passed = $wideOk -and $wshOk
            }
            # Readback must not modify either custom-extension snapshots or any
            # direct/copied .lnk. Reopen without reparses and retain identity too.
            foreach ($link in $case.links) {
                $after = [PrivacyBrowserSynthetic.FileSafety]::ObserveFile($link.path)
                $link.sha256_after = $after.sha256
                $link.identity_after = $after.identity
                $link.passed = ($link.passed -and $link.sha256_before -ceq $link.sha256_after -and
                                $link.identity_before -ceq $link.identity_after -and $link.bytes -eq $after.bytes)
            }
            $case.files_unchanged = @($case.links | Where-Object {
                $_.sha256_before -cne $_.sha256_after -or $_.identity_before -cne $_.identity_after
            }).Count -eq 0
            $case.copies_identical = ($case.links[1].sha256_before -ceq $case.links[3].sha256_before -and
                                     $case.links[2].sha256_before -ceq $case.links[4].sha256_before)
            $case.passed = ($case.files_unchanged -and $case.copies_identical -and
                           @($case.links | Where-Object { -not $_.passed }).Count -eq 0)
        } catch {
            $case.error_type = $_.Exception.GetType().FullName
            $case.error_hresult = $_.Exception.HResult
        }
    }
    $report.passed = ($report.cases.Count -eq 3 -and @($report.cases | Where-Object { -not $_.passed }).Count -eq 0)
    if ($report.passed) { $exitCode = 0 }
} catch {
    $report.error_type = $_.Exception.GetType().FullName
    $report.error_hresult = $_.Exception.HResult
} finally {
    try {
        if ($null -ne $outputStream) {
            $bytes = [Text.Encoding]::UTF8.GetBytes(($report | ConvertTo-Json -Depth 12 -Compress) + [Environment]::NewLine)
            Assert-True ($bytes.Length -le $MaxReportBytes)
            $outputStream.Write($bytes, 0, $bytes.Length)
            $outputStream.Flush($true)
        }
    } finally {
        if ($null -ne $outputStream) { $outputStream.Dispose() }
        for ($index = $directories.Count - 1; $index -ge 0; $index--) { $directories[$index].Dispose() }
    }
}
if ($exitCode -ne 0) { [Console]::Error.WriteLine('Synthetic shortcut readback failed; retain the fixed diagnostic JSON.') }
exit $exitCode
