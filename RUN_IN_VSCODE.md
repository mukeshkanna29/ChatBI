# Running ChatBI in VS Code

This project already has a working virtual environment (`.venv`) with every
dependency installed. These steps get it running inside VS Code specifically —
for general setup and features, see [README.md](README.md).

## 1. Open the project

Open VS Code, then **File → Open Folder…** and select this folder
(`D:\Cprojects\BI`) — not a parent folder, and not just `app.py` on its own.

If you have the `code` command on PATH, you can instead run from a terminal
already in this folder:

```powershell
code .
```

## 2. Install the Python extension (one-time)

If it isn't already installed: open the Extensions panel (`Ctrl+Shift+X`),
search for **Python** (publisher: Microsoft), and install it. This is what
lets VS Code find `.venv` and gives you the integrated terminal, debugging,
and IntelliSense for `app.py`.

## 3. Point VS Code at the existing `.venv`

1. Press `Ctrl+Shift+P` to open the Command Palette.
2. Run **Python: Select Interpreter**.
3. Pick the one under `.venv` — it's listed as something like:
   `Python 3.x.x ('.venv': venv) .\.venv\Scripts\python.exe`

If `.venv` doesn't show up in that list, point VS Code at it directly by
entering the path when prompted:

```
.venv\Scripts\python.exe
```

You only need to do this once per VS Code window — it's remembered for this
folder from then on, and the integrated terminal will activate `.venv`
automatically every time you open a new one.

## 4. Run the app

Streamlit apps aren't started with the `▷ Run` button — they run as a
**server**, so use VS Code's integrated terminal (`` Ctrl+` `` to open one):

```powershell
streamlit run app.py
```

VS Code opens the terminal already inside `.venv`, so this resolves to
`.venv\Scripts\streamlit.exe` without you needing to type the full path.
Streamlit prints a local URL — usually `http://localhost:8501` — open it in
your browser, or `Ctrl`+click the link directly in the VS Code terminal.

To stop the server, click into that terminal and press `Ctrl+C`.

### One-click alternative: a VS Code task

If you'd rather press a button than type the command, add
`.vscode/tasks.json` with:

```json
{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "Run ChatBI",
      "type": "shell",
      "command": "${workspaceFolder}/.venv/Scripts/streamlit.exe",
      "args": ["run", "app.py"],
      "isBackground": true,
      "problemMatcher": [],
      "presentation": { "reveal": "always", "panel": "dedicated" }
    }
  ]
}
```

Then run it from **Terminal → Run Task… → Run ChatBI**, or bind it to a
keyboard shortcut. Stop it the same way — `Ctrl+C` in its terminal panel, or
the trash-can icon on the panel itself.

## 5. Generate sample data (optional)

Also run from the integrated terminal, with the same `.venv` active:

```powershell
python make_sample_files.py       # five small practice datasets
python make_demo_dataset.py       # one richer dataset with real signals in it
```

Both write to a **ChatBI samples** folder on your Desktop.

## If the interpreter or imports look wrong

- **Command Palette → Python: Select Interpreter** shows a different/global
  Python, or `import streamlit` is underlined red in `app.py`: re-run step 3.
- **A terminal opened before you picked the interpreter** won't have `.venv`
  active. Close it and open a fresh one (`` Ctrl+` ``, or the `+` on the
  terminal panel) — new terminals pick up the interpreter you selected.
- **`.venv` is missing entirely** (e.g. this folder was copied to another
  machine without it — virtual environments don't travel between machines):

  ```powershell
  python -m venv .venv
  .venv\Scripts\Activate.ps1
  pip install -r requirements.txt
  ```

  If PowerShell blocks the activation script with a execution-policy error,
  run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` first, or
  just use the integrated terminal's *Command Prompt* profile instead of
  PowerShell, where `.venv\Scripts\activate.bat` needs no policy change.
