#!/usr/bin/env python3
import re, sys

src = open("stephen_studio.html", encoding="utf-8").read()

# 1. onclick handlers -> functions
handlers = set()
for m in re.finditer(r'on(?:click|input|change)="([A-Za-z_][A-Za-z0-9_]*)\s*\(', src):
    handlers.add(m.group(1))
fns = set()
for m in re.finditer(r'(?:^|\n)\s*(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(', src):
    fns.add(m.group(1))
fns |= set(re.findall(r'\bwindow\.onNativeMidi\b', src)) - set()
fns |= set(re.findall(r'\bwindow\.onMidiDevices\b', src)) - set()

missing_fns = sorted(h for h in handlers if h not in fns)
print("== FUNCTIONS CALLED BUT NOT DEFINED ==")
for f in missing_fns or ["(none)"]:
    print("  MISSING:", f)

# 2. getElementById targets vs id= attributes
ids_in_html = set(re.findall(r'\bid="([A-Za-z0-9_\-]+)"', src))
ids_used = set(re.findall(r'getElementById\(["\']([A-Za-z0-9_\-]+)["\']\)', src))
missing_ids = sorted(i for i in ids_used if i not in ids_in_html)
print("== getElementById TARGETS NOT IN HTML ==")
for i in missing_ids or ["(none)"]:
    print("  MISSING:", i)

# 3. dynamic ids built at runtime (template) - report as info
dyn = sorted(set(re.findall(r'getElementById\(["\']([^"\']*\+[^"\']*)["\']\)', src)))
print("== DYNAMIC ID TEMPLATES (check manually) ==")
for d in dyn or ["(none)"]:
    print("  ", d)

# 4. functions defined but unused (info only)
defined = set(re.findall(r'(?:^|\n)\s*(?:async\s+)?function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(', src))
called = set(re.findall(r'\b([A-Za-z_][A-Za-z0-9_]*)\s*\(', src))
unused = sorted(d for d in defined if d not in called)
print("== DEFINED BUT NEVER CALLED ==")
for u in unused or ["(none)"]:
    print("  ", u)
