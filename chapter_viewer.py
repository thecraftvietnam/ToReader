"""
ToReader – Manga Viewer
=======================
A lightweight manga viewer that supports:
  • Image folders (single chapter or multi-chapter manga root)
  • .cbz and .pdf files
  • Floating chapter sidebar (toggle with ☰)
  • Next / Previous chapter navigation
  • Lazy-loading continuous vertical strip with right-side scrollbar

Requirements:
    pip install Pillow          # always required
    pip install pymupdf         # for PDF support  (import fitz)
"""

import tkinter as tk
from tkinter import filedialog, ttk, messagebox
from PIL import Image, ImageTk
import os
import re
import threading
import zipfile
import io

# Optional PDF support
try:
    import fitz  # PyMuPDF
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False

# ─────────────────────────────────────────────
#  Constants
# ─────────────────────────────────────────────

SUPPORTED_IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}
PRELOAD_RADIUS    = 2
PLACEHOLDER_COLOR = "#1e1e1e"
BG_COLOR          = "#1b1b1b"
TOOLBAR_BG        = "#1a1a1a"
TEXT_COLOR        = "#e0e0e0"
ACCENT            = "#FF5B36"
SIDEBAR_BG        = "#231D1B"
SIDEBAR_WIDTH     = 200
SIDEBAR_HOVER     = "#2E2421"
SIDEBAR_SEL       = "#FF6C3B"

# ─────────────────────────────────────────────
#  Helpers
# ─────────────────────────────────────────────

def natural_sort_key(s):
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", s)]


def get_image_files_from_folder(folder):
    files = [f for f in os.listdir(folder)
             if os.path.splitext(f)[1].lower() in SUPPORTED_IMG_EXT]
    files.sort(key=natural_sort_key)
    return [os.path.join(folder, f) for f in files]


def get_chapter_folders(root_folder):
    """
    Return a sorted list of sub-folders that contain images,
    or [root_folder] itself when the root directly contains images.
    """
    # Check if root itself has images
    root_images = get_image_files_from_folder(root_folder)
    if root_images:
        return [root_folder]           # single chapter — no sub-folders needed

    # Look for sub-folders that contain images
    chapters = []
    for entry in sorted(os.listdir(root_folder), key=natural_sort_key):
        full = os.path.join(root_folder, entry)
        if os.path.isdir(full):
            if get_image_files_from_folder(full):
                chapters.append(full)

    return chapters if chapters else [root_folder]


def load_images_from_cbz(cbz_path):
    """Return list of (name, bytes) tuples from a .cbz archive."""
    results = []
    with zipfile.ZipFile(cbz_path, "r") as zf:
        names = sorted(
            [n for n in zf.namelist()
             if os.path.splitext(n)[1].lower() in SUPPORTED_IMG_EXT],
            key=natural_sort_key,
        )
        for name in names:
            results.append((name, zf.read(name)))
    return results


def load_images_from_pdf(pdf_path):
    """Return list of (name, PIL.Image) tuples from a PDF (one per page)."""
    if not PDF_SUPPORT:
        messagebox.showerror(
            "PDF not supported",
            "Install PyMuPDF to open PDF files:\n  pip install pymupdf",
        )
        return []
    results = []
    doc = fitz.open(pdf_path)
    for i, page in enumerate(doc):
        mat = fitz.Matrix(2, 2)          # 2× scale → ~144 DPI
        pix = page.get_pixmap(matrix=mat)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        results.append((f"page_{i+1:04d}.png", img))
    doc.close()
    return results


# ─────────────────────────────────────────────
#  Main Application
# ─────────────────────────────────────────────

class ToReader:
    def __init__(self, root):
        self.root = root
        self.root.title("ToReader")
        self.root.geometry("1000x750")
        self.root.configure(bg=BG_COLOR)
        self.root.minsize(500, 400)

        # ── Chapter / image state ──────────────────────────────
        self.chapters        = []   # list of chapter folder paths (or ["CBZ/PDF"])
        self.current_chap    = -1   # index into self.chapters
        self.image_paths     = []   # resolved file paths for current chapter
        self.cbz_data        = {}   # idx -> bytes  (CBZ in-memory images)
        self.pdf_images      = {}   # idx -> PIL.Image (PDF pages)
        self.source_type     = "folder"   # "folder" | "cbz" | "pdf"
        self.source_file     = ""

        # ── Canvas / rendering state ───────────────────────────
        self.loaded          = {}   # idx -> ImageTk.PhotoImage
        self.canvas_items    = {}   # idx -> canvas item id
        self.slot_y          = []
        self.slot_h          = []
        self.zoom            = 1.0
        self.base_width      = 800

        # ── Sidebar ────────────────────────────────────────────
        self.sidebar_visible = False

        self._build_ui()
        self._bind_events()

    # ══════════════════════════════════════════════════════════
    #  UI Construction
    # ══════════════════════════════════════════════════════════

    def _build_ui(self):
        # ── Toolbar ───────────────────────────────────────────
        toolbar = tk.Frame(self.root, bg=TOOLBAR_BG, height=48)
        toolbar.pack(side=tk.TOP, fill=tk.X)
        toolbar.pack_propagate(False)

        btn = dict(bg=TOOLBAR_BG, fg=TEXT_COLOR, activebackground="#333",
                   activeforeground="white", relief=tk.FLAT,
                   font=("Segoe UI", 10), cursor="hand2", padx=8, pady=4)

        # Hamburger toggle
        tk.Button(toolbar, text="☰", command=self._toggle_sidebar,
                  **btn).pack(side=tk.LEFT, padx=(8, 4), pady=6)
          

        tk.Frame(toolbar, bg="#444", width=1).pack(side=tk.LEFT, fill=tk.Y, pady=8)

        # Open buttons
        tk.Button(toolbar, text="📁  Manga Folder",
                  command=self._open_manga_folder, **btn).pack(side=tk.LEFT, padx=(8, 4), pady=6)
        tk.Button(toolbar, text="📄  Manga File",
                  command=self._open_manga_file,   **btn).pack(side=tk.LEFT, padx=(0, 8), pady=6)

        tk.Frame(toolbar, bg="#444", width=1).pack(side=tk.LEFT, fill=tk.Y, pady=8)

        # Zoom controls
        tk.Button(toolbar, text="＋", command=self._zoom_in,
                  **btn, width=2).pack(side=tk.LEFT, padx=(8, 0), pady=6)
        self.zoom_label = tk.Label(toolbar, text="100%", bg=TOOLBAR_BG,
                                   fg=ACCENT, font=("Segoe UI", 10, "bold"), width=5)
        self.zoom_label.pack(side=tk.LEFT, pady=6)
        tk.Button(toolbar, text="－", command=self._zoom_out,
                  **btn, width=2).pack(side=tk.LEFT, padx=(0, 8), pady=6)
        tk.Button(toolbar, text="⊡  Fit Width", command=self._fit_width,
                  **btn).pack(side=tk.LEFT, pady=6)

        tk.Frame(toolbar, bg="#444", width=1).pack(side=tk.LEFT, fill=tk.Y, pady=8)

        # Info label
        self.info_label = tk.Label(toolbar, text="No manga opened",
                                   bg=TOOLBAR_BG, fg="#999", font=("Segoe UI", 9))
        self.info_label.pack(side=tk.RIGHT, padx=12)

        # ── Body: sidebar + canvas area ───────────────────────
        self.body = tk.Frame(self.root, bg=BG_COLOR)
        self.body.pack(fill=tk.BOTH, expand=True)

        # Floating sidebar (hidden by default)
        self.sidebar_frame = tk.Frame(self.body, bg=SIDEBAR_BG,
                                      width=SIDEBAR_WIDTH)
        # Not packed yet — toggled later

        # Right area: canvas + scrollbar
        self.canvas_area = tk.Frame(self.body, bg=BG_COLOR)
        self.canvas_area.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.scrollbar = ttk.Scrollbar(self.canvas_area, orient=tk.VERTICAL)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.canvas = tk.Canvas(self.canvas_area, bg=BG_COLOR,
                                highlightthickness=0,
                                yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.scrollbar.config(command=self.canvas.yview)

        # Build sidebar contents
        self._build_sidebar()

        # ── Status bar ────────────────────────────────────────
        self.status_var = tk.StringVar(value="Open a manga folder or file to begin.")
        tk.Label(self.root, textvariable=self.status_var,
                 bg=TOOLBAR_BG, fg="#777",
                 font=("Segoe UI", 8), anchor="w", padx=8).pack(side=tk.BOTTOM, fill=tk.X)

    def _build_sidebar(self):
        """Build the chapter list inside the sidebar frame."""
        hdr = tk.Frame(self.sidebar_frame, bg=SIDEBAR_BG)
        hdr.pack(fill=tk.X, padx=6, pady=(10, 4))

        tk.Label(hdr, text="Chapters", bg=SIDEBAR_BG, fg=ACCENT,
                 font=("Segoe UI", 11, "bold")).pack(side=tk.LEFT)

        # Scrollable list
        list_frame = tk.Frame(self.sidebar_frame, bg=SIDEBAR_BG)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        sb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        self.chapter_listbox = tk.Listbox(
            list_frame,
            bg=SIDEBAR_BG, fg=TEXT_COLOR,
            selectbackground=SIDEBAR_SEL, selectforeground="white",
            activestyle="none",
            font=("Segoe UI", 9),
            borderwidth=0, highlightthickness=0,
            yscrollcommand=sb.set,
            relief=tk.FLAT,
        )
        self.chapter_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.config(command=self.chapter_listbox.yview)
        self.chapter_listbox.bind("<<ListboxSelect>>", self._on_chapter_select)

    def _toggle_sidebar(self):
        if self.sidebar_visible:
            self.sidebar_frame.pack_forget()
            self.sidebar_visible = False
        else:
            self.sidebar_frame.pack(side=tk.LEFT, fill=tk.Y, before=self.canvas_area)
            self.sidebar_visible = True

    # ══════════════════════════════════════════════════════════
    #  Open Manga
    # ══════════════════════════════════════════════════════════

    def _open_manga_folder(self):
        folder = filedialog.askdirectory(title="Select Manga Folder")
        if not folder:
            return

        chapters = get_chapter_folders(folder)
        if not chapters:
            messagebox.showinfo("No images", "No image files found in that folder.")
            return

        self.source_type = "folder"
        self.source_file = ""
        self.chapters    = chapters
        self._populate_chapter_list()

        # Open first chapter
        self._load_chapter(0)

    def _open_manga_file(self):
        filetypes = [
            ("Manga files", "*.cbz *.pdf"),
            ("Comic Book Zip", "*.cbz"),
            ("PDF files", "*.pdf"),
            ("All files", "*.*"),
        ]
        path = filedialog.askopenfilename(title="Select Manga File",
                                          filetypes=filetypes)
        if not path:
            return

        ext = os.path.splitext(path)[1].lower()
        if ext == ".cbz":
            self.source_type = "cbz"
        elif ext == ".pdf":
            self.source_type = "pdf"
        else:
            messagebox.showerror("Unsupported", f"Unsupported file type: {ext}")
            return

        self.source_file = path
        self.chapters    = [path]          # single "chapter" = the file
        self._populate_chapter_list()
        self._load_chapter(0)

    def _populate_chapter_list(self):
        self.chapter_listbox.delete(0, tk.END)
        for ch in self.chapters:
            self.chapter_listbox.insert(tk.END, "  " + os.path.basename(ch))
        # Auto-show sidebar when multiple chapters exist
        if len(self.chapters) > 1 and not self.sidebar_visible:
            self._toggle_sidebar()

    # ══════════════════════════════════════════════════════════
    #  Chapter Loading
    # ══════════════════════════════════════════════════════════

    def _load_chapter(self, idx):
        if idx < 0 or idx >= len(self.chapters):
            return

        self.current_chap = idx
        self.chapter_listbox.selection_clear(0, tk.END)
        self.chapter_listbox.selection_set(idx)
        self.chapter_listbox.see(idx)

        # Clear previous data
        self.image_paths = []
        self.cbz_data    = {}
        self.pdf_images  = {}

        target = self.chapters[idx]

        if self.source_type == "cbz":
            raw_list = load_images_from_cbz(target)
            self.image_paths = [name for name, _ in raw_list]
            self.cbz_data    = {i: data for i, (_, data) in enumerate(raw_list)}
        elif self.source_type == "pdf":
            raw_list = load_images_from_pdf(target)
            self.image_paths = [name for name, _ in raw_list]
            self.pdf_images  = {i: img for i, (_, img) in enumerate(raw_list)}
        else:
            self.image_paths = get_image_files_from_folder(target)

        if not self.image_paths:
            self.status_var.set("No images found in this chapter.")
            return

        count       = len(self.image_paths)
        chap_name   = os.path.basename(target)
        self.info_label.config(text=f"{chap_name}  —  {count} images")
        self.status_var.set(f"Loaded {count} images  |  chapter: {chap_name}")
        self.root.title(f"ToReader – {chap_name}")

        self._reset_canvas()
        self._build_slots()
        self._on_scroll_changed()
        self.canvas.yview_moveto(0)

    def _on_chapter_select(self, event):
        sel = self.chapter_listbox.curselection()
        if sel:
            self._load_chapter(sel[0])

    # ── Next / Previous chapter (called from canvas buttons) ──

    def _next_chapter(self):
        if self.current_chap < len(self.chapters) - 1:
            self._load_chapter(self.current_chap + 1)
        else:
            self.status_var.set("Already at the last chapter.")

    def _prev_chapter(self):
        if self.current_chap > 0:
            self._load_chapter(self.current_chap - 1)
        else:
            self.status_var.set("Already at the first chapter.")

    # ══════════════════════════════════════════════════════════
    #  Canvas / Slot Management
    # ══════════════════════════════════════════════════════════

    def _reset_canvas(self):
        self.canvas.delete("all")
        self.loaded.clear()
        self.canvas_items.clear()
        self.slot_y.clear()
        self.slot_h.clear()

    def _build_slots(self):
        canvas_w   = max(self.canvas.winfo_width(), 600)
        display_w  = int(self.base_width * self.zoom)
        x_offset   = max((canvas_w - display_w) // 2, 0)
        GAP        = 12
        y_cursor   = 10

        for i, path in enumerate(self.image_paths):
            est_h = self._estimate_height(i, display_w)

            self.slot_y.append(y_cursor)
            self.slot_h.append(est_h)

            self.canvas.create_rectangle(
                x_offset, y_cursor,
                x_offset + display_w, y_cursor + est_h,
                fill=PLACEHOLDER_COLOR, outline="#3a3a3a", tags=f"slot_{i}"
            )
            self.canvas.create_text(
                x_offset + display_w // 2, y_cursor + est_h // 2,
                text=f"{i + 1} / {len(self.image_paths)}",
                fill="#555", font=("Segoe UI", 12), tags=f"slot_{i}"
            )
            y_cursor += est_h + GAP

        # ── Chapter navigation buttons at bottom ─────────────
        btn_y     = y_cursor + 10
        btn_h     = 44
        btn_w     = 160
        center_x  = canvas_w // 2

        has_prev = self.current_chap > 0
        has_next = self.current_chap < len(self.chapters) - 1

        if has_prev:
            self._draw_nav_button(center_x - btn_w - 16, btn_y,
                                  btn_w, btn_h, "◀  Prev Chapter",
                                  self._prev_chapter, "prev_btn")
        if has_next:
            self._draw_nav_button(center_x + 16, btn_y,
                                  btn_w, btn_h, "Next Chapter  ▶",
                                  self._next_chapter, "next_btn")

        # If only one direction is available, center it
        if has_prev and not has_next:
            # re-center the single prev button
            self.canvas.delete("prev_btn")
            self._draw_nav_button(center_x - btn_w // 2, btn_y,
                                  btn_w, btn_h, "◀  Prev Chapter",
                                  self._prev_chapter, "prev_btn")
        elif has_next and not has_prev:
            self.canvas.delete("next_btn")
            self._draw_nav_button(center_x - btn_w // 2, btn_y,
                                  btn_w, btn_h, "Next Chapter  ▶",
                                  self._next_chapter, "next_btn")

        total_h = btn_y + btn_h + 20 if (has_prev or has_next) else y_cursor + 20
        self.canvas.config(scrollregion=(0, 0, canvas_w, total_h))

    def _draw_nav_button(self, x, y, w, h, label, command, tag):
        """Draw a styled button on the canvas."""
        rx, ry = x, y
        rect = self.canvas.create_rectangle(
            rx, ry, rx + w, ry + h,
            fill="#EC693D", outline="#EC693D", width=2,
            tags=(tag,)
        )
        text = self.canvas.create_text(
            rx + w // 2, ry + h // 2,
            text=label, fill="white",
            font=("Segoe UI", 10, "bold"),
            tags=(tag,)
        )

        def on_enter(e):
            self.canvas.itemconfig(rect, fill="#EC693D")
        def on_leave(e):
            self.canvas.itemconfig(rect, fill="#EC693D")
        def on_click(e):
            command()

        for item in (rect, text):
            self.canvas.tag_bind(item, "<Enter>",    on_enter)
            self.canvas.tag_bind(item, "<Leave>",    on_leave)
            self.canvas.tag_bind(item, "<Button-1>", on_click)
            self.canvas.tag_bind(item, "<ButtonRelease-1>", on_click)

        self.canvas.config(cursor="hand2")

    def _estimate_height(self, idx, display_w):
        """Fast height estimate — reads image header only."""
        try:
            if self.source_type == "cbz" and idx in self.cbz_data:
                with Image.open(io.BytesIO(self.cbz_data[idx])) as img:
                    w, h = img.size
            elif self.source_type == "pdf" and idx in self.pdf_images:
                w, h = self.pdf_images[idx].size
            else:
                with Image.open(self.image_paths[idx]) as img:
                    w, h = img.size
            ratio = h / w if w > 0 else 1.4
            return max(int(display_w * ratio), 50)
        except Exception:
            return int(display_w * 1.4)

    # ══════════════════════════════════════════════════════════
    #  Lazy Loading
    # ══════════════════════════════════════════════════════════

    def _on_scroll_changed(self, *_):
        visible = self._get_visible_indices()
        if not visible:
            return
        center     = visible[len(visible) // 2]
        load_range = range(
            max(0, center - PRELOAD_RADIUS),
            min(len(self.image_paths), center + PRELOAD_RADIUS + 1)
        )
        for idx in list(self.loaded.keys()):
            if idx not in load_range:
                self._unload_image(idx)
        for idx in load_range:
            if idx not in self.loaded:
                self._load_image_async(idx)

    def _get_visible_indices(self):
        if not self.slot_y:
            return []
        try:
            top_frac, bot_frac = self.canvas.yview()
        except Exception:
            return []
        region = self.canvas.cget("scrollregion")
        if not region:
            return []
        total_h = float(str(region).split()[-1])
        top_px  = top_frac * total_h
        bot_px  = bot_frac * total_h
        return [i for i, (sy, sh) in enumerate(zip(self.slot_y, self.slot_h))
                if sy + sh >= top_px and sy <= bot_px]

    def _load_image_async(self, idx):
        def _worker():
            try:
                display_w = int(self.base_width * self.zoom)

                if self.source_type == "cbz" and idx in self.cbz_data:
                    img = Image.open(io.BytesIO(self.cbz_data[idx]))
                elif self.source_type == "pdf" and idx in self.pdf_images:
                    img = self.pdf_images[idx].copy()
                else:
                    img = Image.open(self.image_paths[idx])

                img = img.convert("RGBA") if img.mode in ("RGBA", "P") else img.convert("RGB")
                ow, oh    = img.size
                ratio     = oh / ow if ow > 0 else 1
                display_h = int(display_w * ratio)
                img       = img.resize((display_w, display_h), Image.LANCZOS)
                photo     = ImageTk.PhotoImage(img)

                self.root.after(0, lambda: self._place_image(idx, photo, display_w, display_h))
            except Exception as e:
                self.root.after(0, lambda: self.status_var.set(
                    f"Error loading image {idx+1}: {e}"))

        threading.Thread(target=_worker, daemon=True).start()

    def _place_image(self, idx, photo, display_w, display_h):
        if idx >= len(self.slot_y):
            return
        canvas_w = max(self.canvas.winfo_width(), 600)
        x_offset = max((canvas_w - display_w) // 2, 0)
        y        = self.slot_y[idx]

        self.canvas.delete(f"slot_{idx}")
        item_id           = self.canvas.create_image(x_offset, y, anchor=tk.NW, image=photo)
        self.loaded[idx]  = photo
        self.canvas_items[idx] = item_id

        name = (os.path.basename(self.image_paths[idx])
                if self.source_type == "folder"
                else f"page {idx+1}")
        self.status_var.set(f"Viewing: {name}  ({idx+1} / {len(self.image_paths)})")

    def _unload_image(self, idx):
        if idx in self.canvas_items:
            self.canvas.delete(self.canvas_items.pop(idx))
        if idx in self.loaded:
            del self.loaded[idx]
        if idx < len(self.slot_y):
            canvas_w  = max(self.canvas.winfo_width(), 600)
            display_w = int(self.base_width * self.zoom)
            x_offset  = max((canvas_w - display_w) // 2, 0)
            y, h      = self.slot_y[idx], self.slot_h[idx]
            self.canvas.create_rectangle(
                x_offset, y, x_offset + display_w, y + h,
                fill=PLACEHOLDER_COLOR, outline="#3a3a3a", tags=f"slot_{idx}"
            )
            self.canvas.create_text(
                x_offset + display_w // 2, y + h // 2,
                text=f"{idx+1} / {len(self.image_paths)}",
                fill="#555", font=("Segoe UI", 12), tags=f"slot_{idx}"
            )

    # ══════════════════════════════════════════════════════════
    #  Events
    # ══════════════════════════════════════════════════════════

    def _bind_events(self):
        self.canvas.bind("<Configure>",  self._on_resize)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Button-4>",   self._on_mousewheel)
        self.canvas.bind("<Button-5>",   self._on_mousewheel)
        self.root.bind("<Control-equal>", lambda e: self._zoom_in())
        self.root.bind("<Control-plus>",  lambda e: self._zoom_in())
        self.root.bind("<Control-minus>", lambda e: self._zoom_out())

    def _on_mousewheel(self, event):
        if event.num == 4:
            self.canvas.yview_scroll(-2, "units")
        elif event.num == 5:
            self.canvas.yview_scroll(2, "units")
        else:
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        self._on_scroll_changed()

    def _on_resize(self, event):
        if self.image_paths:
            self._rebuild_layout()

    # ══════════════════════════════════════════════════════════
    #  Zoom
    # ══════════════════════════════════════════════════════════

    def _zoom_in(self):
        if self.zoom < 4.0:
            self.zoom = round(min(self.zoom + 0.25, 4.0), 2)
            self._apply_zoom()

    def _zoom_out(self):
        if self.zoom > 0.25:
            self.zoom = round(max(self.zoom - 0.25, 0.25), 2)
            self._apply_zoom()

    def _fit_width(self):
        self.zoom = 1.0
        self._apply_zoom()

    def _apply_zoom(self):
        self.zoom_label.config(text=f"{int(self.zoom * 100)}%")
        if self.image_paths:
            self._rebuild_layout()

    def _rebuild_layout(self):
        self.canvas.delete("all")
        self.loaded.clear()
        self.canvas_items.clear()
        self.slot_y.clear()
        self.slot_h.clear()
        self._build_slots()
        self._on_scroll_changed()


# ─────────────────────────────────────────────
#  Entry Point
# ─────────────────────────────────────────────

if __name__ == "__main__":
    root = tk.Tk()
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    app = ToReader(root)
    root.mainloop()