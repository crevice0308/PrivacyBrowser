#!/usr/bin/env python3
"""Compile an unsigned, per-user Windows 11 AMD64 developer NSIS installer.

This packages a supplied developer overlay. It does not compile Gecko, sign the
setup, or certify Windows runtime behavior. Native payload files are retained
byte-for-byte; the installer is for private development testing only.
"""
from __future__ import annotations
import argparse
import configparser
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
DEVICE=re.compile(r'(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?\Z',re.I)
SAFE_PART=re.compile(r'[A-Za-z0-9 ._+()@=-]+\Z')
REQUIRED=('firefox.exe','xul.dll','mozglue.dll','nss3.dll','application.ini','platform.ini','omni.ja','browser/omni.ja','dependentlibs.list','PRIVACY_BROWSER_BUILD.json','privacybrowser.cfg','defaults/pref/privacybrowser-autoconfig.js','distribution/privacybrowser/chrome.manifest','distribution/privacybrowser/assets/privacybrowser.ico','distribution/policies.json')

def runtime_files(runtime:Path)->list[Path]:
    runtime=runtime.resolve()
    if not runtime.is_dir():raise ValueError('Runtime directory missing')
    output=[]
    seen=set()
    for path in sorted(runtime.rglob('*')):
        if path.is_symlink():raise ValueError('Symlinks cannot enter Windows installer payload')
        parts=path.relative_to(runtime).parts
        if any(part in {'.','..'} or not SAFE_PART.fullmatch(part) or DEVICE.fullmatch(part) or part[-1:] in {' ','.'} for part in parts):raise ValueError('Unsafe Windows/NSIS payload filename')
        folded='/'.join(parts).casefold()
        if folded in seen:raise ValueError('Case-colliding Windows payload paths')
        seen.add(folded)
        if path.is_file():output.append(path)
        elif not path.is_dir():raise ValueError('Non-regular payload entry')
    return output

def pe_machine(binary:Path)->int:
    with binary.open('rb') as stream:
        dos=stream.read(64)
        if len(dos)!=64 or dos[:2]!=b'MZ':raise ValueError('Missing DOS executable header')
        offset=struct.unpack_from('<I',dos,0x3c)[0]
        if offset<64 or offset>binary.stat().st_size-26:raise ValueError('Invalid PE header offset')
        stream.seek(offset);header=stream.read(26)
        if header[:4]!=b'PE\0\0':raise ValueError('Missing PE signature')
        machine=struct.unpack_from('<H',header,4)[0]
        magic=struct.unpack_from('<H',header,24)[0]
        if machine!=0x8664 or magic!=0x20b:raise ValueError('An AMD64 PE32+ browser executable is required')
        return machine

def validate_runtime(runtime:Path)->dict:
    runtime=runtime.resolve();files=runtime_files(runtime)
    for name in REQUIRED:
        if not (runtime/name).is_file():raise ValueError('Incomplete runtime: '+name)
    manifest=json.loads((runtime/'PRIVACY_BROWSER_BUILD.json').read_text())
    if manifest.get('build_kind')!='developer-runtime-overlay' or manifest.get('official_release') is not False or manifest.get('native_binary')!='firefox.exe':raise ValueError('Only explicitly unofficial developer runtimes are accepted')
    digest=hashlib.sha256((runtime/'firefox.exe').read_bytes()).hexdigest()
    if manifest.get('native_binary_sha256')!=digest:raise ValueError('Native executable changed after staging')
    pe_machine(runtime/'firefox.exe')
    config=configparser.ConfigParser();config.read(runtime/'application.ini')
    expected=json.loads((ROOT/'config/esr.json').read_text())['upstream_version'].removesuffix('esr')
    if config.get('App','Version',fallback='')!=expected:raise ValueError('Runtime ESR version differs from project pin')
    policies=json.loads((runtime/'distribution/policies.json').read_text())['policies']
    if policies.get('DisableAppUpdate') is not True:raise ValueError('Developer runtime must disable incompatible upstream application updates')
    return {'version':expected,'architecture':'AMD64','native_sha256':digest,'files':len(files),'payload_bytes':sum(p.stat().st_size for p in files),'official_release':False}

def write_payload_include(runtime:Path,destination:Path)->dict:
    runtime=runtime.resolve();files=runtime_files(runtime)
    # Source paths are compile-time NSIS strings; reject parser metacharacters.
    if any(x in str(runtime) for x in ['$', '"', '\n','\r','\t']):raise ValueError('Unsafe staging directory name')
    directories=set()
    for path in files:
        relative=path.relative_to(runtime)
        for parent in relative.parents:
            if parent!=Path('.'):directories.add(parent)
    lines=['; Generated exact payload inventory. No recursive deletion.','!macro InstallPayload']
    for path in files:
        relative=path.relative_to(runtime)
        folder='' if relative.parent==Path('.') else '\\'+str(relative.parent).replace('/','\\')
        lines += ['  SetOutPath "$INSTDIR'+folder+'"','  File "'+path.as_posix()+'"']
    lines += ['!macroend','','!macro RemovePayload']
    for path in files:
        relative=path.relative_to(runtime).as_posix().replace('/','\\')
        lines.append('  Delete "$INSTDIR\\'+relative+'"')
    for folder in sorted(directories,key=lambda x:(-len(x.parts),x.as_posix())):
        lines.append('  RMDir "$INSTDIR\\'+folder.as_posix().replace('/','\\')+'"')
    lines += ['!macroend','']
    destination.parent.mkdir(parents=True,exist_ok=True);destination.write_text('\n'.join(lines),encoding='utf-8')
    return {'files':len(files),'directories':len(directories)}

def build(runtime:Path,output:Path)->dict:
    info=validate_runtime(runtime);output=output.resolve()
    if any(x in str(output) for x in ['$', '"', '\n','\r','\t']):raise ValueError('Unsafe installer output path')
    if output==runtime.resolve() or runtime.resolve() in output.parents:raise ValueError('Installer output must be outside the runtime payload')
    output.parent.mkdir(parents=True,exist_ok=True)
    compiler=shutil.which('makensis')
    if not compiler:raise ValueError('NSIS makensis is required (Linux cross-compilation is supported)')
    with tempfile.TemporaryDirectory(prefix='privacybrowser-nsis-') as temp:
        include=Path(temp)/'payload.nsh';inventory=write_payload_include(runtime,include)
        command=[compiler,'-V3','-NOCD','-DRUNTIME_DIR='+str(runtime.resolve()),'-DPAYLOAD_INCLUDE='+str(include),'-DOUTPUT_FILE='+str(output),str(ROOT/'packaging/windows/privacy-browser-dev.nsi')]
        result=subprocess.run(command,capture_output=True,text=True)
        (output.parent/(output.name+'.build.log')).write_text(result.stdout+result.stderr)
        if result.returncode:raise RuntimeError('NSIS compilation failed: '+(result.stdout+result.stderr)[-4000:])
    data=output.read_bytes()
    if data[:2]!=b'MZ':raise ValueError('Compiler did not produce a Windows executable')
    proof={**info,'artifact':output.name,'artifact_bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'nsis_payload':inventory,'installer_compiled':True,'gecko_source_compiled':False,'authenticode_signed':False,'windows_execution_tested':False,'distribution':'private developer testing; not an independent officially branded release'}
    (output.parent/(output.name+'.json')).write_text(json.dumps(proof,indent=2)+'\n')
    return proof

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runtime',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    try:print(json.dumps(build(a.runtime,a.output),indent=2))
    except (ValueError,RuntimeError,OSError) as e:p.exit(1,str(e)+'\n')
