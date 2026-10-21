# XPression As Run Log Formatter

A standalone replacement for the Ross Video *XPression As Run Log Formatter*,
rebuilt in Python with one major enhancement: **multiple source folders**.
All `.arlog` files from every listed folder are combined and processed together,
and `.thumb` thumbnails are searched across all of them.

Built for the Baltimore Ravens broadcast team.

---

## Features

- Parse one or many `.arlog` files from **multiple source folders**
- Decode `.thumb` thumbnail files (Windows BMP) and embed them in the PDF
- **Report Header**: Title, Client, Note
- **Date / Time Range**: Today or a custom start/end date, with optional
  time-of-day start/end
- **Group Based On**: Scene Name / Item Name / Content / Content Items
- **Sort Based On**: First Time On Air / Scene Name / Item Name / Content
- **Partitioning**: None / Game State (by layer) / Time Ranges (named windows)
- **Concealing Layers**: Add / Edit / Delete rules that hide a "Concealed"
  layer (by FBID) whenever a "Concealing" layer is live at the same time
- **Filter** by content item text
- Live preview, **Export to CSV** and **Export to PDF**
  (Ravens purple `#24135F` headers, gold `#9A7611` rules, white background)
- Save / Load report settings as JSON; last-used settings auto-restored

---

## Running from source

Requires **Python 3.8+**.

```bash
pip install reportlab Pillow
python xp_formatter.py
```

`tkinter` ships with the standard CPython installer on Windows and macOS.
On some Linux distributions install it separately, e.g. `sudo apt install python3-tk`.

---

## Building the Windows `.exe`

On a Windows machine with Python 3.8+ installed, simply run:

```
build_exe.bat
```

This installs the dependencies and builds a single-file, windowed executable
using the provided `xp_formatter.spec`. The result is:

```
dist\XPressionAsRunFormatter.exe
```

Double-click it to run — no Python installation is required on the target PC.

To build manually instead:

```
pip install reportlab Pillow pyinstaller
pyinstaller --noconfirm --clean xp_formatter.spec
```

> **Note:** the `.exe` must be built *on Windows* to produce a Windows binary.
> PyInstaller is not a cross-compiler.

### Optional: application icon
Place an `app.ico` next to the spec file and uncomment the `icon='app.ico'`
line in `xp_formatter.spec` before building.

---

## Usage

1. **As Run Log File Source** – click **Add Folder…** to add one or more
   folders that contain `.arlog` (and `.thumb`) files. Use **Remove** to drop a
   folder. Click **Load / Refresh** to parse them.
2. Fill in the **Report Header** (Title / Client / Note).
3. Choose a **Date / Time Range** (dates are `MM/DD/YYYY`).
4. Pick **Group Based On** and **Sort Based On** options.
5. Optionally configure **Partitioning** (Game State or Time Ranges) and
   **Concealing Layers**.
6. Click **Refresh Preview** to see the grouped/sorted result.
7. Click **Export to CSV…** or **Export To PDF…** to save the report.

Use **File → Save/Load Report Settings** to store a configuration for reuse.
The app also remembers your last session automatically
(`xp_formatter_config.json`, saved next to the executable).

---

## Log format reference

```
*** XPression <version> started @ <date>
@ONAIRTIME,OFFAIRTIME,ITEMID,ITEMNAME,FBID,LAYER,SCENENAME,SCENEUID,THUMBHASH,CONTENT
<logts>: <ONAIRTIME>\t<OFFAIRTIME>\t<ITEMID>\t<ITEMNAME>\t<FBID>\t<LAYER>\t<SCENENAME>\t<SCENEUID>\t<THUMBHASH>\t<CONTENT>
```

- Dates inside the columns use `DD-MM-YYYY HH:MM:SS.mmm`.
- `THUMBHASH` maps to a `<THUMBHASH>.thumb` file (Windows BMP) in a source folder.
- Blank, header, and malformed lines are skipped gracefully; a count of skipped
  lines is shown in the status bar.

---

## Files

| File                 | Purpose                                             |
|----------------------|-----------------------------------------------------|
| `xp_formatter.py`    | The complete application (GUI + parsing + export)   |
| `xp_formatter.spec`  | PyInstaller spec for the single-file Windows `.exe` |
| `build_exe.bat`      | One-click Windows build script                      |
| `README.md`          | This file                                           |

---

*XPression As Run Log Formatter — version 1.0*
