from __future__ import annotations

import tkinter as tk
from typing import Optional


class BubbleWindow(tk.Toplevel):
    def __init__(self, master: tk.Tk, transparent_color: str):
        super().__init__(master)
        self.transparent_color = transparent_color
        self.withdraw()
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=transparent_color)
        try:
            self.wm_attributes("-transparentcolor", transparent_color)
        except tk.TclError:
            pass
        self.canvas = tk.Canvas(self, width=240, height=92, bg=transparent_color, highlightthickness=0, bd=0)
        self.canvas.pack()
        self.after_id: Optional[str] = None
        self.anchor: Optional[tk.Toplevel] = None

    def show_near(self, anchor: tk.Toplevel, text: str, duration_ms: int = 4500) -> None:
        if not text.strip() or not anchor.winfo_ismapped():
            return
        if self.after_id:
            self.after_cancel(self.after_id)
            self.after_id = None
        self.anchor = anchor
        self.canvas.delete("all")
        self.draw_cloud(text.strip())
        self.follow_anchor()
        self.deiconify()
        self.lift()
        self.after_id = self.after(duration_ms, self.hide)

    def hide(self) -> None:
        self.anchor = None
        self.withdraw()

    def follow_anchor(self) -> None:
        anchor = self.anchor
        if anchor is None or not anchor.winfo_ismapped():
            return
        anchor.update_idletasks()
        bubble_w, bubble_h = 240, 92
        screen_w = self.winfo_screenwidth()
        x = anchor.winfo_x() + max(0, (anchor.winfo_width() - bubble_w) // 2)
        y = anchor.winfo_y() - bubble_h - 8
        if y < 0:
            y = anchor.winfo_y() + anchor.winfo_height() + 8
        x = max(0, min(x, screen_w - bubble_w))
        self.geometry(f"{bubble_w}x{bubble_h}+{x}+{y}")

    def draw_cloud(self, text: str) -> None:
        fill = "#ffffff"
        outline = "#b9c7d8"
        for oval in [(10, 22, 62, 74), (38, 8, 112, 78), (88, 14, 166, 80), (148, 24, 226, 78)]:
            self.canvas.create_oval(*oval, fill=fill, outline=outline, width=2)
        self.canvas.create_rectangle(40, 38, 202, 76, fill=fill, outline=fill)
        self.canvas.create_oval(172, 70, 190, 86, fill=fill, outline=outline, width=2)
        self.canvas.create_oval(196, 82, 206, 92, fill=fill, outline=outline, width=1)
        self.canvas.create_text(
            120,
            48,
            text=text,
            fill="#27313f",
            width=190,
            justify="center",
            font=("Microsoft YaHei UI", 10),
        )
