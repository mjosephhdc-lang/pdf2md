# PDF to Markdown — Batch Converter

A Windows desktop app that converts English-text PDFs into clean Markdown,
with drag-and-drop batch processing and OCR fallback for scanned pages.

## Features

- Drag-and-drop or file/folder picker to queue PDFs
- Batch conversion with per-file progress and a live log
- Heading, bullet/numbered list, bold-text, and table detection based on
  font size and layout (not just raw text dump)
- Automatic OCR fallback (Tesseract) for scanned/image-only pages
- Choose an output folder, or write each `.md` next to its source PDF

## Project layout

```
pdf2md/
  requirements.txt
  pdf2md.spec              # PyInstaller build spec (Windows)
  src/pdf2md/
    __init__.py
    __main__.py             # entry point: python -m pdf2md
    converter.py             # PDF -> Markdown conversion engine
    worker.py                # background batch worker (QThread)
    main_window.py           # PyQt6 GUI
```

## Running from source (development)

1. Install Python 3.11+ on Windows.
2. Install Tesseract OCR (only needed if you want scanned-PDF support):
   https://github.com/UB-Mannheim/tesseract/wiki — install and make sure
   `tesseract.exe` is on your PATH, or set
   `pytesseract.pytesseract.tesseract_cmd` to its full path near the top
   of `converter.py`.
3. From the `pdf2md` folder:

   ```
   pip install -r requirements.txt
   python -m pdf2md
   ```

## Building the Windows .exe

From the `pdf2md` folder, **on a Windows machine** (PyInstaller builds
for the OS it runs on — you can't cross-build a Windows .exe from Linux):

```
pip install -r requirements.txt
pyinstaller pdf2md.spec
```

The app will be in `dist\PDF2Markdown\PDF2Markdown.exe` (a folder you can
zip and share — this starts faster than a single-file build). A
single-file alternative is included as a commented-out block at the
bottom of `pdf2md.spec` if you'd rather ship one `.exe`.

If you want OCR to work without requiring users to separately install
Tesseract, bundle the Tesseract Windows binary into `datas` in the spec
file and point `pytesseract.pytesseract.tesseract_cmd` at the bundled
path at runtime — ask and I can wire that up.

## Notes on output quality

- Works best on PDFs with real (selectable) text — reports, articles,
  letters, manuals.
- Scanned/photographed pages are OCR'd automatically; OCR text comes
  back as plain paragraphs (no heading/list structure, since that
  metadata doesn't exist in an image).
- Very complex layouts (multi-column papers, deeply nested tables,
  footnotes/sidebars) are converted but won't be pixel-perfect — nothing
  fully automated handles those 100% of the time.
