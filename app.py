from __future__ import annotations

import json
import math
import os
import re
import runpy
import secrets
import shutil
import subprocess
import sys
import threading
import traceback
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, ttk
except ModuleNotFoundError as exc:
    if exc.name == "tkinter":
        print("当前 Python 没有 tkinter，桌面窗口需要它才能运行。")
        print("请改用带 Tcl/Tk 的标准 Python 环境。")
        raise SystemExit(1) from exc
    raise

from PIL import Image, ImageTk
import pystray
from pystray import MenuItem as TrayMenuItem


TRANSPARENT_COLOR = "#ff00ff"
MIN_DISPLAY_SCALE = 0.1
MAX_DISPLAY_SCALE = 3.0
NEW_GENERATION_LABEL = "新任务"


def get_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def get_resource_dir() -> Path:
    return Path(getattr(sys, "_MEIPASS", get_app_dir())).resolve()


APP_DIR = get_app_dir()
RESOURCE_DIR = get_resource_dir()
CONFIG_DIR = APP_DIR / "config"
PETS_DIR = APP_DIR / "pets"
OUTPUT_DIR = APP_DIR / "output"
APP_CONFIG = CONFIG_DIR / "app_config.json"
LEGACY_APP_CONFIG = APP_DIR / "app_config.json"
DEFAULT_GENERATION_PROMPT = "生成一个开心的小猫桌宠，会待机、挥手、跳起来。"
INTERACTION_SPECS = [
    ("idle", "待机", "idle"),
    ("hover", "摸摸", "wave"),
    ("drag_left", "左拖", "move-left"),
    ("drag_right", "右拖", "move-right"),
    ("drag_up", "拎起", "lift"),
    ("click", "点击", "idle"),
    ("play", "玩耍", "play"),
]
DEFAULT_INTERACTION_BINDINGS = {key: action for key, _label, action in INTERACTION_SPECS}
BASIC_GENERATION_ACTIONS = ["idle", "wave", "move-left", "move-right", "lift", "play"]
ACTION_DISPLAY_NAMES = {
    "idle": "待机",
    "wave": "挥手",
    "move-left": "向左移动",
    "move-right": "向右移动",
    "lift": "拎起",
    "play": "玩耍",
    "jump": "跳跃",
    "cheer": "开心",
    "think": "思考",
    "work": "工作",
    "focus": "专注",
}


@dataclass
class ActionConfig:
    id: str
    name: str
    row: int
    frames: int
    fps: float = 8.0
    loop: bool = True


@dataclass
class SpriteConfig:
    image: str
    cell_width: int
    cell_height: int
    actions: list[ActionConfig]
    anchor_x: int = 0
    anchor_y: int = 0
    scale: float = 1.0
    nickname: str = ""
    interaction_bindings: dict[str, str] | None = None


def remove_green_matte(img: Image.Image) -> Image.Image:
    """减少半透明边缘的绿色溢色。"""
    rgba = img.convert("RGBA")
    pixels = rgba.load()
    for y in range(rgba.height):
        for x in range(rgba.width):
            r, g, b, a = pixels[x, y]
            if 0 < a < 255 and g > r and g > b:
                pixels[x, y] = (r, min(r, b), b, a)
    return rgba


def remove_purple_fringe(img: Image.Image) -> Image.Image:
    """减少半透明边缘的紫色/洋红溢色。"""
    rgba = img.convert("RGBA")
    pixels = rgba.load()
    for y in range(rgba.height):
        for x in range(rgba.width):
            r, g, b, a = pixels[x, y]
            if 0 < a < 255 and r > g and b > g:
                pixels[x, y] = (r, max(r, b), b, a)
    return rgba


def harden_alpha(img: Image.Image, threshold: int = 18) -> Image.Image:
    """Tk 色键透明不是逐像素 alpha；播放前压掉低透明边缘，避免紫边。"""
    rgba = img.convert("RGBA")
    pixels = rgba.load()
    for y in range(rgba.height):
        for x in range(rgba.width):
            r, g, b, a = pixels[x, y]
            if a < threshold:
                pixels[x, y] = (0, 0, 0, 0)
            else:
                pixels[x, y] = (r, g, b, 255)
    return rgba


def prepare_display_frame(img: Image.Image) -> Image.Image:
    frame = remove_green_matte(img)
    frame = remove_purple_fringe(frame)
    return harden_alpha(frame)


class PetWindow(tk.Toplevel):
    def __init__(self, master: tk.Tk):
        super().__init__(master)
        self.withdraw()
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=TRANSPARENT_COLOR)
        try:
            self.wm_attributes("-transparentcolor", TRANSPARENT_COLOR)
        except tk.TclError:
            pass

        self.label = tk.Label(self, bg=TRANSPARENT_COLOR, bd=0, highlightthickness=0)
        self.label.pack()

        self.tk_frames: list[ImageTk.PhotoImage] = []
        self.index = 0
        self.after_id: Optional[str] = None
        self.fps = 8.0
        self.loop = True
        self.drag_start = (0, 0)
        self.drag_origin = (0, 0)
        self.drag_last_pointer = (0, 0)
        self.drag_moved = False
        self.drag_interaction = ""
        self.drag_button_down = False
        self.interaction_handler = None

        self.label.bind("<ButtonPress-1>", self._start_drag)
        self.label.bind("<B1-Motion>", self._drag)
        self.label.bind("<ButtonRelease-1>", self._end_drag)
        self.label.bind("<Enter>", self._enter)
        self.label.bind("<Button-3>", self._show_menu)

        self.menu = tk.Menu(self, tearoff=False)
        self.menu.add_command(label="隐藏", command=self.withdraw)
        self.menu.add_command(label="关闭", command=self.destroy)

    def _start_drag(self, event: tk.Event) -> None:
        self.drag_start = (event.x, event.y)
        self.drag_origin = (self.winfo_pointerx(), self.winfo_pointery())
        self.drag_last_pointer = self.drag_origin
        self.drag_moved = False
        self.drag_interaction = ""
        self.drag_button_down = True

    def _drag(self, _event: tk.Event) -> None:
        pointer_x = self.winfo_pointerx()
        pointer_y = self.winfo_pointery()
        x = pointer_x - self.drag_start[0]
        y = pointer_y - self.drag_start[1]
        self.geometry(f"+{x}+{y}")
        dx = pointer_x - self.drag_origin[0]
        dy = pointer_y - self.drag_origin[1]
        step_dx = pointer_x - self.drag_last_pointer[0]
        step_dy = pointer_y - self.drag_last_pointer[1]
        self.drag_last_pointer = (pointer_x, pointer_y)
        if abs(dx) < 12 and abs(dy) < 12:
            return
        self.drag_moved = True

        interaction = self.drag_interaction
        if abs(step_dy) >= 4 and step_dy < 0 and abs(step_dy) >= abs(step_dx):
            interaction = "drag_up"
        elif abs(step_dx) >= 4 and step_dx < 0:
            interaction = "drag_left"
        elif abs(step_dx) >= 4 and step_dx > 0:
            interaction = "drag_right"
        elif not interaction and dy < -18 and abs(dy) >= abs(dx):
            interaction = "drag_up"
        elif not interaction and dx < -18:
            interaction = "drag_left"
        elif not interaction and dx > 18:
            interaction = "drag_right"
        else:
            return

        if interaction != self.drag_interaction:
            self.drag_interaction = interaction
            self.emit_interaction(interaction)

    def _end_drag(self, _event: tk.Event) -> None:
        self.drag_button_down = False
        if not self.drag_moved:
            self.emit_interaction("click")
        else:
            self.emit_interaction("idle")

    def _enter(self, _event: tk.Event) -> None:
        if not self.drag_button_down:
            self.emit_interaction("hover")

    def _show_menu(self, event: tk.Event) -> None:
        self.menu.tk_popup(event.x_root, event.y_root)

    def set_interaction_handler(self, handler) -> None:
        self.interaction_handler = handler

    def emit_interaction(self, interaction: str) -> None:
        if self.interaction_handler is not None:
            self.interaction_handler(interaction)

    def show_action(self, frames: list[Image.Image], fps: float, loop: bool, scale: float = 1.0) -> None:
        self.stop()
        self.index = 0
        self.fps = max(0.1, fps)
        self.loop = loop
        self.tk_frames = [self._to_tk(frame, scale) for frame in frames]
        if not self.tk_frames:
            return
        self.deiconify()
        self._tick()

    def stop(self) -> None:
        if self.after_id:
            self.after_cancel(self.after_id)
            self.after_id = None

    def _to_tk(self, img: Image.Image, scale: float) -> ImageTk.PhotoImage:
        rgba = prepare_display_frame(img)
        if scale != 1.0:
            size = (max(1, round(rgba.width * scale)), max(1, round(rgba.height * scale)))
            rgba = rgba.resize(size, Image.Resampling.NEAREST)
        return ImageTk.PhotoImage(rgba)

    def _tick(self) -> None:
        if not self.tk_frames:
            return
        self.label.configure(image=self.tk_frames[self.index])
        self.index += 1
        if self.index >= len(self.tk_frames):
            if not self.loop:
                self.index = len(self.tk_frames) - 1
                return
            self.index = 0
        delay = max(16, round(1000 / self.fps))
        self.after_id = self.after(delay, self._tick)


class ScrollableFrame(ttk.Frame):
    def __init__(self, parent: tk.Widget):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.body = ttk.Frame(self.canvas)
        self.window_id = self.canvas.create_window((0, 0), window=self.body, anchor="nw")

        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        self.body.bind("<Configure>", self._update_scroll_region)
        self.canvas.bind("<Configure>", self._resize_body)
        self.canvas.bind("<Enter>", self._bind_mousewheel)
        self.canvas.bind("<Leave>", self._unbind_mousewheel)

    def _update_scroll_region(self, _event: tk.Event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _resize_body(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self.window_id, width=event.width)

    def _bind_mousewheel(self, _event: tk.Event) -> None:
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _unbind_mousewheel(self, _event: tk.Event) -> None:
        self.canvas.unbind_all("<MouseWheel>")

    def _on_mousewheel(self, event: tk.Event) -> None:
        self.canvas.yview_scroll(int(-event.delta / 120), "units")


class DesktopPetApp:
    def __init__(self, root: tk.Tk):
        self.ensure_app_dirs()
        self.root = root
        self.root.title("桌宠精灵图播放器")
        self.root.geometry("980x720")
        self.root.minsize(820, 560)

        self.image_path: Optional[Path] = None
        self.json_path: Optional[Path] = None
        self.sheet: Optional[Image.Image] = None
        self.config: Optional[SpriteConfig] = None
        self.pet = PetWindow(root)
        self.pet.set_interaction_handler(self.play_interaction)
        self.app_config = self.load_app_config()

        self.cell_width = tk.IntVar(value=192)
        self.cell_height = tk.IntVar(value=208)
        self.cols = tk.IntVar(value=1)
        self.rows = tk.IntVar(value=1)
        self.scale = tk.DoubleVar(value=1.0)
        self.default_fps = tk.DoubleVar(value=8.0)
        self.scale_refresh_after_id: Optional[str] = None
        self.gen_api_key = tk.StringVar(value=self.app_config.get("api_key", os.getenv("OPENAI_API_KEY", "")))
        self.gen_base_url = tk.StringVar(value=self.app_config.get("base_url", os.getenv("OPENAI_BASE_URL", "")))
        self.gen_model = tk.StringVar(value=self.app_config.get("model", "gpt-image-2"))
        self.gen_quality = tk.StringVar(value="medium")
        self.gen_references = [Path(path) for path in self.app_config.get("reference_images", []) if str(path).strip()]
        self.gen_reference_label = tk.StringVar(value="")
        self.gen_reference_preview_frame: Optional[ttk.Frame] = None
        self.gen_reference_preview_refs: list[ImageTk.PhotoImage] = []
        self.gen_history_var = tk.StringVar(value="")
        self.gen_history_options: dict[str, Path] = {}
        self.gen_running = False
        self.gen_log: Optional[scrolledtext.ScrolledText] = None
        self.gen_prompt_text: Optional[scrolledtext.ScrolledText] = None
        self.current_run_dir: Optional[Path] = None
        self.pet_folders: list[Path] = []
        self.selected_pet_folder: Optional[Path] = None
        self.pet_library_frame: Optional[ttk.Frame] = None
        self.pet_row_widgets: dict[Path, tk.Widget] = {}
        self.pet_thumbnail_refs: list[ImageTk.PhotoImage] = []
        self.pet_nickname = tk.StringVar(value="")
        self.binding_vars = {key: tk.StringVar(value=default) for key, _label, default in INTERACTION_SPECS}
        self.binding_combos: list[ttk.Combobox] = []
        self.playful_after_id: Optional[str] = None
        self.action_token = 0
        self.current_action_id = ""
        self.current_force_loop = False
        self.pet_position_applied = False
        self.tray_icon = None
        self.is_quitting = False

        self._build_ui()
        self.setup_window_behavior()
        self.setup_tray_icon()
        self.refresh_generation_history()
        self.refresh_pet_library()
        self.restore_last_pet()

    def ensure_app_dirs(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        PETS_DIR.mkdir(parents=True, exist_ok=True)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    def setup_window_behavior(self) -> None:
        self.root.protocol("WM_DELETE_WINDOW", self.hide_main_window)
        self.root.bind("<Unmap>", self._handle_minimize)

    def _handle_minimize(self, _event: tk.Event) -> None:
        if self.is_quitting:
            return
        if self.root.state() == "iconic":
            self.root.after(0, self.hide_main_window)

    def hide_main_window(self) -> None:
        self.root.withdraw()

    def show_main_window(self) -> None:
        self.root.after(0, self._show_main_window_on_ui_thread)

    def _show_main_window_on_ui_thread(self) -> None:
        self.root.deiconify()
        self.root.state("normal")
        self.root.lift()
        self.root.focus_force()

    def setup_tray_icon(self) -> None:
        menu = pystray.Menu(
            TrayMenuItem("显示主窗口", lambda _icon, _item: self.show_main_window()),
            TrayMenuItem("显示桌宠", lambda _icon, _item: self.root.after(0, self.play_selected)),
            TrayMenuItem("隐藏桌宠", lambda _icon, _item: self.root.after(0, self.hide_pet)),
            TrayMenuItem("退出", lambda _icon, _item: self.root.after(0, self.quit_app)),
        )
        self.tray_icon = pystray.Icon("DesktopPet", self.make_tray_image(), "桌宠", menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def make_tray_image(self) -> Image.Image:
        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        pixels = image.load()
        for y in range(64):
            for x in range(64):
                dx = x - 32
                dy = y - 32
                if dx * dx + dy * dy <= 28 * 28:
                    pixels[x, y] = (80, 180, 150, 255)
                if (x - 23) ** 2 + (y - 27) ** 2 <= 4 * 4 or (x - 41) ** 2 + (y - 27) ** 2 <= 4 * 4:
                    pixels[x, y] = (20, 35, 35, 255)
                if 24 <= x <= 40 and 41 <= y <= 45:
                    pixels[x, y] = (20, 35, 35, 255)
        return image

    def quit_app(self) -> None:
        self.is_quitting = True
        self.save_current_pet_state(enabled=bool(self.pet.winfo_ismapped()))
        if self.playful_after_id:
            self.root.after_cancel(self.playful_after_id)
            self.playful_after_id = None
        if self.tray_icon is not None:
            self.tray_icon.stop()
            self.tray_icon = None
        self.pet.stop()
        self.pet.destroy()
        self.root.destroy()

    def _build_ui(self) -> None:
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True)

        player_tab = ttk.Frame(notebook)
        generator_tab = ttk.Frame(notebook)
        notebook.add(player_tab, text="播放")
        notebook.add(generator_tab, text="生成")

        player_scroll = ScrollableFrame(player_tab)
        player_scroll.pack(fill="both", expand=True)
        self._build_player_tab(player_scroll.body)
        self._build_generator_tab(generator_tab)

        self.status = tk.StringVar(value="请先导入一张 spritesheet 精灵图，或在“生成”页创建一个。")
        ttk.Label(self.root, textvariable=self.status, anchor="w", padding=8).pack(fill="x")

    def _build_player_tab(self, parent: ttk.Frame) -> None:
        toolbar = ttk.Frame(parent, padding=8)
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="导入精灵图", command=self.import_spritesheet).pack(side="left")
        ttk.Button(toolbar, text="加载 JSON", command=self.load_json_dialog).pack(side="left", padx=(6, 0))
        ttk.Button(toolbar, text="保存 JSON", command=self.save_json_dialog).pack(side="left", padx=(6, 0))
        ttk.Button(toolbar, text="显示桌宠", command=self.play_selected).pack(side="left", padx=(16, 0))
        ttk.Button(toolbar, text="隐藏桌宠", command=self.hide_pet).pack(side="left", padx=(6, 0))
        ttk.Button(toolbar, text="刷新宠物库", command=self.refresh_pet_library).pack(side="left", padx=(6, 0))

        settings = ttk.LabelFrame(parent, text="切图网格", padding=8)
        settings.pack(fill="x", padx=8, pady=(0, 8))

        for label, var in [
            ("格宽", self.cell_width),
            ("格高", self.cell_height),
            ("列数", self.cols),
            ("行数", self.rows),
            ("FPS", self.default_fps),
        ]:
            ttk.Label(settings, text=label).pack(side="left")
            ttk.Entry(settings, textvariable=var, width=7).pack(side="left", padx=(3, 10))

        ttk.Label(settings, text="显示大小").pack(side="left")
        self.scale_spinbox = ttk.Spinbox(
            settings,
            textvariable=self.scale,
            from_=MIN_DISPLAY_SCALE,
            to=MAX_DISPLAY_SCALE,
            increment=0.1,
            width=7,
            command=self.apply_display_scale_change,
        )
        self.scale_spinbox.pack(side="left", padx=(3, 10))
        self.scale_spinbox.bind("<KeyRelease>", lambda _event: self.schedule_display_scale_change())
        self.scale_spinbox.bind("<Return>", lambda _event: self.apply_display_scale_change())
        self.scale_spinbox.bind("<FocusOut>", lambda _event: self.apply_display_scale_change())

        ttk.Button(settings, text="按网格重建动作", command=self.rebuild_actions_from_grid).pack(side="left")

        library = ttk.LabelFrame(parent, text="宠物库", padding=8)
        library.pack(fill="x", padx=8, pady=(0, 8))
        self.pet_library_frame = ttk.Frame(library)
        self.pet_library_frame.pack(fill="x")

        pet_settings = ttk.LabelFrame(parent, text="宠物设置与交互绑定", padding=8)
        pet_settings.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Label(pet_settings, text="昵称").grid(row=0, column=0, sticky="w", padx=(0, 4), pady=(0, 6))
        ttk.Entry(pet_settings, textvariable=self.pet_nickname, width=18).grid(row=0, column=1, sticky="w", padx=(0, 12), pady=(0, 6))
        ttk.Button(pet_settings, text="保存绑定", command=self.save_pet_settings).grid(row=0, column=2, sticky="w", pady=(0, 6))

        self.binding_combos = []
        for index, (key, label, _default) in enumerate(INTERACTION_SPECS):
            row = 1 + index // 4
            col = (index % 4) * 2
            ttk.Label(pet_settings, text=label).grid(row=row, column=col, sticky="w", padx=(0, 4), pady=(2, 2))
            combo = ttk.Combobox(pet_settings, textvariable=self.binding_vars[key], state="readonly", width=13)
            combo.grid(row=row, column=col + 1, sticky="w", padx=(0, 14), pady=(2, 2))
            self.binding_combos.append(combo)

        main = ttk.Frame(parent, padding=8)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.columnconfigure(1, weight=0)
        main.columnconfigure(2, weight=0)
        main.rowconfigure(0, weight=1)

        columns = ("id", "name", "row", "frames", "fps", "loop")
        headings = {
            "id": "动作 ID",
            "name": "动作名称",
            "row": "行号",
            "frames": "帧数",
            "fps": "FPS",
            "loop": "循环",
        }
        self.tree = ttk.Treeview(main, columns=columns, show="headings", height=8)
        for col in columns:
            self.tree.heading(col, text=headings[col])
            self.tree.column(col, width=110 if col in {"id", "name"} else 70, anchor="center")
        self.tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll = ttk.Scrollbar(main, orient="vertical", command=self.tree.yview)
        tree_scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=tree_scroll.set)
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self.load_selected_into_editor())
        self.tree.bind("<Double-1>", lambda _event: self.play_selected())

        side = ttk.Frame(main, padding=(8, 0, 0, 0))
        side.grid(row=0, column=2, sticky="ns")

        self.edit_vars = {
            "id": tk.StringVar(),
            "name": tk.StringVar(),
            "row": tk.IntVar(value=0),
            "frames": tk.IntVar(value=1),
            "fps": tk.DoubleVar(value=8.0),
            "loop": tk.BooleanVar(value=True),
        }
        edit_labels = {
            "id": "动作 ID",
            "name": "动作名称",
            "row": "行号",
            "frames": "帧数",
            "fps": "FPS",
        }
        for key in ["id", "name", "row", "frames", "fps"]:
            ttk.Label(side, text=edit_labels[key]).pack(anchor="w")
            ttk.Entry(side, textvariable=self.edit_vars[key], width=24).pack(fill="x", pady=(0, 6))
        ttk.Checkbutton(side, text="循环播放", variable=self.edit_vars["loop"]).pack(anchor="w", pady=(0, 8))
        ttk.Button(side, text="应用修改", command=self.apply_edit).pack(fill="x")
        ttk.Button(side, text="播放动作", command=self.play_selected).pack(fill="x", pady=(6, 0))

    def _build_generator_tab(self, parent: ttk.Frame) -> None:
        form = ttk.Frame(parent, padding=12)
        form.pack(fill="both", expand=True)
        form.columnconfigure(0, weight=1)
        form.columnconfigure(1, weight=0)
        form.rowconfigure(0, weight=1)

        left_panel = ttk.Frame(form)
        left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left_panel.columnconfigure(1, weight=1)

        ttk.Label(left_panel, text="一句话描述").grid(row=0, column=0, sticky="nw", pady=4)
        self.gen_prompt_text = scrolledtext.ScrolledText(left_panel, width=72, height=5, wrap="word")
        self.gen_prompt_text.grid(row=0, column=1, sticky="ew", pady=4)
        self.gen_prompt_text.insert("1.0", self.app_config.get("last_prompt") or DEFAULT_GENERATION_PROMPT)

        refs = ttk.LabelFrame(form, text="参考图片", padding=6)
        refs.grid(row=0, column=1, sticky="ne")
        refs.configure(width=130)
        ttk.Button(refs, text="选择图片", command=self.select_reference_images).pack(fill="x")
        ttk.Button(refs, text="清空", command=self.clear_reference_images).pack(fill="x", pady=(6, 0))
        ttk.Label(refs, textvariable=self.gen_reference_label, foreground="#555", wraplength=120, justify="left").pack(fill="x", pady=(8, 0))
        self.gen_reference_preview_frame = ttk.Frame(refs)
        self.gen_reference_preview_frame.pack(fill="x", pady=(8, 0))
        self.update_reference_label()

        fields = [
            ("API Key", self.gen_api_key),
            ("API URL", self.gen_base_url),
            ("模型", self.gen_model),
        ]
        for row, (label, var) in enumerate(fields, start=1):
            ttk.Label(left_panel, text=label).grid(row=row, column=0, sticky="w", pady=4)
            entry = ttk.Entry(left_panel, textvariable=var, width=72, show="*" if label == "API Key" else "")
            entry.grid(row=row, column=1, sticky="ew", pady=4)

        buttons = ttk.Frame(left_panel)
        buttons.grid(row=len(fields) + 1, column=0, columnspan=2, sticky="w", pady=(14, 6))
        ttk.Button(buttons, text="开始生成", command=self.start_generation).pack(side="left")
        ttk.Button(buttons, text="加载最近输出结果", command=self.load_generated_result).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="刷新历史", command=self.refresh_generation_history).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="清空日志", command=self.clear_generation_log).pack(side="left", padx=(8, 0))

        history_frame = ttk.Frame(left_panel)
        history_frame.grid(row=len(fields) + 2, column=0, columnspan=2, sticky="ew", pady=(0, 6))
        history_frame.columnconfigure(1, weight=1)
        ttk.Label(history_frame, text="历史任务").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.gen_history_combo = ttk.Combobox(history_frame, textvariable=self.gen_history_var, state="readonly")
        self.gen_history_combo.grid(row=0, column=1, sticky="ew")

        help_text = (
            "例：生成一个开心的小猫桌宠，会待机、挥手、跳起来。"
            "历史任务选择“新任务”会重新生成；选择已有任务再开始会从中断处继续。"
        )
        ttk.Label(left_panel, text=help_text, foreground="#555").grid(row=len(fields) + 3, column=0, columnspan=2, sticky="w")

        log_frame = ttk.LabelFrame(left_panel, text="生成日志", padding=6)
        log_frame.grid(row=len(fields) + 4, column=0, columnspan=2, sticky="nsew", pady=(12, 0))
        left_panel.rowconfigure(len(fields) + 4, weight=1)
        self.gen_log = scrolledtext.ScrolledText(log_frame, height=12, wrap="word")
        self.gen_log.pack(fill="both", expand=True)

    def start_generation(self) -> None:
        if self.gen_running:
            messagebox.showinfo("正在生成", "当前已有生成任务在运行。")
            return
        run_dir = self.selected_history_run_dir()
        if run_dir is None and not self.get_generation_prompt().strip():
            messagebox.showinfo("缺少描述", "请先用一句话描述你想要的桌宠。")
            return
        if run_dir is not None and not (run_dir / "imagegen-jobs.json").exists():
            messagebox.showinfo("无法继续", f"找不到任务状态文件：{run_dir / 'imagegen-jobs.json'}")
            return
        self.save_app_config()
        self.gen_running = True
        self.current_run_dir = run_dir
        self.status.set("正在继续历史任务..." if run_dir is not None else "正在生成动画，请稍等...")
        self.clear_generation_log()
        if run_dir is None:
            self.append_generation_log("开始生成动画...\n")
        else:
            self.append_generation_log(f"继续历史任务：{run_dir}\n")
        thread = threading.Thread(target=self._generation_worker, kwargs={"resume_run_dir": run_dir}, daemon=True)
        thread.start()

    def _generation_worker(self, resume_run_dir: Optional[Path] = None) -> None:
        try:
            runner = RESOURCE_DIR / "generator" / "make-animation-frames" / "scripts" / "run_animation_pipeline.py"
            is_resume = resume_run_dir is not None
            run_dir = resume_run_dir.resolve() if resume_run_dir is not None else (OUTPUT_DIR / self.make_run_id()).resolve()
            self.current_run_dir = run_dir
            self.app_config["last_generation_run"] = str(run_dir)
            prompt = self.get_generation_prompt().strip()
            subject, description, actions = self.parse_generation_prompt(prompt)
            runner_args = [
                "--run-dir", str(run_dir),
                "--model", self.gen_model.get().strip() or "gpt-image-2",
                "--quality", self.gen_quality.get().strip() or "medium",
            ]
            if is_resume:
                runner_args.append("--resume-existing")
            else:
                runner_args.extend(["--subject", subject, "--description", description, "--force"])
            if self.gen_api_key.get().strip():
                runner_args.extend(["--api-key", self.gen_api_key.get().strip()])
            if self.gen_base_url.get().strip():
                runner_args.extend(["--base-url", self.gen_base_url.get().strip()])
            references = [] if is_resume else self.valid_reference_images()
            if not is_resume:
                for reference in references:
                    runner_args.extend(["--reference", str(reference)])
                for action in actions:
                    runner_args.extend(["--action", action])

            self.root.after(0, lambda: self.append_generation_log(f"运行目录：{run_dir}\n"))
            if is_resume:
                self.root.after(0, lambda: self.append_generation_log("模式：继续历史任务，不重新准备 prompt 和动作清单。\n"))
            else:
                self.root.after(0, lambda: self.append_generation_log(f"解析动作：{', '.join(actions)}\n"))
            if references:
                self.root.after(0, lambda: self.append_generation_log("参考图片：\n" + "\n".join(str(path) for path in references) + "\n"))
            if getattr(sys, "frozen", False):
                cmd = [sys.executable, "--run-generator", *runner_args]
                self.root.after(0, lambda: self.append_generation_log("命令：\n" + self._redacted_command(cmd) + "\n\n"))
                self._last_generator_lines = []
                return_code = self._run_generator_in_process(runner, runner_args)
                lines = getattr(self, "_last_generator_lines", [])
            else:
                cmd = [sys.executable, str(runner), *runner_args]
                self.root.after(0, lambda: self.append_generation_log("命令：\n" + self._redacted_command(cmd) + "\n\n"))
                process = subprocess.Popen(
                    cmd,
                    cwd=APP_DIR,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    bufsize=1,
                )
                assert process.stdout is not None
                lines = []
                for line in process.stdout:
                    lines.append(line)
                    self.root.after(0, lambda text=line: self.append_generation_log(text))
                return_code = process.wait()
            if return_code != 0:
                message = "".join(lines[-80:])
                self.root.after(0, lambda: self._generation_failed(message))
                return
            self.root.after(0, lambda: self.append_generation_log("\n生成流程完成，正在加载结果...\n"))
            self.root.after(0, lambda: self.load_generated_result(run_dir))
        except Exception as exc:
            self.root.after(0, lambda: self._generation_failed(str(exc)))

    def get_generation_prompt(self) -> str:
        if self.gen_prompt_text is None:
            return self.app_config.get("last_prompt") or DEFAULT_GENERATION_PROMPT
        return self.gen_prompt_text.get("1.0", "end").strip()

    def select_reference_images(self) -> None:
        paths = filedialog.askopenfilenames(
            title="选择参考图片",
            filetypes=[("图片文件", "*.png *.jpg *.jpeg *.webp"), ("所有文件", "*.*")],
        )
        if not paths:
            return
        self.gen_references = [Path(path) for path in paths]
        self.update_reference_label()
        self.save_app_config()

    def clear_reference_images(self) -> None:
        self.gen_references = []
        self.update_reference_label()
        self.save_app_config()

    def update_reference_label(self) -> None:
        if not self.gen_references:
            self.gen_reference_label.set("未选择")
            self.render_reference_previews()
            return
        names = [path.name for path in self.gen_references[:3]]
        extra = f"\n等 {len(self.gen_references)} 张" if len(self.gen_references) > 3 else ""
        self.gen_reference_label.set("\n".join(names) + extra)
        self.render_reference_previews()

    def render_reference_previews(self) -> None:
        if self.gen_reference_preview_frame is None:
            return
        for child in self.gen_reference_preview_frame.winfo_children():
            child.destroy()
        self.gen_reference_preview_refs = []
        for index, path in enumerate(self.gen_references[:4]):
            try:
                img = Image.open(path).convert("RGBA")
                img.thumbnail((84, 84), Image.Resampling.LANCZOS)
                thumb = Image.new("RGBA", (90, 90), (245, 245, 245, 255))
                thumb.alpha_composite(img, ((90 - img.width) // 2, (90 - img.height) // 2))
                tk_img = ImageTk.PhotoImage(thumb)
                self.gen_reference_preview_refs.append(tk_img)
                label = tk.Label(self.gen_reference_preview_frame, image=tk_img, bd=1, relief="solid", bg="#f5f5f5")
                label.grid(row=index // 2, column=index % 2, padx=3, pady=3)
            except Exception:
                ttk.Label(self.gen_reference_preview_frame, text="无法预览").grid(row=index // 2, column=index % 2, padx=3, pady=3)

    def valid_reference_images(self) -> list[Path]:
        valid = []
        missing = []
        for path in self.gen_references:
            if path.exists() and path.is_file():
                valid.append(path)
            else:
                missing.append(path)
        if missing:
            self.root.after(0, lambda: self.append_generation_log("跳过不存在的参考图片：\n" + "\n".join(str(path) for path in missing) + "\n"))
        return valid

    def make_run_id(self) -> str:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return f"{stamp}-{secrets.token_hex(3)}"

    def _run_generator_in_process(self, runner: Path, args: list[str]) -> int:
        class LogWriter:
            def __init__(self, app: DesktopPetApp):
                self.app = app
                self.buffer = ""
                self.lines: list[str] = []

            def write(self, text: str) -> int:
                self.buffer += text
                while "\n" in self.buffer:
                    line, self.buffer = self.buffer.split("\n", 1)
                    value = line + "\n"
                    self.lines.append(value)
                    self.app.root.after(0, lambda text=value: self.app.append_generation_log(text))
                return len(text)

            def flush(self) -> None:
                if self.buffer:
                    text = self.buffer
                    self.buffer = ""
                    self.lines.append(text)
                    self.app.root.after(0, lambda value=text: self.app.append_generation_log(value))

        old_argv = sys.argv[:]
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        writer = LogWriter(self)
        try:
            sys.argv = [str(runner), *args]
            sys.stdout = writer  # type: ignore[assignment]
            sys.stderr = writer  # type: ignore[assignment]
            runpy.run_path(str(runner), run_name="__main__")
            writer.flush()
            return 0
        except SystemExit as exc:
            code = exc.code
            if code not in (None, 0) and not isinstance(code, int):
                writer.write(f"\n{code}\n")
            writer.flush()
            return int(code) if isinstance(code, int) else 1
        except Exception as exc:
            writer.write(f"\n{exc}\n")
            writer.write(traceback.format_exc())
            writer.flush()
            return 1
        finally:
            self._last_generator_lines = writer.lines[-120:]
            sys.argv = old_argv
            sys.stdout = old_stdout
            sys.stderr = old_stderr

    def _redacted_command(self, cmd: list[str]) -> str:
        safe = []
        skip_secret = False
        for item in cmd:
            if skip_secret:
                safe.append("***")
                skip_secret = False
                continue
            safe.append(item)
            if item == "--api-key":
                skip_secret = True
        return " ".join(f'"{part}"' if " " in part else part for part in safe)

    def append_generation_log(self, text: str) -> None:
        if self.gen_log is None:
            return
        self.gen_log.configure(state="normal")
        self.gen_log.insert("end", text)
        self.gen_log.see("end")
        self.gen_log.configure(state="disabled")

    def clear_generation_log(self) -> None:
        if self.gen_log is None:
            return
        self.gen_log.configure(state="normal")
        self.gen_log.delete("1.0", "end")
        self.gen_log.configure(state="disabled")

    def parse_generation_prompt(self, prompt: str) -> tuple[str, str, list[str]]:
        text = prompt.lower()
        actions = list(BASIC_GENERATION_ACTIONS)
        keyword_actions = [
            (("挥手", "招手", "wave"), "wave"),
            (("跳", "蹦", "jump"), "jump"),
            (("开心", "庆祝", "欢呼", "celebrate", "cheer"), "cheer"),
            (("思考", "想", "think"), "think"),
            (("工作", "打字", "处理", "work"), "work"),
            (("专注", "观察", "盯", "focus"), "focus"),
            (("向右", "右走", "right"), "move-right"),
            (("向左", "左走", "left"), "move-left"),
        ]
        if any(word in text for word in ("全套", "全部动作", "所有动作", "all actions")):
            requested = ["idle", "wave", "jump", "cheer", "think", "work", "focus", "move-right", "move-left", "lift", "play"]
        else:
            requested = []
            for words, action in keyword_actions:
                if any(word in text for word in words):
                    requested.append(action)
        for action in requested:
            if action not in actions:
                actions.append(action)
        interaction_notes = (
            "必须生成桌宠交互所需的基础动作：idle 待机、wave 摸摸/挥手、move-left 左拖、"
            "move-right 右拖、lift 向上拖动拎起、play 无交互时玩耍。点击交互默认绑定 idle；base 只作为内部 canonical base 形象，不是动作。"
            "所有动作必须保持同一个角色身份、比例、配色、材质、轮廓和道具；角色在每个动作格子里的视觉大小必须基本一致，不能有的动作明显变大或变小。"
            "如果提供参考图，只提取主要角色/物体的身份和风格，不要复制参考图里的截图布局、背景、边框、文字、水印、房间、场景或多余人物。"
            "每帧必须是一个完整、清晰、独立的桌宠姿势，纯色可抠除背景，无阴影、无发光、无速度线、无漂浮特效、无文字。"
        )
        return prompt, f"{prompt}\n\n{interaction_notes}", actions

    def _generation_failed(self, message: str) -> None:
        self.gen_running = False
        self.status.set("生成失败。")
        self.append_generation_log("\n生成失败。\n")
        self.refresh_generation_history()
        messagebox.showerror("生成失败", message[-3000:] if message else "未知错误")

    def load_generated_result(self, run_dir: Optional[Path] = None) -> None:
        self.gen_running = False
        run_dir = (run_dir or self.current_run_dir or self.latest_output_run())
        if run_dir is None:
            messagebox.showinfo("没有生成结果", f"找不到生成目录：{OUTPUT_DIR}")
            self.status.set("没有找到生成结果。")
            return
        run_dir = run_dir.resolve()
        sheet = run_dir / "final" / "spritesheet.png"
        if not sheet.exists():
            messagebox.showinfo("没有生成结果", f"找不到：{sheet}")
            self.status.set("没有找到生成结果。")
            return
        metadata = self._build_json_from_generated_run(run_dir)
        json_path = run_dir / "final" / "spritesheet.json"
        json_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
        pet_dir = self.copy_final_to_pet_library(run_dir)
        self.load_pet_folder(pet_dir)
        self.status.set(f"生成完成并已应用：{pet_dir}")
        self.append_generation_log(f"已复制 final 到宠物库：{pet_dir}\n")
        self.save_current_pet_state(enabled=True)
        self.refresh_generation_history()

    def latest_output_run(self) -> Optional[Path]:
        runs = [path for path in OUTPUT_DIR.iterdir() if path.is_dir() and (path / "final" / "spritesheet.png").exists()]
        if not runs:
            return None
        return max(runs, key=lambda path: path.stat().st_mtime)

    def refresh_generation_history(self) -> None:
        combo = getattr(self, "gen_history_combo", None)
        if combo is None:
            return
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        runs = [path for path in OUTPUT_DIR.iterdir() if path.is_dir() and (path / "imagegen-jobs.json").exists()]
        runs.sort(key=lambda path: path.stat().st_mtime, reverse=True)
        self.gen_history_options = {}
        labels = [NEW_GENERATION_LABEL]
        for run_dir in runs:
            label = self.generation_history_label(run_dir)
            labels.append(label)
            self.gen_history_options[label] = run_dir
        combo.configure(values=labels)
        current = self.gen_history_var.get()
        self.gen_history_var.set(current if current in labels else NEW_GENERATION_LABEL)

    def generation_history_label(self, run_dir: Path) -> str:
        complete = 0
        total = 0
        subject = ""
        status = "未完成"
        try:
            manifest = json.loads((run_dir / "imagegen-jobs.json").read_text(encoding="utf-8"))
            jobs = manifest.get("jobs", [])
            total = len(jobs)
            complete = sum(1 for job in jobs if job.get("status") == "complete")
            subject = str(manifest.get("subject") or "")
            if total and complete >= total and (run_dir / "final" / "spritesheet.png").exists():
                status = "已完成"
        except Exception:
            status = "状态异常"
        subject_part = f" | {subject[:24]}" if subject else ""
        return f"{run_dir.name} | {complete}/{total} | {status}{subject_part}"

    def selected_history_run_dir(self) -> Optional[Path]:
        label = self.gen_history_var.get()
        if not label or label == NEW_GENERATION_LABEL:
            return None
        run_dir = self.gen_history_options.get(label)
        if run_dir and run_dir.exists():
            self.app_config["last_generation_run"] = str(run_dir)
            self.save_app_config()
            return run_dir
        self.refresh_generation_history()
        label = self.gen_history_var.get()
        return None if label == NEW_GENERATION_LABEL else self.gen_history_options.get(label)

    def copy_final_to_pet_library(self, run_dir: Path) -> Path:
        pet_dir = PETS_DIR / run_dir.name
        while pet_dir.exists():
            pet_dir = PETS_DIR / f"{run_dir.name}-{secrets.token_hex(2)}"
        final_dir = run_dir / "final"
        shutil.copytree(final_dir, pet_dir)
        return pet_dir

    def make_import_pet_dir(self, source: Path) -> Path:
        base = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff_-]+", "-", source.stem).strip("-_")
        if not base:
            base = "imported-pet"
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        pet_dir = PETS_DIR / f"{base}-{stamp}-{secrets.token_hex(2)}"
        while pet_dir.exists():
            pet_dir = PETS_DIR / f"{base}-{stamp}-{secrets.token_hex(2)}"
        pet_dir.mkdir(parents=True, exist_ok=False)
        return pet_dir

    def _build_json_from_generated_run(self, run_dir: Path) -> dict:
        request = json.loads((run_dir / "animation_request.json").read_text(encoding="utf-8"))
        actions = []
        for row, action in enumerate(request["actions"]):
            actions.append({
                "id": action["id"],
                "name": ACTION_DISPLAY_NAMES.get(action["id"], action.get("display_name", action["id"])),
                "row": row,
                "frames": int(action["frames"]),
                "fps": 8,
                "loop": True,
            })
        return {
            "version": 1,
            "image": "spritesheet.png",
            "cell": {"width": request["cell_width"], "height": request["cell_height"]},
            "nickname": "",
            "interaction_bindings": DEFAULT_INTERACTION_BINDINGS,
            "actions": actions,
            "anchor": {"x": request["cell_width"] // 2, "y": request["cell_height"]},
            "scale": self.clamp_display_scale(),
        }

    def import_spritesheet(self) -> None:
        path = filedialog.askopenfilename(
            title="导入精灵图",
            filetypes=[("图片文件", "*.png *.webp *.gif"), ("所有文件", "*.*")],
        )
        if not path:
            return
        source = Path(path)
        pet_dir = self.make_import_pet_dir(source)
        sheet_path = pet_dir / "spritesheet.png"
        json_path = pet_dir / "spritesheet.json"
        self.sheet = Image.open(source).convert("RGBA")
        self.sheet.save(sheet_path)
        self.image_path = sheet_path
        self.json_path = json_path
        self._infer_grid()
        nearby_json = source.with_suffix(".json")
        if nearby_json.exists():
            self.load_json(nearby_json)
            self.json_path = json_path
            self.save_json(json_path)
        else:
            self.rebuild_actions_from_grid()
            self.save_json(json_path)
        self.refresh_pet_library()
        self.select_pet_folder(pet_dir)
        self.status.set(f"已导入为新宠物：{pet_dir}")
        self.save_current_pet_state(enabled=False)

    def refresh_pet_library(self) -> None:
        self.pet_folders = []
        if PETS_DIR.exists():
            self.pet_folders = [
                path for path in sorted(PETS_DIR.iterdir(), key=lambda item: item.name.lower())
                if path.is_dir() and (path / "spritesheet.png").exists() and (path / "spritesheet.json").exists()
            ]
        self.render_pet_library()
        last_pet_folder = str(self.app_config.get("last_pet_folder", "")).strip()
        if last_pet_folder:
            self.select_pet_folder(Path(last_pet_folder))

    def render_pet_library(self) -> None:
        if self.pet_library_frame is None:
            return
        for child in self.pet_library_frame.winfo_children():
            child.destroy()
        self.pet_row_widgets = {}
        self.pet_thumbnail_refs = []
        if not self.pet_folders:
            ttk.Label(self.pet_library_frame, text="还没有宠物").pack(anchor="w", pady=6)
            return
        for folder in self.pet_folders:
            row = tk.Frame(self.pet_library_frame, bd=0, highlightthickness=1, highlightbackground="#dddddd", bg="#f7f7f7")
            row.pack(fill="x", pady=(0, 6))
            row.columnconfigure(1, weight=1)
            self.pet_row_widgets[folder.resolve()] = row

            thumbnail = self.make_pet_thumbnail(folder)
            self.pet_thumbnail_refs.append(thumbnail)
            image_label = tk.Label(row, image=thumbnail, width=52, height=52, bg="#f7f7f7")
            image_label.grid(row=0, column=0, rowspan=2, padx=6, pady=6)

            name, description = self.pet_card_text(folder)
            name_label = tk.Label(row, text=name, anchor="w", bg="#f7f7f7", fg="#222222")
            name_label.grid(row=0, column=1, sticky="ew", padx=(4, 8), pady=(8, 0))
            desc_label = tk.Label(row, text=description, anchor="w", bg="#f7f7f7", fg="#666666", wraplength=520, justify="left")
            desc_label.grid(row=1, column=1, sticky="ew", padx=(4, 8), pady=(0, 8))

            button = ttk.Button(row, text="选择", width=6, command=lambda item=folder: self.apply_pet_folder(item))
            button.grid(row=0, column=2, rowspan=2, padx=(0, 6), pady=6)

            for widget in (row, image_label, name_label, desc_label):
                widget.bind("<Button-1>", lambda _event, item=folder: self.select_pet_folder(item))
                widget.bind("<Double-1>", lambda _event, item=folder: self.apply_pet_folder(item))

    def pet_card_text(self, folder: Path) -> tuple[str, str]:
        try:
            data = json.loads((folder / "spritesheet.json").read_text(encoding="utf-8"))
        except Exception:
            return folder.name, "无法读取宠物信息。"
        nickname = str(data.get("nickname") or "").strip()
        name = nickname or str(data.get("name") or data.get("title") or folder.name)
        description = str(data.get("description") or "").strip()
        if not description:
            actions = data.get("actions", [])
            action_names = [str(item.get("name") or item.get("id")) for item in actions[:3] if isinstance(item, dict)]
            if action_names:
                suffix = "等" if len(actions) > 3 else ""
                description = f"包含 {len(actions)} 个动作：{'、'.join(action_names)}{suffix}。"
            else:
                description = "Spritesheet 桌宠。"
        return name, description

    def make_pet_thumbnail(self, folder: Path) -> ImageTk.PhotoImage:
        size = (48, 48)
        try:
            data = json.loads((folder / "spritesheet.json").read_text(encoding="utf-8"))
            cell = data.get("cell", {})
            cw = int(cell.get("width", data.get("cell_width", 192)))
            ch = int(cell.get("height", data.get("cell_height", 208)))
            sheet = Image.open(folder / "spritesheet.png").convert("RGBA")
            frame = sheet.crop((0, 0, min(cw, sheet.width), min(ch, sheet.height))).convert("RGBA")
            frame = prepare_display_frame(frame)
            bbox = frame.getchannel("A").getbbox()
            if bbox:
                frame = frame.crop(bbox)
            frame.thumbnail(size, Image.Resampling.LANCZOS)
            thumbnail = Image.new("RGBA", size, (255, 255, 255, 0))
            x = (size[0] - frame.width) // 2
            y = (size[1] - frame.height) // 2
            thumbnail.alpha_composite(frame, (x, y))
            return ImageTk.PhotoImage(thumbnail)
        except Exception:
            fallback = Image.new("RGBA", size, (230, 230, 230, 255))
            return ImageTk.PhotoImage(fallback)

    def select_pet_folder(self, folder: Path) -> None:
        resolved = folder.resolve()
        self.selected_pet_folder = resolved
        for pet_folder, row in self.pet_row_widgets.items():
            if pet_folder == resolved:
                row.configure(bg="#eef4ff", highlightbackground="#6b8cff")
                for child in row.winfo_children():
                    if isinstance(child, tk.Label):
                        child.configure(bg="#eef4ff")
            else:
                row.configure(bg="#f7f7f7", highlightbackground="#dddddd")
                for child in row.winfo_children():
                    if isinstance(child, tk.Label):
                        child.configure(bg="#f7f7f7")

    def apply_selected_pet_folder(self) -> None:
        if self.selected_pet_folder is None:
            messagebox.showinfo("未选择宠物", "请先在宠物库列表中选择一个宠物。")
            return
        self.apply_pet_folder(self.selected_pet_folder)

    def apply_pet_folder(self, folder: Path) -> None:
        self.load_pet_folder(folder)
        self.play_selected()

    def load_pet_folder(self, folder: Path) -> None:
        sheet = folder / "spritesheet.png"
        metadata = folder / "spritesheet.json"
        if not sheet.exists() or not metadata.exists():
            messagebox.showerror("宠物不完整", f"缺少 spritesheet.png 或 spritesheet.json：{folder}")
            return
        self.image_path = sheet
        self.sheet = Image.open(sheet).convert("RGBA")
        self.load_json(metadata)
        self.refresh_pet_library()
        self.select_pet_folder(folder)
        self.status.set(f"已应用宠物：{folder.name}")

    def load_json_dialog(self) -> None:
        path = filedialog.askopenfilename(title="加载精灵图 JSON", filetypes=[("JSON", "*.json")])
        if path:
            self.load_json(Path(path))

    def load_json(self, path: Path) -> None:
        data = json.loads(path.read_text(encoding="utf-8"))
        self.json_path = path

        if "cell" in data:
            cell_width = int(data["cell"]["width"])
            cell_height = int(data["cell"]["height"])
        else:
            cell_width = int(data.get("cell_width", self.cell_width.get()))
            cell_height = int(data.get("cell_height", self.cell_height.get()))

        image_value = data.get("image")
        if image_value and self.image_path is None:
            candidate = (path.parent / image_value).resolve()
            if candidate.exists():
                self.image_path = candidate
                self.sheet = Image.open(candidate).convert("RGBA")

        if self.sheet is None:
            messagebox.showerror("没有图片", "请先加载精灵图图片，或在 JSON 中提供可访问的 image 路径。")
            return

        actions = []
        for idx, item in enumerate(data.get("actions", [])):
            actions.append(
                ActionConfig(
                    id=str(item.get("id", f"row-{idx}")),
                    name=str(item.get("name", item.get("display_name", item.get("id", f"第 {idx} 行")))),
                    row=int(item.get("row", idx)),
                    frames=int(item.get("frames", len(item.get("frame_paths", [])) or self._infer_cols(cell_width))),
                    fps=float(item.get("fps", self.default_fps.get())),
                    loop=bool(item.get("loop", True)),
                )
            )

        self.config = SpriteConfig(
            image=str(self.image_path.name if self.image_path else image_value or ""),
            cell_width=cell_width,
            cell_height=cell_height,
            actions=actions,
            anchor_x=int(data.get("anchor", {}).get("x", cell_width // 2)),
            anchor_y=int(data.get("anchor", {}).get("y", cell_height)),
            scale=self.clamped_display_scale(data.get("scale", self.scale.get())),
            nickname=str(data.get("nickname", "")),
            interaction_bindings=self.normalize_bindings(data.get("interaction_bindings")),
        )
        self.cell_width.set(cell_width)
        self.cell_height.set(cell_height)
        self.cols.set(self._infer_cols(cell_width))
        self.rows.set(max(1, math.ceil(self.sheet.height / cell_height)))
        self.scale.set(self.config.scale)
        self.pet_nickname.set(self.config.nickname)
        self.set_binding_vars(self.config.interaction_bindings)
        self._refresh_tree()
        self.refresh_binding_options()
        self.status.set(f"已加载 JSON：{path}")
        self.save_current_pet_state(enabled=False)

    def clamped_display_scale(self, value: object | None = None) -> float:
        try:
            scale = float(self.scale.get() if value is None else value)
        except (tk.TclError, TypeError, ValueError):
            scale = 1.0
        return min(MAX_DISPLAY_SCALE, max(MIN_DISPLAY_SCALE, scale))

    def clamp_display_scale(self) -> float:
        scale = round(self.clamped_display_scale(), 2)
        try:
            self.scale.set(scale)
        except tk.TclError:
            pass
        if self.config is not None:
            self.config.scale = scale
        return scale

    def schedule_display_scale_change(self) -> None:
        if self.scale_refresh_after_id:
            self.root.after_cancel(self.scale_refresh_after_id)
        self.scale_refresh_after_id = self.root.after(250, self.apply_display_scale_change)

    def apply_display_scale_change(self) -> None:
        self.scale_refresh_after_id = None
        self.clamp_display_scale()
        if self.config is None or self.sheet is None:
            return
        if self.pet.winfo_ismapped() and self.current_action_id:
            action = next((item for item in self.config.actions if item.id == self.current_action_id), None)
            if action is not None:
                self.play_action(action, return_to_idle=False, force_loop=self.current_force_loop)
        else:
            self.save_current_pet_state(enabled=bool(self.pet.winfo_ismapped()))

    def normalize_bindings(self, raw: object) -> dict[str, str]:
        bindings = dict(DEFAULT_INTERACTION_BINDINGS)
        if isinstance(raw, dict):
            for key, value in raw.items():
                if key in bindings and isinstance(value, str) and value.strip():
                    bindings[key] = value.strip()
        return bindings

    def set_binding_vars(self, bindings: Optional[dict[str, str]]) -> None:
        normalized = self.normalize_bindings(bindings)
        for key, _label, _default in INTERACTION_SPECS:
            self.binding_vars[key].set(normalized[key])

    def current_bindings(self) -> dict[str, str]:
        action_ids = [action.id for action in self.config.actions] if self.config and self.config.actions else []
        fallback = action_ids[0] if action_ids else ""
        return {
            key: self.valid_binding_value(self.binding_vars[key].get().strip() or default, action_ids, fallback)
            for key, _label, default in INTERACTION_SPECS
        }

    def valid_binding_value(self, value: str, action_ids: list[str], fallback: str) -> str:
        if not action_ids:
            return value
        return value if value in action_ids else fallback

    def refresh_binding_options(self) -> None:
        action_ids = [action.id for action in self.config.actions] if self.config else []
        if not action_ids:
            action_ids = list(dict.fromkeys(DEFAULT_INTERACTION_BINDINGS.values()))
        fallback = action_ids[0]
        for key, _label, default in INTERACTION_SPECS:
            self.binding_vars[key].set(self.valid_binding_value(self.binding_vars[key].get().strip() or default, action_ids, fallback))
        for combo in self.binding_combos:
            combo.configure(values=action_ids)

    def save_pet_settings(self) -> None:
        if self.config is None or self.json_path is None:
            messagebox.showinfo("没有宠物", "请先加载或选择一个宠物。")
            return
        self.config.nickname = self.pet_nickname.get().strip()
        self.config.interaction_bindings = self.current_bindings()
        self.save_json(self.json_path)
        self.refresh_pet_library()
        if self.image_path:
            self.select_pet_folder(self.image_path.parent)

    def save_json_dialog(self) -> None:
        if self.config is None:
            self.rebuild_actions_from_grid()
        default = self.image_path.with_suffix(".json").name if self.image_path else "spritesheet.json"
        path = filedialog.asksaveasfilename(
            title="保存精灵图 JSON",
            initialfile=default,
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
        )
        if path:
            self.save_json(Path(path))

    def save_json(self, path: Path) -> None:
        if self.config is None:
            self.rebuild_actions_from_grid()
        assert self.config is not None
        data = {
            "version": 1,
            "image": self.image_path.name if self.image_path else self.config.image,
            "cell": {"width": self.config.cell_width, "height": self.config.cell_height},
            "actions": [asdict(action) for action in self.config.actions],
            "anchor": {"x": self.config.anchor_x, "y": self.config.anchor_y},
            "scale": self.clamp_display_scale(),
            "nickname": self.pet_nickname.get().strip(),
            "interaction_bindings": self.current_bindings(),
        }
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        self.status.set(f"已保存：{path}")
        self.save_current_pet_state(enabled=bool(self.pet.winfo_ismapped()))

    def _infer_grid(self) -> None:
        assert self.sheet is not None
        cw, ch = self.cell_width.get(), self.cell_height.get()
        if self.sheet.width % cw != 0 or self.sheet.height % ch != 0:
            cw, ch = self._guess_cell_size(self.sheet)
            self.cell_width.set(cw)
            self.cell_height.set(ch)
        self.cols.set(max(1, self.sheet.width // cw))
        self.rows.set(max(1, self.sheet.height // ch))

    def _guess_cell_size(self, sheet: Image.Image) -> tuple[int, int]:
        common = [(192, 208), (128, 128), (256, 256), (96, 96), (64, 64)]
        for cw, ch in common:
            if sheet.width % cw == 0 and sheet.height % ch == 0:
                return cw, ch
        return sheet.width, sheet.height

    def _infer_cols(self, cell_width: int) -> int:
        if self.sheet is None:
            return self.cols.get()
        return max(1, self.sheet.width // cell_width)

    def rebuild_actions_from_grid(self) -> None:
        if self.sheet is None:
            messagebox.showinfo("没有精灵图", "请先导入一张 spritesheet 精灵图。")
            return
        cw, ch = self.cell_width.get(), self.cell_height.get()
        rows = self.rows.get()
        cols = self.cols.get()
        actions = []
        for row in range(rows):
            frames = self._count_nonempty_frames(row, cols, cw, ch)
            actions.append(
                ActionConfig(
                    id=f"row-{row}",
                    name=f"第 {row} 行",
                    row=row,
                    frames=max(1, frames),
                    fps=self.default_fps.get(),
                    loop=True,
                )
            )
        self.config = SpriteConfig(
            image=self.image_path.name if self.image_path else "",
            cell_width=cw,
            cell_height=ch,
            actions=actions,
            anchor_x=cw // 2,
            anchor_y=ch,
            scale=self.clamp_display_scale(),
            nickname=self.pet_nickname.get().strip(),
            interaction_bindings=self.current_bindings(),
        )
        self._refresh_tree()
        self.refresh_binding_options()

    def _count_nonempty_frames(self, row: int, cols: int, cw: int, ch: int) -> int:
        assert self.sheet is not None
        count = 0
        for col in range(cols):
            box = (col * cw, row * ch, (col + 1) * cw, (row + 1) * ch)
            if box[2] > self.sheet.width or box[3] > self.sheet.height:
                continue
            frame = self.sheet.crop(box)
            if frame.getchannel("A").getbbox() is not None:
                count = col + 1
        return count or cols

    def _refresh_tree(self) -> None:
        self.tree.delete(*self.tree.get_children())
        if self.config is None:
            return
        for action in self.config.actions:
            self.tree.insert(
                "",
                "end",
                iid=action.id,
                values=(action.id, action.name, action.row, action.frames, action.fps, action.loop),
            )

    def selected_action(self) -> Optional[ActionConfig]:
        if self.config is None:
            return None
        selected = self.tree.selection()
        if not selected:
            return self.config.actions[0] if self.config.actions else None
        action_id = selected[0]
        return next((action for action in self.config.actions if action.id == action_id), None)

    def load_selected_into_editor(self) -> None:
        action = self.selected_action()
        if not action:
            return
        self.edit_vars["id"].set(action.id)
        self.edit_vars["name"].set(action.name)
        self.edit_vars["row"].set(action.row)
        self.edit_vars["frames"].set(action.frames)
        self.edit_vars["fps"].set(action.fps)
        self.edit_vars["loop"].set(action.loop)

    def apply_edit(self) -> None:
        if self.config is None:
            return
        selected = self.selected_action()
        if selected is None:
            return
        new_id = self.edit_vars["id"].get().strip() or selected.id
        selected.id = new_id
        selected.name = self.edit_vars["name"].get().strip() or new_id
        selected.row = int(self.edit_vars["row"].get())
        selected.frames = max(1, int(self.edit_vars["frames"].get()))
        selected.fps = max(0.1, float(self.edit_vars["fps"].get()))
        selected.loop = bool(self.edit_vars["loop"].get())
        self.config.cell_width = self.cell_width.get()
        self.config.cell_height = self.cell_height.get()
        self.config.scale = self.clamp_display_scale()
        self.config.nickname = self.pet_nickname.get().strip()
        self.config.interaction_bindings = self.current_bindings()
        self._refresh_tree()
        self.refresh_binding_options()
        if self.tree.exists(new_id):
            self.tree.selection_set(new_id)

    def play_selected(self) -> None:
        if self.sheet is None or self.config is None:
            messagebox.showinfo("没有精灵图", "请先导入一张 spritesheet 精灵图。")
            return
        action = self.selected_action()
        if action is None:
            return
        self.play_action(action, return_to_idle=False)

    def play_action(self, action: ActionConfig, return_to_idle: bool = False, force_loop: bool = False) -> None:
        if self.sheet is None or self.config is None:
            return
        self.action_token += 1
        token = self.action_token
        self.current_action_id = action.id
        self.current_force_loop = force_loop
        frames = self.slice_action(action)
        self.pet.show_action(frames, action.fps, action.loop or force_loop, self.clamp_display_scale())
        self.root.after(0, self.apply_saved_or_default_pet_position)
        self.status.set(f"正在播放：{action.name}")
        self.save_current_pet_state(enabled=True)
        self.schedule_playful_action()
        if return_to_idle and action.id != self.current_bindings().get("idle"):
            delay = max(500, round(1000 * max(1, action.frames) / max(0.1, action.fps)))
            self.root.after(delay, lambda expected=token: self.return_to_idle_if_current(expected))

    def return_to_idle_if_current(self, expected_token: int) -> None:
        if self.pet.drag_button_down:
            return
        if expected_token == self.action_token:
            self.play_interaction("idle", reset_play_timer=False)

    def play_action_by_id(self, action_id: str, return_to_idle: bool = True, force_loop: bool = False) -> bool:
        if self.config is None:
            return False
        action = next((item for item in self.config.actions if item.id == action_id), None)
        if action is None:
            return False
        self.play_action(action, return_to_idle=return_to_idle, force_loop=force_loop)
        return True

    def play_interaction(self, interaction: str, reset_play_timer: bool = True) -> None:
        if self.config is None or self.sheet is None:
            return
        hold_interactions = {"drag_left", "drag_right", "drag_up"}
        if self.pet.drag_button_down and interaction not in hold_interactions:
            return
        if reset_play_timer:
            self.schedule_playful_action()
        bindings = self.current_bindings()
        action_id = bindings.get(interaction) or DEFAULT_INTERACTION_BINDINGS.get(interaction)
        if not action_id:
            return
        is_hold = interaction in hold_interactions
        self.play_action_by_id(action_id, return_to_idle=not is_hold and interaction != "idle", force_loop=is_hold)

    def pet_position(self) -> dict[str, int]:
        try:
            return {"x": int(self.pet.winfo_x()), "y": int(self.pet.winfo_y())}
        except tk.TclError:
            return {}

    def apply_saved_or_default_pet_position(self) -> None:
        if self.pet_position_applied:
            return
        self.pet.update_idletasks()
        raw_position = self.app_config.get("pet_position")
        x: int
        y: int
        if isinstance(raw_position, dict) and "x" in raw_position and "y" in raw_position:
            try:
                x = int(raw_position["x"])
                y = int(raw_position["y"])
            except (TypeError, ValueError):
                x, y = self.default_pet_position()
        else:
            x, y = self.default_pet_position()
        x, y = self.clamp_pet_position(x, y)
        self.pet.geometry(f"+{x}+{y}")
        self.pet_position_applied = True

    def default_pet_position(self) -> tuple[int, int]:
        self.pet.update_idletasks()
        screen_w = self.pet.winfo_screenwidth()
        screen_h = self.pet.winfo_screenheight()
        width = max(1, self.pet.winfo_width())
        height = max(1, self.pet.winfo_height())
        return max(0, screen_w - width - 24), max(0, screen_h - height - 64)

    def clamp_pet_position(self, x: int, y: int) -> tuple[int, int]:
        self.pet.update_idletasks()
        screen_w = self.pet.winfo_screenwidth()
        screen_h = self.pet.winfo_screenheight()
        width = max(1, self.pet.winfo_width())
        height = max(1, self.pet.winfo_height())
        max_x = max(0, screen_w - min(width, screen_w))
        max_y = max(0, screen_h - min(height, screen_h))
        return min(max_x, max(0, x)), min(max_y, max(0, y))

    def schedule_playful_action(self) -> None:
        if self.playful_after_id:
            self.root.after_cancel(self.playful_after_id)
        self.playful_after_id = self.root.after(45000, self.playful_action)

    def playful_action(self) -> None:
        self.playful_after_id = None
        if not self.pet.winfo_ismapped():
            return
        if self.pet.drag_button_down:
            self.schedule_playful_action()
            return
        self.play_interaction("play", reset_play_timer=False)
        self.schedule_playful_action()

    def hide_pet(self) -> None:
        self.pet.withdraw()
        if self.playful_after_id:
            self.root.after_cancel(self.playful_after_id)
            self.playful_after_id = None
        self.save_current_pet_state(enabled=False)

    def slice_action(self, action: ActionConfig) -> list[Image.Image]:
        assert self.sheet is not None and self.config is not None
        cw, ch = self.config.cell_width, self.config.cell_height
        frames = []
        for col in range(action.frames):
            box = (col * cw, action.row * ch, (col + 1) * cw, (action.row + 1) * ch)
            if box[2] > self.sheet.width or box[3] > self.sheet.height:
                break
            frames.append(self.sheet.crop(box).convert("RGBA"))
        return frames

    def load_app_config(self) -> dict:
        if APP_CONFIG.exists():
            try:
                return json.loads(APP_CONFIG.read_text(encoding="utf-8"))
            except Exception:
                return {}
        if LEGACY_APP_CONFIG.exists():
            try:
                return json.loads(LEGACY_APP_CONFIG.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def save_app_config(self) -> None:
        self.app_config.update({
            "last_prompt": self.get_generation_prompt(),
            "api_key": self.gen_api_key.get(),
            "base_url": self.gen_base_url.get(),
            "model": self.gen_model.get(),
            "reference_images": [str(path) for path in self.gen_references],
        })
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        APP_CONFIG.write_text(json.dumps(self.app_config, indent=2, ensure_ascii=False), encoding="utf-8")

    def save_current_pet_state(self, enabled: bool) -> None:
        self.save_app_config()
        pet_folder = ""
        if self.image_path:
            try:
                parent = self.image_path.parent.resolve()
                if parent.parent.resolve() == PETS_DIR.resolve():
                    pet_folder = str(parent)
            except Exception:
                pet_folder = ""
        self.app_config.update({
            "last_pet_folder": pet_folder,
            "last_pet_image": str(self.image_path) if self.image_path else "",
            "last_pet_json": str(self.json_path) if self.json_path else "",
            "pet_enabled": enabled,
        })
        if enabled and self.pet.winfo_ismapped() and self.pet_position_applied:
            self.app_config["pet_position"] = self.pet_position()
        APP_CONFIG.write_text(json.dumps(self.app_config, indent=2, ensure_ascii=False), encoding="utf-8")

    def restore_last_pet(self) -> None:
        was_enabled = bool(self.app_config.get("pet_enabled"))
        raw_pet_folder = str(self.app_config.get("last_pet_folder", "")).strip()
        if raw_pet_folder:
            pet_folder = Path(raw_pet_folder)
            if pet_folder.exists() and pet_folder.is_dir():
                self.load_pet_folder(pet_folder)
                self.app_config["pet_enabled"] = was_enabled
                if was_enabled:
                    self.root.after(300, self.play_selected)
                return

        raw_image_path = str(self.app_config.get("last_pet_image", "")).strip()
        raw_json_path = str(self.app_config.get("last_pet_json", "")).strip()
        if not raw_image_path:
            return

        image_path = Path(raw_image_path)
        json_path = Path(raw_json_path) if raw_json_path else None
        if image_path.exists() and image_path.is_file():
            try:
                self.image_path = image_path
                self.sheet = Image.open(image_path).convert("RGBA")
            except Exception:
                self.image_path = None
                self.sheet = None
                self.app_config["last_pet_folder"] = ""
                self.app_config["last_pet_image"] = ""
                self.app_config["last_pet_json"] = ""
                self.app_config["pet_enabled"] = False
                self.save_app_config()
                return
            if json_path and json_path.exists() and json_path.is_file():
                self.load_json(json_path)
            else:
                self._infer_grid()
                self.rebuild_actions_from_grid()
            self.status.set(f"已恢复上次桌宠：{image_path}")
            self.app_config["pet_enabled"] = was_enabled
            if was_enabled:
                self.root.after(300, self.play_selected)


def run_embedded_generator() -> int:
    runner = RESOURCE_DIR / "generator" / "make-animation-frames" / "scripts" / "run_animation_pipeline.py"
    if not runner.exists():
        print(f"找不到内置生成器：{runner}", file=sys.stderr)
        return 2
    sys.argv = [str(runner), *sys.argv[2:]]
    runpy.run_path(str(runner), run_name="__main__")
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--run-generator":
        return run_embedded_generator()
    root = tk.Tk()
    DesktopPetApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
