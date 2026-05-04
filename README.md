# ANSYS AVZ Exporter

Batch tool for exporting ANSYS **CDB + RST** results into **AVZ visualization format** using ANSYS Mechanical automation.

---

## Overview

This tool automates the workflow:

```
ANSYS DB → CDB → Mechanical → AVZ → AVZM (optional)
```

It is designed for batch processing of multiple simulation jobs and supports flexible configuration via CLI.

---

## Features

- Batch processing of multiple jobs
- Automatic **CDB generation from DB**
- Auto-detection of unit system from `/UNITS`
- RST time-step probing via **ANSYS DPF**
- Flexible time selection: `LAST`, `INDEX:n`, `VALUE:t`
- Predefined and custom result sets (Total Deformation, Equivalent Stress, Strains)
- Optional AVZM bundle creation
- Fully CLI-driven workflow

---

## Architecture

```
Python (CPython)
  ↓ Generates IronPython script
ANSYS Workbench (RunWB2)
  ↓ Mechanical execution
AVZ export
```

---

## Project Structure

```
.
├── export_avz_from_rst.py   # Main CLI tool
├── mech_export_avz.ipy      # Mechanical (IronPython) template
├── ExportToAVZ.bat          # Quick launch script (Windows)
├── out_list.txt             # Example job list
├── .gitignore
└── README.md
```

---

## Requirements

- Windows
- ANSYS Workbench / Mechanical
- ANSYS MAPDL
- ANSYS DPF (`ansys.dpf.core`)
- Environment variable: `ANSYS###_DIR`

---

## Usage

### Basic example

```bash
python export_avz_from_rst.py ^
  --base D:\ANSYS_jobs ^
  --list out_list.txt ^
  --version 241 ^
  --units-out BIN ^
  --time LAST
```

### Time selection options

| Format | Description |
|--------|-------------|
| `LAST` | Last available step |
| `INDEX:5` | Step by index |
| `VALUE:0.25` | Closest time value |
| `0.25` | Same as `VALUE:0.25` |

### Custom results

```bash
--results-preset custom ^
--results-list "Total Deformation,Equivalent Stress"
```

### Quick start (Windows)

```bat
ExportToAVZ.bat
```

---

## Output

- `.avz` files — one per result
- `.avzm` bundle — optional zip archive
- `*_export_status.json` — status report per job

---

## Notes

- `mech_export_avz.ipy` is required for execution inside Mechanical
- The tool does not include ANSYS data files
- Designed for engineering automation workflows
- Compatible with legacy (pre-PyAnsys) workflows (Ansys 2022)

---

## Author

Maxim Liplenko

## License

TBD
