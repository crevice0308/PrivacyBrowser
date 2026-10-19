#!/usr/bin/env python3
"""Check project syntax, local imports, XML assets and chrome trust boundaries."""
import ast
import json
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
errors = []
counts = {'python':0,'javascript':0,'xml':0}
for source in sorted((ROOT/'tools').glob('*.py')):
    try:
        ast.parse(source.read_text(encoding='utf-8'),filename=str(source));counts['python']+=1
    except SyntaxError as e:
        errors.append(f'{source.relative_to(ROOT)}: {e.msg}')
for source in sorted((ROOT/'browser/privacy').rglob('*.mjs')):
    result=subprocess.run(['node','--check',str(source)],capture_output=True,text=True)
    counts['javascript']+=1
    if result.returncode:
        errors.append(f'{source.relative_to(ROOT)}: invalid JavaScript')
    for relative in re.findall(r'(?:from\s*|import\s*)[\'"](\./[^\'"]+)[\'"]',source.read_text()):
        if not (source.parent/relative).is_file():
            errors.append(f'{source.relative_to(ROOT)}: missing relative import {relative}')
for source in sorted((ROOT/'browser/privacy').rglob('*')):
    if source.suffix in ['.svg','.xhtml']:
        try:ET.parse(source);counts['xml']+=1
        except ET.ParseError:errors.append(f'{source.relative_to(ROOT)}: invalid XML')
manifest=(ROOT/'browser/privacy/chrome.manifest').read_text()
if 'contentaccessible' in manifest.lower():
    errors.append('Privileged chrome package must not be contentaccessible')
for source in (ROOT/'browser/privacy').rglob('*'):
    if not source.is_file() or source.suffix in ['.png','.ico']:
        continue
    text=source.read_text()
    if 'resource://gre/modules/Services.sys.mjs' in text:
        errors.append(f'{source.relative_to(ROOT)}: removed ESR Services module')
    for uri in re.findall(r'(?:resource://privacybrowser/|chrome://privacybrowser/(?:content|skin)/)([A-Za-z0-9_./-]+)',text):
        if not any((ROOT/'browser/privacy'/prefix/uri).is_file() for prefix in ['', 'content','skin']):
            errors.append(f'{source.relative_to(ROOT)}: missing local resource {uri}')
    if re.search(r'(?:security\.sandbox[^\n]*[,=]\s*0|fission\.autostart[^\n]*[,=]\s*false|xpinstall\.signatures\.required[^\n]*[,=]\s*false)',text):
        errors.append(f'{source.relative_to(ROOT)}: forbidden security downgrade')
print(json.dumps({'ok':not errors,'checked':counts,'errors':errors},indent=2))
sys.exit(bool(errors))
