# Shared test-only read-only Unicode reader. Callers enforce hosted/fresh-path guards.
# WSH observations never decide acceptance and never replace a wide-reader failure.
# These are built-in Windows COM/.NET APIs only. Uncalled interface slots are
# required to preserve the native vtable order; no setter is ever invoked.
$MaxString = 1024
Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Text;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using Microsoft.Win32.SafeHandles;
namespace PrivacyBrowserShortcut {
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
        public static bool Matches(WideResult result, string target, string arguments,
                                   string icon, int iconIndex, string workingDirectory) {
            if (result == null || !result.strings_within_bound || result.error_type != null ||
                result.error_hresult != null || result.load_mode != 0 || result.path_flags != 0 ||
                result.load_hresult != 0 || result.target_hresult != 0 || result.arguments_hresult != 0 ||
                result.icon_hresult != 0 || result.working_directory_hresult != 0) return false;
            foreach (string value in new [] { result.target, result.arguments, result.icon, result.working_directory,
                                              target, arguments, icon, workingDirectory }) {
                if (String.IsNullOrEmpty(value) || value.Length > Limit) return false;
            }
            return String.Equals(result.target, target, StringComparison.OrdinalIgnoreCase) &&
                   String.Equals(result.arguments, arguments, StringComparison.Ordinal) &&
                   String.Equals(result.icon, icon, StringComparison.Ordinal) && result.icon_index == iconIndex &&
                   String.Equals(result.working_directory, workingDirectory, StringComparison.Ordinal);
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
        [DllImport("kernel32.dll", SetLastError=true)]
        static extern uint GetFileType(SafeFileHandle handle);
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
            if (GetFileType(handle) != 1 || (info.Attributes & 0x410) != 0 || info.Links != 1 || info.SizeHigh != 0 || info.SizeLow == 0 || info.SizeLow > 65536) {
                handle.Dispose(); throw new InvalidOperationException();
            }
            return new FileStream(handle, FileAccess.Read);
        }
        public static bool EqualFiles(string left, string right) {
            using (var a = PinFile(left)) using (var b = PinFile(right)) {
                if (a.Length != b.Length) return false;
                int value;
                while ((value = a.ReadByte()) != -1) if (value != b.ReadByte()) return false;
                return b.ReadByte() == -1;
            }
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
        if ($separator -lt 0) { throw [InvalidOperationException]::new('Missing diagnostic icon delimiter.') }
        $result.icon = Bounded-String $values[2].Substring(0, $separator)
        $index = 0
        if (-not [int]::TryParse($values[2].Substring($separator + 1).Trim(), [ref]$index)) { throw [InvalidOperationException]::new('Invalid diagnostic icon index.') }
        $result.icon_index = $index
        $result.read_succeeded = $true
    } catch {
        $result.error_type = $_.Exception.GetType().FullName
        $result.error_hresult = $_.Exception.HResult
    } finally {
        # A diagnostic cleanup failure is retained, never promoted into the
        # authoritative wide-reader verdict and never retried.
        foreach ($instance in @($shortcut, $shell)) {
            if ($null -ne $instance) {
                try { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($instance) }
                catch {
                    $result.read_succeeded = $false
                    $result.error_type = $_.Exception.GetType().FullName
                    $result.error_hresult = $_.Exception.HResult
                }
            }
        }
    }
    return $result
}
function Test-WideShortcut($Actual, $Expected) {
    return [PrivacyBrowserShortcut.LinkReader]::Matches($Actual, $Expected.target, $Expected.arguments,
        $Expected.icon, $Expected.icon_index, $Expected.working_directory)
}

function Pin-ShortcutParents([string]$Path, $Directories, $Pinned) {
    $parents = [Collections.Generic.List[string]]::new()
    $parent = [IO.Path]::GetDirectoryName($Path)
    while ($parent) {
        $parents.Add($parent)
        $parent = [IO.Path]::GetDirectoryName($parent)
    }
    for ($index = $parents.Count - 1; $index -ge 0; $index--) {
        if (-not $Pinned.Contains($parents[$index])) {
            $handle = [PrivacyBrowserShortcut.FileSafety]::PinDirectory($parents[$index])
            $Directories.Add($handle)
            [void]$Pinned.Add($parents[$index])
        }
    }
}
