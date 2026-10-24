# Build: uv run --group build pyinstaller league_helper.spec --noconfirm
# Output: dist/LoLCompanion/LoLCompanion.exe (onedir: faster start, fewer AV false positives)
a = Analysis(
    ["app.py"],
    datas=[("panel", "panel"), ("config.toml", ".")],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="LoLCompanion", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="LoLCompanion")
