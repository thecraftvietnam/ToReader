# Developer Guide — ToReader Manga Viewer

## 1. Prerequisites
- **Python 3.9+** (Windows recommended).
- Required libraries:
  bash
  pip install Pillow pymupdf

- Build tool:
  pip install pyinstaller

## 2. Running the Python script directly
- Navigate to the folder containing chapter_viewer.py:
  cd /C "C:\Build"
  python chapter_viewer.py

- The viewer opens. Use the toolbar to load a manga folder (📁 Manga Folder) or a .cbz / .pdf file (📄 Manga File).

## 3. Building a portable .exe
- Place chapter_viewer.py and icon.ico in the same folder.
- Save the provided toreader_onefile.spec file in that folder.
- Run:
  pyinstaller toreader_onefile.spec
- Output: dist/ToReader.exe (single-file executable with icon).

## 4. Common build options
- Debugging: set console=True in the spec to see errors in a terminal window.
- Hidden imports: add modules to hiddenimports if PyInstaller misses them (e.g., fitz for PDF support).
- Extra data: add folders or sample files to datas in the spec:
  datas=[('icon.ico', '.'), ('samples/*', 'samples')]

## 5. Alternative CLI build
- Instead of using the spec file, you can run:
  pyinstaller --onefile --windowed --icon=icon.ico chapter_viewer.py
- This produces the same single-file executable.
