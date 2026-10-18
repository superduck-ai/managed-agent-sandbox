import glob


def executable():
    matches = glob.glob('/opt/oma-tools-browsers/chromium_headless_shell-*/**/headless_shell', recursive=True)
    matches += glob.glob('/opt/oma-tools-browsers/chromium_headless_shell-*/**/chrome-headless-shell', recursive=True)
    if len(matches) != 1:
        raise ValueError('Installed Chromium headless shell is missing or ambiguous; ask the environment administrator to repair the toolkit image')
    return matches[0]
