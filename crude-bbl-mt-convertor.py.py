from __future__ import annotations

import math
import os
import tempfile
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader


# ----------------------------
# Presets (18) — default API@60F
# ----------------------------
PRESETS = [
    {"name": "WTI", "category": "Light Sweet", "api60": 39.6},
    {"name": "Brent", "category": "Light Sweet", "api60": 38.1},
    {"name": "Azeri Light", "category": "Light Sweet", "api60": 35.6},
    {"name": "Qua Iboe", "category": "Light Sweet", "api60": 37.3},
    {"name": "Bonny Light", "category": "Light Sweet", "api60": 32.9},

    {"name": "Arab Light", "category": "Light/Medium Sour", "api60": 33.5},
    {"name": "Basrah Light", "category": "Light/Medium Sour", "api60": 33.45},
    {"name": "Oman Export", "category": "Light/Medium Sour", "api60": 33.2},
    {"name": "Dubai (Fateh)", "category": "Light/Medium Sour", "api60": 31.0},
    {"name": "Urals", "category": "Medium Sour", "api60": 32.0},

    {"name": "CPC Blend", "category": "Very Light", "api60": 46.6},
    {"name": "Forties", "category": "Very Light", "api60": 44.4},

    {"name": "Arab Heavy", "category": "Heavy Sour", "api60": 27.0},
    {"name": "Basrah Heavy", "category": "Heavy Sour", "api60": 24.0},
    {"name": "WCS", "category": "Heavy Sour", "api60": 21.0},
    {"name": "Maya", "category": "Heavy Sour", "api60": 21.5},

    {"name": "Mars", "category": "Medium/Heavy Sour", "api60": 29.5},
    {"name": "ESPO", "category": "Light Sour", "api60": 34.7},
]

BBL_TO_M3 = 0.1589873

# Reference temps:
TREF_DENS_C = 15.0       # Density reference (°C)
TREF_API_C = 15.56       # 60°F in °C (API reference)

# Water density approximation used for SG <-> rho bridging in this estimate model
RHO_WATER_60F = 999.0  # kg/m³


# ----------------------------
# Utility / model
# ----------------------------
def fmt(x: float, nd: int = 4) -> str:
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return "—"
    return f"{x:.{nd}f}"


def api_to_sg(api: float) -> float:
    return 141.5 / (api + 131.5)


def sg_to_api(sg: float) -> float:
    return 141.5 / sg - 131.5


def rho_from_api_at_ref(api_ref: float) -> float:
    """Approx density (kg/m³) at API reference (60°F) from API@60°F."""
    return api_to_sg(api_ref) * RHO_WATER_60F


def rho_at_temp_from_ref(rho_ref: float, t_c: float, beta: float, t_ref_c: float) -> float:
    """
    Linear volumetric expansion estimate:
      rho(T) = rho_ref / (1 + beta*(T - Tref))
    """
    denom = 1.0 + beta * (t_c - t_ref_c)
    if denom <= 0:
        return float("nan")
    return rho_ref / denom


def tonnes_from_bbl(bbl: float, rho_kg_m3: float) -> float:
    return bbl * BBL_TO_M3 * rho_kg_m3 / 1000.0


def bbl_from_tonnes(tonnes: float, rho_kg_m3: float) -> float:
    factor = BBL_TO_M3 * rho_kg_m3 / 1000.0
    if factor <= 0:
        return float("nan")
    return tonnes / factor


def estimate_beta_by_api(api60: float) -> float:
    """
    Auto-beta bands (engineering approximation).
    API higher -> generally slightly lower beta; heavier -> higher beta.
    """
    if api60 >= 40.0:
        return 0.00065
    if api60 >= 30.0:
        return 0.00070
    if api60 >= 20.0:
        return 0.00075
    return 0.00080


# ----------------------------
# Tooltip (hover help)
# ----------------------------
class ToolTip:
    def __init__(self, widget: tk.Widget, text: str, delay_ms: int = 500):
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self._after_id = None
        self._tip = None

        widget.bind("<Enter>", self._schedule)
        widget.bind("<Leave>", self._hide)
        widget.bind("<ButtonPress>", self._hide)

    def _schedule(self, _event=None):
        self._cancel()
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _cancel(self):
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self):
        if self._tip is not None:
            return
        x = self.widget.winfo_rootx() + 10
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8

        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")

        frame = tk.Frame(self._tip, bg="#1f1f1f", bd=1, relief="solid")
        frame.pack(fill="both", expand=True)
        label = tk.Label(
            frame,
            text=self.text,
            bg="#1f1f1f",
            fg="#e8e8e8",
            justify="left",
            padx=10,
            pady=6,
            wraplength=380,
        )
        label.pack()

    def _hide(self, _event=None):
        self._cancel()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None


# ----------------------------
# Fixed height card wrapper
# ----------------------------
class FixedHeightCard(ttk.Frame):
    """A fixed-height container that also forces its child to stretch."""
    def __init__(self, master, height: int, **kwargs):
        super().__init__(master, **kwargs)
        self.grid_propagate(False)
        self.configure(height=height)
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)


# ----------------------------
# App
# ----------------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Crude Converter (bbl ↔ t) — Presets + API/Density + Temperature (Estimate)")
        self.geometry("1200x740")
        self.minsize(980, 620)

        self._apply_dark_theme()

        # ---- scrolling root layout ----
        self.container = ttk.Frame(self)
        self.container.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(self.container, highlightthickness=0, bg="#2b2b2b")
        self.vsb = ttk.Scrollbar(self.container, orient="vertical", command=self.canvas.yview)
        self.hsb = ttk.Scrollbar(self.container, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=self.vsb.set, xscrollcommand=self.hsb.set)

        self.vsb.pack(side="right", fill="y")
        self.hsb.pack(side="bottom", fill="x")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.content = ttk.Frame(self.canvas, padding=10)
        self.content_id = self.canvas.create_window((0, 0), window=self.content, anchor="nw")

        self.content.bind("<Configure>", self._on_content_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

        self.bind_all("<MouseWheel>", self._on_mousewheel)       # Windows
        self.bind_all("<Shift-MouseWheel>", self._on_shiftwheel) # horizontal

        # ---- state ----
        self.preset_var = tk.StringVar()
        self.use_mode_var = tk.StringVar(value="density")  # "density" or "api"

        self.temp_var = tk.DoubleVar(value=15.0)  # current temperature (°C)

        # β mode: manual or auto
        self.beta_mode_var = tk.StringVar(value="auto")  # "auto" or "manual"
        self.beta_var = tk.DoubleVar(value=0.0007)

        self.density15_var = tk.DoubleVar(value=850.0)  # kg/m3 @15°C (reference input)
        self.api60_var = tk.DoubleVar(value=35.0)       # API @60°F (reference input)

        self.mode_var = tk.StringVar(value="bbl_to_tonnes")
        self.realtime_var = tk.BooleanVar(value=True)

        self.bbl_var = tk.DoubleVar(value=1000.0)
        self.tonnes_var = tk.DoubleVar(value=0.0)

        # display-only computed @T values
        self.densityT_var = tk.StringVar(value="—")
        self.apiT_var = tk.StringVar(value="—")

        # one-click "push" lock
        self._normalize_lock_key: tuple | None = None

        # internal guard to avoid beta-update loops
        self._setting_beta = False

        # ---- UI ----
        self._build_ui()
        self._load_presets()

        self.preset_combo.current(0)
        self._apply_preset_defaults()
        self._sync_input_enablement()
        self._sync_beta_enablement()
        self._recalc()

    # ---------------- Theme ----------------
    def _apply_dark_theme(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        bg = "#2b2b2b"
        fg = "#e8e8e8"
        entry_bg = "#3a3a3a"
        entry_fg = "#e8e8e8"
        accent = "#4a4a4a"

        self.configure(bg=bg)

        style.configure(".", background=bg, foreground=fg, fieldbackground=entry_bg)
        style.configure("TFrame", background=bg)
        style.configure("TLabel", background=bg, foreground=fg)
        style.configure("TLabelframe", background=bg, foreground=fg)
        style.configure("TLabelframe.Label", background=bg, foreground=fg)
        style.configure("TButton", background=accent, foreground=fg, padding=6)
        style.map("TButton", background=[("active", "#555555")])
        style.configure("TEntry", fieldbackground=entry_bg, foreground=entry_fg, insertcolor=entry_fg)
        style.configure("TCombobox", fieldbackground=entry_bg, foreground=entry_fg)
        style.map("TCombobox", fieldbackground=[("readonly", entry_bg)])

    # ---------------- Scrolling helpers ----------------
    def _on_content_configure(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self.content_id, width=event.width)

    def _on_mousewheel(self, event):
        delta = int(-1 * (event.delta / 120))
        self.canvas.yview_scroll(delta, "units")

    def _on_shiftwheel(self, event):
        delta = int(-1 * (event.delta / 120))
        self.canvas.xview_scroll(delta, "units")

    # ---------------- UI build ----------------
    def _build_ui(self):
        self.content.columnconfigure(0, weight=1)

        # ---------- Row 1 ----------
        row1 = ttk.Frame(self.content)
        row1.grid(row=0, column=0, sticky="ew")
        row1.columnconfigure(0, weight=1)
        row1.columnconfigure(1, weight=1)
        row1.rowconfigure(0, weight=1)

        # a bit taller because we show computed @T lines
        row1_h = 230

        card1 = FixedHeightCard(row1, height=row1_h)
        card1.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        lf_preset = ttk.LabelFrame(card1, text="Crude Blend Preset")
        lf_preset.grid(row=0, column=0, sticky="nsew")
        lf_preset.columnconfigure(0, weight=1)
        lf_preset.rowconfigure(1, weight=1)

        self.preset_combo = ttk.Combobox(lf_preset, textvariable=self.preset_var, state="readonly")
        self.preset_combo.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 6))
        self.preset_combo.bind("<<ComboboxSelected>>", lambda e: self._on_preset_selected())

        self.preset_info = ttk.Label(lf_preset, text="—", justify="left")
        self.preset_info.grid(row=1, column=0, sticky="nw", padx=10, pady=(0, 10))

        card2 = FixedHeightCard(row1, height=row1_h)
        card2.grid(row=0, column=1, sticky="nsew")

        lf_custom = ttk.LabelFrame(card2, text="Custom Inputs (reference: density@15°C, API@60°F)")
        lf_custom.grid(row=0, column=0, sticky="nsew")
        for c in range(4):
            lf_custom.columnconfigure(c, weight=1)

        ttk.Label(lf_custom, text="Temperature (°C)").grid(row=0, column=0, sticky="w", padx=10, pady=(10, 4))
        self.temp_entry = ttk.Entry(lf_custom, textvariable=self.temp_var)
        self.temp_entry.grid(row=0, column=1, sticky="ew", padx=10, pady=(10, 4))

        ttk.Label(lf_custom, text="β Mode").grid(row=0, column=2, sticky="w", padx=10, pady=(10, 4))
        self.beta_mode_combo = ttk.Combobox(
            lf_custom,
            state="readonly",
            values=["auto", "manual"],
            textvariable=self.beta_mode_var,
        )
        self.beta_mode_combo.grid(row=0, column=3, sticky="ew", padx=10, pady=(10, 4))
        self.beta_mode_combo.bind("<<ComboboxSelected>>", lambda e: self._on_beta_mode_changed())

        ttk.Label(lf_custom, text="β (1/°C)").grid(row=1, column=2, sticky="w", padx=10, pady=4)
        self.beta_entry = ttk.Entry(lf_custom, textvariable=self.beta_var)
        self.beta_entry.grid(row=1, column=3, sticky="ew", padx=10, pady=4)

        ToolTip(
            self.beta_mode_combo,
            "auto: β is estimated from API@60°F used in calculations.\n"
            "manual: you specify β directly.\n\n"
            "This is still an estimate model (not certified custody transfer)."
        )

        self.use_den_rb = ttk.Radiobutton(
            lf_custom, text="Use Density @15°C (kg/m³)", variable=self.use_mode_var, value="density",
            command=self._on_use_mode_changed
        )
        self.use_den_rb.grid(row=1, column=0, sticky="w", padx=10, pady=4)

        self.density_entry = ttk.Entry(lf_custom, textvariable=self.density15_var)
        self.density_entry.grid(row=1, column=1, sticky="ew", padx=10, pady=4)

        self.use_api_rb = ttk.Radiobutton(
            lf_custom, text="Use API @60°F", variable=self.use_mode_var, value="api",
            command=self._on_use_mode_changed
        )
        self.use_api_rb.grid(row=2, column=0, sticky="w", padx=10, pady=4)

        self.api_entry = ttk.Entry(lf_custom, textvariable=self.api60_var)
        self.api_entry.grid(row=2, column=1, sticky="ew", padx=10, pady=4)

        self.normalize_btn = ttk.Button(
            lf_custom,
            text="Push reference → current T",
            command=self._push_reference_to_current_temp,
        )
        self.normalize_btn.grid(row=2, column=2, columnspan=2, sticky="ew", padx=10, pady=4)

        ToolTip(
            self.normalize_btn,
            "Updates the computed @T values (does NOT overwrite the reference inputs).\n\n"
            "• Density mode: density@15°C → density@T and API@T\n"
            "• API mode: API@60°F → density@T and apparent API@T\n\n"
            "The conversion calculations always use reference inputs + the model.\n"
            "This button is mainly for 'commit/refresh' and is protected from spam clicks."
        )

        self.reset_btn = ttk.Button(lf_custom, text="Reset to preset defaults", command=self._apply_preset_defaults)
        self.reset_btn.grid(row=3, column=2, columnspan=2, sticky="ew", padx=10, pady=4)

        ttk.Label(lf_custom, text="Computed density @T (kg/m³)").grid(row=3, column=0, sticky="w", padx=10, pady=4)
        ttk.Label(lf_custom, textvariable=self.densityT_var).grid(row=3, column=1, sticky="w", padx=10, pady=4)

        ttk.Label(lf_custom, text="Computed apparent API @T").grid(row=4, column=0, sticky="w", padx=10, pady=(0, 10))
        ttk.Label(lf_custom, textvariable=self.apiT_var).grid(row=4, column=1, sticky="w", padx=10, pady=(0, 10))

        # ---------- Row 2 ----------
        row2 = ttk.Frame(self.content)
        row2.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        row2.columnconfigure(0, weight=1)
        row2.columnconfigure(1, weight=1)
        row2.rowconfigure(0, weight=1)

        row2_h = 230

        card3 = FixedHeightCard(row2, height=row2_h)
        card3.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        lf_calc = ttk.LabelFrame(card3, text="Calculation")
        lf_calc.grid(row=0, column=0, sticky="nsew")
        for c in range(3):
            lf_calc.columnconfigure(c, weight=1)

        ttk.Radiobutton(lf_calc, text="Barrels → Tonnes", variable=self.mode_var, value="bbl_to_tonnes",
                        command=self._recalc).grid(row=0, column=0, sticky="w", padx=10, pady=(10, 6))
        ttk.Radiobutton(lf_calc, text="Tonnes → Barrels", variable=self.mode_var, value="tonnes_to_bbl",
                        command=self._recalc).grid(row=0, column=1, sticky="w", padx=10, pady=(10, 6))
        ttk.Checkbutton(lf_calc, text="Real-time", variable=self.realtime_var).grid(
            row=0, column=2, sticky="e", padx=10, pady=(10, 6)
        )

        ttk.Label(lf_calc, text="Barrels (bbl)").grid(row=1, column=0, sticky="w", padx=10, pady=4)
        self.bbl_entry = ttk.Entry(lf_calc, textvariable=self.bbl_var)
        self.bbl_entry.grid(row=1, column=1, sticky="ew", padx=10, pady=4)

        ttk.Label(lf_calc, text="Metric tonnes (t)").grid(row=2, column=0, sticky="w", padx=10, pady=4)
        self.tonnes_entry = ttk.Entry(lf_calc, textvariable=self.tonnes_var)
        self.tonnes_entry.grid(row=2, column=1, sticky="ew", padx=10, pady=4)

        self.calc_btn = ttk.Button(lf_calc, text="Calculate", command=self._recalc)
        self.calc_btn.grid(row=1, column=2, rowspan=2, sticky="nsew", padx=10, pady=4)

        card4 = FixedHeightCard(row2, height=row2_h)
        card4.grid(row=0, column=1, sticky="nsew")

        lf_res = ttk.LabelFrame(card4, text="Results")
        lf_res.grid(row=0, column=0, sticky="nsew")
        lf_res.columnconfigure(0, weight=1)

        self.res1 = ttk.Label(lf_res, text="1 tonne = — bbl", font=("Segoe UI", 11, "bold"))
        self.res1.grid(row=0, column=0, sticky="w", padx=10, pady=(10, 4))

        self.res2 = ttk.Label(lf_res, text="1 bbl = — tonnes", font=("Segoe UI", 11, "bold"))
        self.res2.grid(row=1, column=0, sticky="w", padx=10, pady=4)

        self.res_details = ttk.Label(lf_res, text="—", justify="left")
        self.res_details.grid(row=2, column=0, sticky="w", padx=10, pady=(4, 8))

        self.export_pdf_btn = ttk.Button(lf_res, text="Export PDF (screenshot)", command=self._export_pdf_screenshot)
        self.export_pdf_btn.grid(row=3, column=0, sticky="w", padx=10, pady=(0, 10))

        ToolTip(
            self.export_pdf_btn,
            "Exports a PDF containing a screenshot of the current app window.\n"
            "A disclaimer is added below.\n\n"
            "If screenshot capture fails, it falls back to a PDF with charts + key numbers."
        )

        # ---------- Charts (smaller) ----------
        charts = ttk.Frame(self.content)
        charts.grid(row=2, column=0, sticky="nsew", pady=(10, 0))
        charts.columnconfigure(0, weight=1)
        charts.columnconfigure(1, weight=1)

        self.fig1 = Figure(figsize=(4.2, 2.7), dpi=100)
        self.ax1 = self.fig1.add_subplot(111)
        self.canvas1 = FigureCanvasTkAgg(self.fig1, master=charts)
        self.canvas1.get_tk_widget().grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=6)

        self.fig2 = Figure(figsize=(4.2, 2.7), dpi=100)
        self.ax2 = self.fig2.add_subplot(111)
        self.canvas2 = FigureCanvasTkAgg(self.fig2, master=charts)
        self.canvas2.get_tk_widget().grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=6)

        # --- bindings ---
        self.temp_entry.bind("<KeyRelease>", lambda e: self._on_user_changed_critical())
        self.beta_entry.bind("<KeyRelease>", lambda e: self._on_user_changed_critical())
        self.density_entry.bind("<KeyRelease>", lambda e: self._on_user_changed_selected_value())
        self.api_entry.bind("<KeyRelease>", lambda e: self._on_user_changed_selected_value())

        for ent in (self.temp_entry, self.beta_entry, self.density_entry, self.api_entry, self.bbl_entry, self.tonnes_entry):
            ent.bind("<Return>", lambda e: self._recalc())
            ent.bind("<FocusOut>", lambda e: self._maybe_recalc())

    # ---------------- Data / state helpers ----------------
    def _load_presets(self):
        self.preset_combo["values"] = [f"{p['name']}  —  {p['category']}" for p in PRESETS]

    def _selected_preset(self) -> dict:
        idx = self.preset_combo.current()
        return PRESETS[idx if idx >= 0 else 0]

    def _reset_normalize_lock(self):
        self._normalize_lock_key = None
        self._update_normalize_button_state()

    def _on_user_changed_critical(self):
        self._reset_normalize_lock()
        self._maybe_recalc()

    def _on_user_changed_selected_value(self):
        self._reset_normalize_lock()
        self._maybe_recalc()

    def _on_preset_selected(self):
        self._apply_preset_defaults()

    def _on_use_mode_changed(self):
        self._sync_input_enablement()
        self._reset_normalize_lock()
        self._maybe_recalc()

    def _on_beta_mode_changed(self):
        self._sync_beta_enablement()
        self._reset_normalize_lock()
        self._maybe_recalc()

    def _sync_input_enablement(self):
        use = self.use_mode_var.get()
        if use == "density":
            self.density_entry.configure(state="normal")
            self.api_entry.configure(state="disabled")
        else:
            self.density_entry.configure(state="disabled")
            self.api_entry.configure(state="normal")
        self._update_normalize_button_state()

    def _sync_beta_enablement(self):
        mode = self.beta_mode_var.get()
        if mode == "manual":
            self.beta_entry.configure(state="normal")
        else:
            self.beta_entry.configure(state="disabled")

    def _apply_preset_defaults(self):
        p = self._selected_preset()
        self.api60_var.set(float(p["api60"]))

        # For density@15, we need a beta. If auto, estimate beta from preset api.
        if self.beta_mode_var.get() == "auto":
            beta = estimate_beta_by_api(float(p["api60"]))
            self._set_beta(beta)
        else:
            beta = float(self.beta_var.get())

        rho60 = rho_from_api_at_ref(float(p["api60"]))
        rho15 = rho_at_temp_from_ref(rho60, TREF_DENS_C, beta, TREF_API_C)
        self.density15_var.set(float(rho15))

        self._update_preset_info()
        self._sync_input_enablement()
        self._sync_beta_enablement()
        self._reset_normalize_lock()
        self._maybe_recalc()

    def _set_beta(self, value: float):
        """Set beta_var without causing feedback loops."""
        self._setting_beta = True
        try:
            self.beta_var.set(float(value))
        finally:
            self._setting_beta = False

    def _update_preset_info(self):
        p = self._selected_preset()
        api60 = float(p["api60"])

        # derive density@15 for display using current beta (auto or manual)
        beta = self._beta_used_for_calc(api60_hint=api60)
        rho60 = rho_from_api_at_ref(api60)
        rho15 = rho_at_temp_from_ref(rho60, TREF_DENS_C, beta, TREF_API_C)

        self.preset_info.configure(
            text=(
                f"Name: {p['name']}\n"
                f"Category: {p['category']}\n"
                f"Preset API @60°F: {fmt(api60, 2)}\n"
                f"Derived density @15°C: {fmt(rho15, 1)} kg/m³\n"
                f"β used: {fmt(beta, 6)} ({self.beta_mode_var.get()})"
            )
        )

    def _maybe_recalc(self):
        if self.realtime_var.get():
            self._recalc()

    # ---------------- Validation / beta ----------------
    def _validate(self) -> bool:
        try:
            t = float(self.temp_var.get())
        except Exception:
            messagebox.showerror("Invalid input", "Temperature must be numeric.")
            return False

        # beta must be valid even if auto (we store it in beta_var)
        try:
            beta = float(self.beta_var.get())
        except Exception:
            messagebox.showerror("Invalid input", "β must be numeric.")
            return False

        if not (-30.0 <= t <= 120.0):
            messagebox.showwarning("Check temperature", "Temperature looks unusual; check units (°C).")

        use = self.use_mode_var.get()
        try:
            if use == "density":
                rho15 = float(self.density15_var.get())
                if not (650.0 <= rho15 <= 1050.0):
                    messagebox.showwarning("Check density", "Density@15°C looks out of typical crude range (650–1050).")
            else:
                api60 = float(self.api60_var.get())
                if not (5.0 <= api60 <= 70.0):
                    messagebox.showwarning("Check API", "API@60°F looks out of typical range (5–70).")
        except Exception:
            messagebox.showerror("Invalid input", "Density/API must be numeric.")
            return False

        if not (0.0 < beta < 0.003):
            messagebox.showwarning("Check β", "β looks unusual. Typical crude ~0.0006–0.0009 1/°C.")

        return True

    def _beta_used_for_calc(self, api60_hint: float | None = None) -> float:
        """
        Returns beta used for calculations.
        If beta mode is auto -> estimate from API@60 used (hint optional).
        """
        if self.beta_mode_var.get() == "manual":
            return float(self.beta_var.get())

        # Auto mode: decide which API to use
        if api60_hint is None:
            # derive current api60 used via current inputs
            _, api60_used = self._rho15_used_and_api60_used(beta_override=None, allow_auto_beta=False)
            api60_hint = api60_used

        beta = estimate_beta_by_api(float(api60_hint))
        # keep displayed beta in sync
        self._set_beta(beta)
        return beta

    # ---------------- Normalize button state ----------------
    def _update_normalize_button_state(self):
        try:
            t = float(self.temp_var.get())
            # use "beta used", but if auto we don't want to trigger loops here; use current beta_var
            beta = float(self.beta_var.get())
        except Exception:
            self.normalize_btn.configure(state="disabled")
            return

        use = self.use_mode_var.get()
        ref_t = TREF_DENS_C if use == "density" else TREF_API_C

        if abs(t - ref_t) < 1e-9:
            self.normalize_btn.configure(state="disabled")
            return

        try:
            val = float(self.density15_var.get()) if use == "density" else float(self.api60_var.get())
        except Exception:
            self.normalize_btn.configure(state="disabled")
            return

        key = (use, round(t, 6), round(beta, 9), round(val, 6))
        self.normalize_btn.configure(state="disabled" if self._normalize_lock_key == key else "normal")

    def _push_reference_to_current_temp(self):
        """
        SAFE push: does NOT overwrite reference inputs.
        Updates the computed @T display only, and locks for the same (use, t, beta, ref_value).
        """
        if not self._validate():
            return

        t_c = float(self.temp_var.get())

        # Determine which api drives beta in auto mode
        rho15_for_calc, api60_for_calc = self._rho15_used_and_api60_used(beta_override=None, allow_auto_beta=True)
        beta = self._beta_used_for_calc(api60_hint=api60_for_calc)

        rhoT = rho_at_temp_from_ref(rho15_for_calc, t_c, beta, TREF_DENS_C)
        if math.isnan(rhoT) or rhoT <= 0:
            messagebox.showerror("Model error", "Computed density became invalid. Check temperature and β.")
            return
        apiT = sg_to_api(rhoT / RHO_WATER_60F)

        self.densityT_var.set(fmt(rhoT, 2))
        self.apiT_var.set(fmt(apiT, 2))

        use = self.use_mode_var.get()
        ref_val = float(self.density15_var.get()) if use == "density" else float(self.api60_var.get())
        self._normalize_lock_key = (use, round(t_c, 6), round(beta, 9), round(ref_val, 6))

        self._update_normalize_button_state()

    # ---------------- Core conversion helpers ----------------
    def _rho15_used_and_api60_used(self, beta_override: float | None, allow_auto_beta: bool) -> tuple[float, float]:
        """
        Centralize conversion on density@15°C.
        - If density mode: rho15 comes from input; api60 derived for reporting.
        - If api mode: api60 comes from input; rho15 derived.
        beta_override can force a beta; if None, use beta_var (or auto if allowed).
        """
        use = self.use_mode_var.get()

        if use == "density":
            rho15 = float(self.density15_var.get())

            # need beta to derive api60 from rho60; choose beta carefully
            if beta_override is not None:
                beta = beta_override
            else:
                beta = float(self.beta_var.get())

            rho60 = rho_at_temp_from_ref(rho15, TREF_API_C, beta, TREF_DENS_C)
            api60 = sg_to_api(rho60 / RHO_WATER_60F)
            return rho15, api60

        # api mode:
        api60 = float(self.api60_var.get())

        # choose beta
        if beta_override is not None:
            beta = beta_override
        else:
            if allow_auto_beta:
                beta = self._beta_used_for_calc(api60_hint=api60)
            else:
                beta = float(self.beta_var.get())

        rho60 = rho_from_api_at_ref(api60)
        rho15 = rho_at_temp_from_ref(rho60, TREF_DENS_C, beta, TREF_API_C)
        return rho15, api60

    def _recalc(self):
        if not self._validate():
            return

        self._update_normalize_button_state()

        t_c = float(self.temp_var.get())

        # First determine api60 used (for auto-beta) without recursively calling auto-beta too early:
        rho15_base, api60_base = self._rho15_used_and_api60_used(beta_override=float(self.beta_var.get()), allow_auto_beta=False)

        # Beta used (manual or auto)
        beta = self._beta_used_for_calc(api60_hint=api60_base)

        # Now re-derive rho15/api60 consistently with that beta if needed:
        rho15, api60 = self._rho15_used_and_api60_used(beta_override=beta, allow_auto_beta=True)

        rhoT = rho_at_temp_from_ref(rho15, t_c, beta, TREF_DENS_C)
        if math.isnan(rhoT) or rhoT <= 0:
            messagebox.showerror("Model error", "Computed density became invalid. Check temperature and β.")
            return

        apiT = sg_to_api(rhoT / RHO_WATER_60F)

        # update computed display
        self.densityT_var.set(fmt(rhoT, 2))
        self.apiT_var.set(fmt(apiT, 2))

        one_bbl_in_t = tonnes_from_bbl(1.0, rhoT)
        one_t_in_bbl = bbl_from_tonnes(1.0, rhoT)

        mode = self.mode_var.get()
        if mode == "bbl_to_tonnes":
            try:
                bbl = float(self.bbl_var.get())
            except Exception:
                messagebox.showerror("Invalid input", "Barrels must be numeric.")
                return
            self.tonnes_var.set(round(tonnes_from_bbl(bbl, rhoT), 6))
        else:
            try:
                tonnes = float(self.tonnes_var.get())
            except Exception:
                messagebox.showerror("Invalid input", "Tonnes must be numeric.")
                return
            self.bbl_var.set(round(bbl_from_tonnes(tonnes, rhoT), 6))

        self.res1.configure(text=f"1 tonne = {fmt(one_t_in_bbl, 4)} bbl")
        self.res2.configure(text=f"1 bbl = {fmt(one_bbl_in_t, 6)} tonnes")
        self.res_details.configure(
            text=(
                f"Used density @15°C: {fmt(rho15, 2)} kg/m³\n"
                f"Used API @60°F: {fmt(api60, 2)}\n"
                f"Density @T ({fmt(t_c, 2)}°C): {fmt(rhoT, 2)} kg/m³\n"
                f"Apparent API @T: {fmt(apiT, 2)}\n"
                f"β used: {fmt(beta, 6)} ({self.beta_mode_var.get()})"
            )
        )

        self._update_preset_info()
        self._update_charts(rho15, beta)

    def _update_charts(self, rho15: float, beta: float):
        t_min, t_max = -10.0, 80.0
        ts = [t_min + i * (t_max - t_min) / 120 for i in range(121)]

        apis = []
        bbl_per_tonne = []
        for t in ts:
            rhoT = rho_at_temp_from_ref(rho15, t, beta, TREF_DENS_C)
            if math.isnan(rhoT) or rhoT <= 0:
                apis.append(float("nan"))
                bbl_per_tonne.append(float("nan"))
                continue
            apis.append(sg_to_api(rhoT / RHO_WATER_60F))
            bbl_per_tonne.append(bbl_from_tonnes(1.0, rhoT))

        t_cur = float(self.temp_var.get())
        rhoT_cur = rho_at_temp_from_ref(rho15, t_cur, beta, TREF_DENS_C)
        apiT_cur = sg_to_api(rhoT_cur / RHO_WATER_60F)
        bpt_cur = bbl_from_tonnes(1.0, rhoT_cur)

        # Dark-ish chart faces (no forced line colors)
        self.fig1.set_facecolor("#2b2b2b")
        self.fig2.set_facecolor("#2b2b2b")
        self.ax1.set_facecolor("#2b2b2b")
        self.ax2.set_facecolor("#2b2b2b")

        self.ax1.clear()
        self.ax1.set_facecolor("#2b2b2b")
        self.ax1.set_title("Apparent API vs Temperature (approx)")
        self.ax1.set_xlabel("Temperature (°C)")
        self.ax1.set_ylabel("API (apparent)")
        self.ax1.plot(ts, apis)
        self.ax1.scatter([t_cur], [apiT_cur])

        self.ax2.clear()
        self.ax2.set_facecolor("#2b2b2b")
        self.ax2.set_title("1 tonne in bbl vs Temperature (approx)")
        self.ax2.set_xlabel("Temperature (°C)")
        self.ax2.set_ylabel("bbl / tonne")
        self.ax2.plot(ts, bbl_per_tonne)
        self.ax2.scatter([t_cur], [bpt_cur])

        # Make labels visible on dark background
        for ax in (self.ax1, self.ax2):
            ax.tick_params(colors="#e8e8e8")
            ax.title.set_color("#e8e8e8")
            ax.xaxis.label.set_color("#e8e8e8")
            ax.yaxis.label.set_color("#e8e8e8")
            for spine in ax.spines.values():
                spine.set_color("#777777")

        self.fig1.tight_layout(pad=1.0)
        self.fig2.tight_layout(pad=1.0)
        self.canvas1.draw()
        self.canvas2.draw()

    # ---------------- PDF export (screenshot + disclaimer) ----------------
    def _export_pdf_screenshot(self):
        # Refresh calculations before export
        if not self._validate():
            return
        self._recalc()

        path = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            filetypes=[("PDF files", "*.pdf")],
            title="Save PDF report",
        )
        if not path:
            return

        # Try screenshot capture using Pillow ImageGrab (best on Windows/macOS)
        screenshot_path = None
        try:
            from PIL import ImageGrab  # type: ignore

            self.update_idletasks()
            x = self.winfo_rootx()
            y = self.winfo_rooty()
            w = self.winfo_width()
            h = self.winfo_height()

            img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
            tmpdir = tempfile.mkdtemp(prefix="crude_pdf_")
            screenshot_path = os.path.join(tmpdir, "window.png")
            img.save(screenshot_path, "PNG")
        except Exception:
            screenshot_path = None

        try:
            if screenshot_path:
                self._write_pdf_with_image(path, screenshot_path)
            else:
                self._write_pdf_fallback(path)

            messagebox.showinfo("PDF saved", f"Saved report:\n{path}")
        finally:
            # cleanup
            if screenshot_path:
                try:
                    tmpdir = os.path.dirname(screenshot_path)
                    os.remove(screenshot_path)
                    os.rmdir(tmpdir)
                except Exception:
                    pass

    def _write_pdf_with_image(self, pdf_path: str, image_path: str):
        c = pdfcanvas.Canvas(pdf_path, pagesize=A4)
        W, H = A4
        margin = 10 * mm

        # Draw screenshot scaled to fit page, keep aspect
        img = ImageReader(image_path)
        iw, ih = img.getSize()

        # reserve space for disclaimer at bottom
        disclaimer_h = 32 * mm
        avail_w = W - 2 * margin
        avail_h = H - 2 * margin - disclaimer_h

        scale = min(avail_w / iw, avail_h / ih)
        dw = iw * scale
        dh = ih * scale

        x = (W - dw) / 2
        y = margin + disclaimer_h + (avail_h - dh) / 2

        c.drawImage(img, x, y, width=dw, height=dh, preserveAspectRatio=True, mask="auto")

        # Disclaimer
        self._draw_disclaimer(c, margin, margin, W - 2 * margin)

        c.showPage()
        c.save()

    def _write_pdf_fallback(self, pdf_path: str):
        """
        If screenshot isn't available, still create a PDF with:
        - charts
        - key numbers
        - disclaimer
        """
        tmpdir = tempfile.mkdtemp(prefix="crude_pdf_fallback_")
        img1 = os.path.join(tmpdir, "chart1.png")
        img2 = os.path.join(tmpdir, "chart2.png")
        self.fig1.savefig(img1, dpi=160, bbox_inches="tight")
        self.fig2.savefig(img2, dpi=160, bbox_inches="tight")

        try:
            c = pdfcanvas.Canvas(pdf_path, pagesize=A4)
            W, H = A4
            margin = 12 * mm
            y = H - margin

            def line(txt, dy=6*mm, font="Helvetica", size=10):
                nonlocal y
                c.setFont(font, size)
                c.drawString(margin, y, txt)
                y -= dy

            c.setFont("Helvetica-Bold", 14)
            c.drawString(margin, y, "Crude Conversion Report (Fallback)")
            y -= 8 * mm
            c.setFont("Helvetica", 10)
            c.drawString(margin, y, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            y -= 10 * mm

            # Pull key numbers
            preset = self._selected_preset()
            t_c = float(self.temp_var.get())
            beta = float(self.beta_var.get())
            rho15, api60 = self._rho15_used_and_api60_used(beta_override=beta, allow_auto_beta=True)
            rhoT = rho_at_temp_from_ref(rho15, t_c, beta, TREF_DENS_C)
            apiT = sg_to_api(rhoT / RHO_WATER_60F)

            one_bbl_in_t = tonnes_from_bbl(1.0, rhoT)
            one_t_in_bbl = bbl_from_tonnes(1.0, rhoT)

            line(f"Preset: {preset['name']}  |  Category: {preset['category']}")
            line(f"T: {t_c:.2f} °C")
            line(f"β used: {beta:.6f} ({self.beta_mode_var.get()})")
            line(f"Density@15°C: {rho15:.2f} kg/m³")
            line(f"API@60°F: {api60:.2f}")
            line(f"Density@T: {rhoT:.2f} kg/m³")
            line(f"Apparent API@T: {apiT:.2f}")
            line(f"1 tonne = {one_t_in_bbl:.4f} bbl")
            line(f"1 bbl = {one_bbl_in_t:.6f} tonnes", dy=10*mm)

            # Charts
            maxw = W - 2 * margin
            imgh = (H * 0.28)
            c.drawImage(ImageReader(img1), margin, y - imgh, width=maxw, height=imgh, preserveAspectRatio=True, mask="auto")
            y -= (imgh + 6 * mm)
            c.drawImage(ImageReader(img2), margin, y - imgh, width=maxw, height=imgh, preserveAspectRatio=True, mask="auto")
            y -= (imgh + 6 * mm)

            self._draw_disclaimer(c, margin, margin, W - 2 * margin)

            c.showPage()
            c.save()
        finally:
            try:
                os.remove(img1)
                os.remove(img2)
                os.rmdir(tmpdir)
            except Exception:
                pass

    def _draw_disclaimer(self, c: pdfcanvas.Canvas, x: float, y: float, width: float):
        disclaimer = (
            "DISCLAIMER / NO LIABILITY:\n"
            "This report is generated by an estimation tool. Results are approximate and provided 'as is'.\n"
            "Numbers are calculated based on approximation (best available). Do not use for custody transfer\n"
            "or safety-critical decisions. The author assumes no liability for any use of these results."
        )
        c.setFont("Helvetica-Oblique", 8)
        # simple wrapped text (manual wrap)
        lines = disclaimer.split("\n")
        yy = y + 26 * mm
        for ln in lines:
            c.drawString(x, yy, ln)
            yy -= 4 * mm


if __name__ == "__main__":
    App().mainloop()