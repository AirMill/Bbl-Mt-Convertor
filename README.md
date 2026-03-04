# Crude Converter (bbl ↔ tonnes) — Presets + Temperature Correction (Estimate)

A lightweight desktop GUI tool (Python + Tkinter) to estimate conversions between **barrels (bbl)** and **metric tonnes (t)** for crude oil using common **crude blend presets**, **API / density inputs**, and **temperature adjustment**.

Built for practical day-to-day trading/logistics work where you need a **fast, reasonable estimate** across common crude categories.

---

## Features

- **18 crude blend presets** (WTI, Brent, Urals, Arab Light/Heavy, Basrah, WCS, Maya, etc.)
- Input by either:
  - **Density @ 15°C (kg/m³)**, or
  - **API @ 60°F**
- **Temperature input in °C**
- Temperature correction model:
  - **Manual β** (thermal expansion coefficient)
  - **Auto β (by API bands)** for better out-of-the-box estimates
- Results include:
  - **1 tonne in bbl**
  - **1 bbl in tonnes**
  - Density @T and apparent API @T
- **Charts**
  - Apparent API vs temperature (approx)
  - bbl per tonne vs temperature (approx)
- **Dark theme UI**
- **Scrollable window**
- **PDF export**
  - Exports a **screenshot of the app window** with a disclaimer
  - Falls back to a PDF with charts + key numbers if screenshot capture fails

---

## Disclaimer / No Liability

This tool is an **estimation tool**. Results are approximate and provided **“as is.”**

- Not for **custody transfer**
- Not for **safety-critical** decisions
- Numbers are calculated based on approximation (best available)
- The author assumes **no liability** for any use of these results

---

## How it works (High level)

- Reference basis:
  - Density is handled as **ρ @ 15°C**
  - API is handled as **API @ 60°F (≈ 15.56°C)**
- Temperature correction uses a **linear volumetric expansion** approximation:

  \[
  \rho(T) = \frac{\rho_{ref}}{1 + \beta (T - T_{ref})}
  \]

- Auto β uses practical API bands:
  - API ≥ 40 → β = 0.00065  
  - 30–39.99 → β = 0.00070  
  - 20–29.99 → β = 0.00075  
  - < 20 → β = 0.00080  

> Note: This is not a certified API MPMS / ASTM D1250 custody transfer implementation.

---

## Requirements

- Python **3.10+** recommended
- Packages:
  - `matplotlib`
  - `reportlab`
  - `pillow` *(optional but recommended for screenshot-to-PDF export)*

---

## Installation

```bash
git clone <your-repo-url>
cd <your-repo-folder>
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -r requirements.txt