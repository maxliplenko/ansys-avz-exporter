#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ANSYS AVZ Exporter - Refactored Architecture
============================================

Converts ANSYS CDB + RST files to AVZ visualization format via Mechanical.

Architecture:
    - Host: CPython 3.x (this orchestrator)
    - Target: IronPython 2.7 in Mechanical via Workbench
    
Workflow:
    1. Validate job files (RST, CDB/DB)
    2. Auto-detect or configure unit systems
    3. Probe RST for time steps (via DPF)
    4. Generate IronPython script from template
    5. Execute via Workbench journal
    6. Collect results and create bundles

Author: Maxim Liplenko
Version: 1.0
Date: 2025-07-08
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ==============================================================================
# LOGGING INFRASTRUCTURE
# ==============================================================================

class LoggerSetup:
    """Centralized logging configuration with singleton pattern."""
    
    _logger: Optional[logging.Logger] = None
    
    @classmethod
    def get_logger(cls, verbose: bool = False) -> logging.Logger:
        """
        Get or create logger instance.
        
        Args:
            verbose: Enable DEBUG level logging if True
            
        Returns:
            Configured logger instance
        """
        if cls._logger is None:
            cls._logger = cls._create_logger(verbose)

        else:
            # verbose, INFO
            if verbose:
                cls._logger.setLevel(logging.DEBUG)
                for h in cls._logger.handlers:
                    h.setLevel(logging.DEBUG)
                    
        return cls._logger
    
    @classmethod
    def _create_logger(cls, verbose: bool) -> logging.Logger:
        """Create logger with console handler."""
        logger = logging.getLogger("avz_exporter")
        logger.setLevel(logging.DEBUG if verbose else logging.INFO)
        logger.handlers.clear()
        
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(logging.DEBUG if verbose else logging.INFO)
        handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        logger.addHandler(handler)
        
        return logger


# Global logger instance
logger = LoggerSetup.get_logger()


def safe_remove(path: Path):
    """
    Safely remove file if it exists.
    
    Args:
        path: Path to file to remove
    """
    try:
        if path.exists():
            path.unlink()
            logger.debug(f"Removed temp file: {path}")
    except Exception as e:
        logger.debug(f"Failed to remove {path}: {e}")


# ==============================================================================
# DOMAIN MODELS
# ==============================================================================

class UnitSystem(Enum):
    """
    ANSYS unit system definitions.
    
    Maps to both Mechanical import and display unit system enums.
    """
    
    BIN = "BIN"      # British Imperial (in, lbf, s)
    BFT = "BFT"      # British (ft, lbf, s)
    NMM = "NMM"      # Metric (N, mm, kg, s)
    MKS = "MKS"      # SI (m, kg, s)
    CGS = "CGS"      # CGS (cm, g, s)
    NMMdat = "NMMdat"
    NMMTon = "NMMTon"
    UMKS = "UMKS"    # User-defined MKS
    SI = "SI"        # International System
    USEng = "USEng"  # US Engineering
    
    @classmethod
    def from_string(cls, value: str) -> UnitSystem:
        """
        Parse unit system from string (case-insensitive).
        
        Args:
            value: Unit system name
            
        Returns:
            UnitSystem enum value
            
        Raises:
            ValueError: If unit system is unknown
        """
        try:
            return cls[value.strip().upper()]
        except KeyError:
            raise ValueError(f"Invalid unit system: {value}")
    
    def to_mechanical_import(self) -> str:
        """
        Get Mechanical CDB import enum name.
        
        Returns:
            Enum name for Mechanical.DataModel.Enums.UnitSystemIDType
        """
        mapping = {
            UnitSystem.BIN: "UnitSystemConsistentBIN",
            UnitSystem.BFT: "UnitSystemConsistentBFT",
            UnitSystem.NMM: "UnitSystemMetricNMM",
            UnitSystem.MKS: "UnitSystemMetricMKS",
            UnitSystem.CGS: "UnitSystemCGS",
            UnitSystem.NMMdat: "UnitSystemNMMdat",
            UnitSystem.NMMTon: "UnitSystemNMMTon",
            UnitSystem.UMKS: "UnitSystemUMKS",
        }
        return mapping[self]
    
    def to_mechanical_display(self) -> str:
        """
        Get Mechanical display enum name.
        
        Returns:
            Enum name for Mechanical.DataModel.Enums.UserUnitSystemType
        """
        mapping = {
            UnitSystem.BIN: "UnitsBIN",
            UnitSystem.BFT: "UnitsBFT",
            UnitSystem.NMM: "UnitsNMM",
            UnitSystem.MKS: "UnitsMKS",
            UnitSystem.CGS: "UnitsCGS",
            UnitSystem.NMMdat: "UnitsNMMdat",
            UnitSystem.NMMTon: "UnitsNMMton",
            UnitSystem.UMKS: "UnitsUMKS",
        }
        return mapping[self]


def get_project_unit_system_name(units: UnitSystem) -> str:
    """
    Map UnitSystem to Workbench Project UnitSystemName.
    
    Used in Workbench journal SetProjectUnitSystem() call.
    
    Args:
        units: Unit system enum
        
    Returns:
        Workbench project unit system name
    """
    mapping = {
        UnitSystem.BIN: "BIN_STANDARD",
        UnitSystem.MKS: "MKS_STANDARD",
        UnitSystem.NMM: "NMMTON_STANDARD",
        UnitSystem.SI: "SI",
        UnitSystem.USEng: "US Engineering",
    }
    return mapping.get(units, "BIN_STANDARD")


class TimeSelectionMode(Enum):
    """Time/step selection strategies."""
    
    LAST = "LAST"      # Use last available time step
    INDEX = "INDEX"    # Use specific step index (1-based)
    VALUE = "VALUE"    # Use time value (find closest match)


class ResultsPreset(Enum):
    """Predefined result object collections."""
    
    DEFAULT = "default"    # Total Deformation + Equivalent Stress
    MINIMAL = "minimal"    # Total Deformation only
    EXTENDED = "extended"  # Extended set including strains
    CUSTOM = "custom"      # User-defined list
    
    def get_result_names(self, custom: Optional[List[str]] = None) -> List[str]:
        """
        Get result names for this preset.
        
        Args:
            custom: Custom result list (required for CUSTOM preset)
            
        Returns:
            List of result names
            
        Raises:
            ValueError: If CUSTOM preset but no custom list provided
        """
        presets = {
            self.DEFAULT: ["Total Deformation", "Equivalent Stress"],
            self.MINIMAL: ["Total Deformation"],
            self.EXTENDED: [
                "Total Deformation",
                "Equivalent Stress",
                "Equivalent Total Strain",
                "Equivalent Plastic Strain",
            ],
        }
        
        if self is ResultsPreset.CUSTOM:
            if not custom:
                raise ValueError("CUSTOM preset requires custom results list")
            return custom
        
        return presets[self]


class OutputMode(Enum):
    """Output directory structure modes."""
    
    ROOT = "root"      # Write AVZ files to job directory
    SUBDIR = "subdir"  # Write AVZ files to job/avz subdirectory


@dataclass
class TimeSelection:
    """
    Resolved time/step selection for export.
    
    Attributes:
        mode: Selection mode (LAST/INDEX/VALUE)
        index: Step index (1-based, None if not resolved)
        time: Time value (None if not resolved)
        canonical: Canonical string representation
        available_times: List of available time steps from RST
    """
    
    mode: TimeSelectionMode
    index: Optional[int] = None
    time: Optional[float] = None
    canonical: str = "LAST"
    available_times: List[float] = field(default_factory=list)
    
    def __str__(self) -> str:
        if self.index is not None and self.time is not None:
            return f"{self.mode.value}(idx={self.index}, t={self.time:.6g})"
        return self.mode.value


@dataclass
class JobPaths:
    """
    File path container for a single job.
    
    Attributes:
        name: Job name (folder name)
        base_dir: Base directory containing all jobs
    """
    
    name: str
    base_dir: Path
    
    @property
    def job_dir(self) -> Path:
        """Job directory path."""
        return self.base_dir / self.name
    
    @property
    def cdb_path(self) -> Path:
        """CDB file path."""
        return self.job_dir / f"{self.name}.cdb"
    
    @property
    def db_path(self) -> Path:
        """DB file path."""
        return self.job_dir / f"{self.name}.db"
    
    @property
    def rst_path(self) -> Path:
        """RST results file path."""
        return self.job_dir / f"{self.name}.rst"
    
    def get_output_dir(self, mode: OutputMode) -> Path:
        """
        Get output directory based on mode.
        
        Args:
            mode: Output mode (ROOT or SUBDIR)
            
        Returns:
            Output directory path
        """
        return self.job_dir if mode == OutputMode.ROOT else self.job_dir / "avz"
    
    def get_status_json(self, mode: OutputMode) -> Path:
        """
        Get path to status JSON file.
        
        Args:
            mode: Output mode
            
        Returns:
            Status JSON file path
        """
        return self.job_dir / f"{self.name}_export_status.json"


@dataclass
class ExportConfig:
    """
    Complete export configuration.
    
    Contains all parameters needed for batch export operation.
    """
    
    base_dir: Path
    jobs: List[str]
    version: int
    units_display: UnitSystem
    units_cdb: str  # "AUTO" or explicit unit system name
    results_preset: ResultsPreset
    custom_results: List[str]
    time_selection: str
    true_scale: bool
    deform_scale: float
    show_thickness: bool
    show_mesh: bool
    output_mode: OutputMode
    make_bundle: bool
    evaluate_results: bool
    template_path: Path
    runwb2_override: Optional[str]
    dry_run: bool = False
    verbose: bool = False


@dataclass
class ExportResult:
    """
    Result of a single job export.
    
    Attributes:
        job_name: Name of processed job
        success: True if export succeeded
        avz_files: List of exported AVZ files
        error_message: Error description if failed
    """
    
    job_name: str
    success: bool
    avz_files: List[Path] = field(default_factory=list)
    error_message: str = ""


# ==============================================================================
# FILE UTILITIES
# ==============================================================================

class FileOperations:
    """File system operations with error handling."""
    
    @staticmethod
    def ensure_directory(path: Path) -> None:
        """
        Create directory if it doesn't exist.
        
        Args:
            path: Directory path to create
        """
        path.mkdir(parents=True, exist_ok=True)
    
    @staticmethod
    def read_text_safe(path: Path) -> str:
        """
        Read text file with error handling.
        
        Args:
            path: File path to read
            
        Returns:
            File contents or empty string on error
        """
        try:
            return path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to read {path}: {e}")
            return ""
    
    @staticmethod
    def write_text_safe(path: Path, content: str) -> bool:
        """
        Write text file with error handling.
        
        Args:
            path: File path to write
            content: Text content
            
        Returns:
            True if successful, False otherwise
        """
        try:
            FileOperations.ensure_directory(path.parent)
            path.write_text(content, encoding="utf-8")
            return True
        except Exception as e:
            logger.error(f"Failed to write {path}: {e}")
            return False
    
    @staticmethod
    def parse_list_file(path: Path) -> List[str]:
        """
        Parse line-based list file (# for comments).
        
        Args:
            path: List file path
            
        Returns:
            List of non-empty, non-comment lines
        """
        items = []
        try:
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        items.append(line)
        except Exception as e:
            logger.error(f"Failed to parse list file {path}: {e}")
        return items
    
    @staticmethod
    def split_delimited(text: str, delimiters: str = ",;") -> List[str]:
        """
        Split string by multiple delimiters.
        
        Args:
            text: String to split
            delimiters: Delimiter characters
            
        Returns:
            List of non-empty trimmed strings
        """
        if not text:
            return []
        pattern = f"[{re.escape(delimiters)}]"
        return [s.strip() for s in re.split(pattern, text) if s.strip()]


# ==============================================================================
# CDB ANALYSIS
# ==============================================================================

class CDBAnalyzer:
    """CDB file analysis utilities."""
    
    # Regex to match ANSYS /UNITS directive
    UNITS_PATTERN = re.compile(r"(?im)^\s*/UNITS\s*,\s*([A-Z]+)\s*$")
    SCAN_LINES = 200  # Number of lines to scan for /UNITS
    
    @classmethod
    def detect_units(cls, cdb_path: Path) -> Optional[UnitSystem]:
        """
        Detect unit system from CDB /UNITS directive.
        
        Scans first SCAN_LINES lines of CDB file looking for /UNITS command.
        
        Args:
            cdb_path: Path to CDB file
            
        Returns:
            Detected UnitSystem or None if not found
        """
        if not cdb_path.is_file():
            logger.warning(f"CDB not found for unit detection: {cdb_path}")
            return None
        
        try:
            with cdb_path.open("r", encoding="utf-8", errors="ignore") as f:
                for _ in range(cls.SCAN_LINES):
                    line = f.readline()
                    if not line:
                        break
                    
                    match = cls.UNITS_PATTERN.match(line)
                    if match:
                        token = match.group(1).strip().upper()
                        try:
                            return UnitSystem.from_string(token)
                        except ValueError:
                            logger.warning(f"Unknown /UNITS token: {token}")
                            return None
        except Exception as e:
            logger.warning(f"Failed to scan CDB: {e}")
        
        return None


# ==============================================================================
# MAPDL INTEGRATION
# ==============================================================================

class MAPDLRunner:
    """MAPDL batch operations for CDB generation."""
    
    # APDL script template for CDB generation from DB
    CDB_GENERATION_SCRIPT = """\
/CLEAR
/FILNAME,'{jobname}',1
RESUME,'{jobname}','db'
/PREP7
CDWRITE,DB,'{jobname}','cdb',,'',' 
FINISH
"""
    
    @classmethod
    def generate_cdb_from_db(
        cls,
        cdb_path: Path,
        db_path: Path,
        version: int,
        verbose: bool,
    ) -> bool:
        """
        Generate CDB from DB using MAPDL batch mode.
        
        If CDB already exists, returns immediately.
        Otherwise, launches MAPDL in batch mode to create CDB from DB.
        
        Args:
            cdb_path: Target CDB file path
            db_path: Source DB file path
            version: ANSYS version number (e.g., 222)
            verbose: Keep temporary files if True
            
        Returns:
            True if CDB exists or was created successfully
        """
        # Skip if CDB already exists
        if cdb_path.exists():
            logger.debug(f"CDB exists: {cdb_path}")
            return True
        
        if not db_path.exists():
            logger.error(f"DB not found: {db_path}")
            return False
        
        # Find MAPDL executable via environment variable
        env_var = f"ANSYS{version}_DIR"
        ansys_root = os.environ.get(env_var)
        if not ansys_root:
            logger.error(f"Environment variable not set: {env_var}")
            return False
        
        mapdl_exe = Path(ansys_root) / "bin" / "winx64" / f"MAPDL{version}.exe"
        if not mapdl_exe.is_file():
            logger.error(f"MAPDL not found: {mapdl_exe}")
            return False
        
        # Prepare APDL script
        workdir = db_path.parent
        jobname = db_path.stem
        inp_path = workdir / "_make_cdb.inp"
        out_path = workdir / "_make_cdb.out"
        
        script = cls.CDB_GENERATION_SCRIPT.format(jobname=jobname)
        inp_path.write_text(script, encoding="utf-8")
        
        # Build MAPDL command line
        cmd = [
            str(mapdl_exe),
            "-b",           # Batch mode
            "-nolist",      # Suppress listing
            "-j", jobname,  # Job name
            "-dir", str(workdir),  # Working directory
            "-i", str(inp_path),   # Input file
            "-o", str(out_path),   # Output file
        ]
        
        logger.info("Generating CDB via MAPDL...")
        result = subprocess.run(
            cmd,
            cwd=str(workdir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        
        # Check result
        if result.returncode != 0 or not cdb_path.exists():
            logger.error("CDB generation failed")
            logger.debug(f"stdout: {result.stdout}")
            logger.debug(f"stderr: {result.stderr}")
            return False
        
        logger.info(f"CDB created: {cdb_path}")

        # Cleanup temporary files (depends on verbose mode)
        if not verbose:
            safe_remove(inp_path)
            safe_remove(out_path)

        return True


# ==============================================================================
# TIME SELECTION & RST PROBING
# ==============================================================================

class TimeSelectionParser:
    """Parse time selection strings into structured format."""
    
    # Regex patterns for different time selection formats
    PATTERNS = {
        "last": re.compile(r"(?i)^\s*LAST\s*$"),
        "index": re.compile(r"(?i)^\s*INDEX\s*:\s*(\d+)\s*$"),
        "value": re.compile(r"(?i)^\s*VALUE\s*:\s*([+-]?\d*\.?\d+(?:[eE][+-]?\d+)?)\s*$"),
        "float": re.compile(r"^\s*([+-]?\d*\.?\d+(?:[eE][+-]?\d+)?)\s*$"),
    }
    
    @classmethod
    def parse(cls, text: str) -> Tuple[TimeSelectionMode, Optional[float]]:
        """
        Parse time selection string.
        
        Supported formats:
            - "LAST" or empty: Last time step
            - "INDEX:5": Step index 5 (1-based)
            - "VALUE:0.25": Time value 0.25
            - "0.25": Plain float (treated as VALUE)
        
        Args:
            text: Time selection string
            
        Returns:
            Tuple of (mode, value)
            
        Raises:
            ValueError: If format is invalid
        """
        text = text.strip()
        
        # LAST mode (default)
        if not text or cls.PATTERNS["last"].match(text):
            return TimeSelectionMode.LAST, None
        
        # INDEX:n
        match = cls.PATTERNS["index"].match(text)
        if match:
            return TimeSelectionMode.INDEX, float(int(match.group(1)))
        
        # VALUE:t
        match = cls.PATTERNS["value"].match(text)
        if match:
            return TimeSelectionMode.VALUE, float(match.group(1))
        
        # Plain float (treat as VALUE)
        match = cls.PATTERNS["float"].match(text)
        if match:
            return TimeSelectionMode.VALUE, float(match.group(1))
        
        raise ValueError(f"Invalid time selection: {text}")


class RSTProbe:
    """RST file probing via DPF (Data Processing Framework)."""
    
    @staticmethod
    def probe_times(rst_path: Path) -> List[float]:
        """
        Probe available time steps in RST file.
        
        Uses ansys.dpf.core to read time/frequency support from RST.
        
        Args:
            rst_path: Path to RST file
            
        Returns:
            Sorted list of unique time values, empty if probing fails
        """
        if not rst_path.is_file():
            logger.warning(f"RST not found: {rst_path}")
            return []
        
        try:
            import ansys.dpf.core as dpf
        except ImportError:
            logger.debug("ansys.dpf.core not available")
            return []
        
        try:
            model = dpf.Model(str(rst_path))
            tf_support = model.metadata.time_freq_support
            
            if tf_support is None:
                logger.warning("No time_freq_support in RST")
                return []
            
            # Extract and sort time values
            times = [float(t) for t in tf_support.time_frequencies]
            times = sorted(set(times))
            
            if times:
                logger.info(
                    f"Probed {len(times)} steps "
                    f"(range: {times[0]:.6g} to {times[-1]:.6g})"
                )
            
            return times
            
        except Exception as e:
            logger.warning(f"RST probing failed: {e}")
            return []


class TimeResolver:
    """Resolve time selection against actual RST data."""
    
    @staticmethod
    def resolve(rst_path: Path, selection_str: str) -> TimeSelection:
        """
        Resolve time selection for RST file.
        
        Combines user selection string with actual RST time data to produce
        concrete index and time values.
        
        Args:
            rst_path: Path to RST file
            selection_str: Time selection string (e.g., "LAST", "INDEX:5")
            
        Returns:
            Resolved TimeSelection object
        """
        mode, value = TimeSelectionParser.parse(selection_str)
        available = RSTProbe.probe_times(rst_path)
        
        if not available:
            return TimeResolver._fallback_resolve(mode, value)
        
        # LAST mode: Use last available time
        if mode == TimeSelectionMode.LAST:
            last_time = available[-1]
            return TimeSelection(
                mode=mode,
                index=len(available),
                time=last_time,
                canonical=f"VALUE:{last_time:.10g}",
                available_times=available,
            )
        
        # INDEX mode: Use specific step index
        if mode == TimeSelectionMode.INDEX and value is not None:
            idx = int(value)
            if not (1 <= idx <= len(available)):
                logger.warning(
                    f"Index {idx} out of range [1..{len(available)}], using LAST"
                )
                idx = len(available)
            
            return TimeSelection(
                mode=mode,
                index=idx,
                time=available[idx - 1],
                canonical=f"INDEX:{idx}",
                available_times=available,
            )
        
        # VALUE mode: Find closest time match
        if mode == TimeSelectionMode.VALUE and value is not None:
            target = float(value)
            best_idx = min(
                range(len(available)),
                key=lambda i: abs(available[i] - target)
            )
            
            return TimeSelection(
                mode=mode,
                index=best_idx + 1,
                time=available[best_idx],
                canonical=f"VALUE:{available[best_idx]:.10g}",
                available_times=available,
            )
        
        return TimeResolver._fallback_resolve(mode, value)
    
    @staticmethod
    def _fallback_resolve(
        mode: TimeSelectionMode,
        value: Optional[float]
    ) -> TimeSelection:
        """
        Fallback resolution when RST probing unavailable.
        
        Args:
            mode: Time selection mode
            value: Numeric value (if applicable)
            
        Returns:
            Best-effort TimeSelection
        """
        if mode == TimeSelectionMode.INDEX and value is not None:
            idx = max(1, int(value))
            return TimeSelection(
                mode=mode,
                index=idx,
                canonical=f"INDEX:{idx}",
            )
        
        if mode == TimeSelectionMode.VALUE and value is not None:
            return TimeSelection(
                mode=mode,
                time=float(value),
                canonical=f"VALUE:{value:.10g}",
            )
        
        return TimeSelection(mode=TimeSelectionMode.LAST, canonical="LAST")


# ==============================================================================
# TEMPLATE RENDERING
# ==============================================================================

class TemplateRenderer:
    """IronPython script template rendering."""
    
    @staticmethod
    def render(template: str, context: Dict[str, str]) -> str:
        """
        Replace {{KEY}} placeholders with context values.
        
        Args:
            template: Template string with {{PLACEHOLDER}} tokens
            context: Dictionary of placeholder -> value mappings
            
        Returns:
            Rendered string with all placeholders replaced
        """
        rendered = template
        for key, value in context.items():
            rendered = rendered.replace(f"{{{{{key}}}}}", value)
        return rendered
    
    @staticmethod
    def build_context(
        paths: JobPaths,
        config: ExportConfig,
        time_sel: TimeSelection,
        cdb_units: UnitSystem,
        results: List[str],
    ) -> Dict[str, str]:
        """
        Build template rendering context.
        
        Creates dictionary mapping template placeholders to their values.
        All paths and strings are JSON-encoded for safe IronPython injection.
        
        Args:
            paths: Job file paths
            config: Export configuration
            time_sel: Resolved time selection
            cdb_units: CDB import units
            results: List of result names to export
            
        Returns:
            Dictionary of placeholder -> value
        """
        output_dir = paths.get_output_dir(config.output_mode)
        status_json = paths.get_status_json(config.output_mode)
        
        return {
            "CDB_PATH": json.dumps(str(paths.cdb_path)),
            "RST_PATH": json.dumps(str(paths.rst_path)),
            "OUT_DIR": json.dumps(str(output_dir)),
            "JOB": json.dumps(paths.name),
            "STATUS_JSON": json.dumps(str(status_json)),
            "IMPORT_UNITS_ENUM": json.dumps(cdb_units.to_mechanical_import()),
            "MODEL_RESULT_UNITS_ENUM": json.dumps(cdb_units.to_mechanical_display()),
            "DISPLAY_UNITS_ENUM": json.dumps(config.units_display.to_mechanical_display()),
            "TIME_SELECTED_MODE": json.dumps(time_sel.mode.value),
            "TIME_SELECTED_INDEX": json.dumps(time_sel.index or 0),
            "TIME_SELECTED_TIME": json.dumps(time_sel.time or 0.0),
            "TIME_SEL": json.dumps(time_sel.canonical),
            "TRUE_SCALE": "True" if config.true_scale else "False",
            "DEFORM_SCALE": str(config.deform_scale),
            "SHOW_THICKNESS": "True" if config.show_thickness else "False",
            "SHOW_MESH": "True" if config.show_mesh else "False",
            "DO_EVAL": "True" if config.evaluate_results else "False",
            "MAKE_AVZM": "True" if config.make_bundle else "False",
            "RESULTS_LIST_JSON": json.dumps(results),
            "VERBOSE": "True" if config.verbose else "False",
        }


# ==============================================================================
# WORKBENCH INTEGRATION
# ==============================================================================

class WorkbenchLauncher:
    """Workbench/RunWB2 execution manager."""
    
    # Workbench journal template for launching Mechanical with IronPython script
    JOURNAL_TEMPLATE = """\
SetScriptVersion(Version="22.2.192")

unitSystem1 = SetProjectUnitSystem(UnitSystemName={project_unit_system})

with open({mech_script_path}) as f:
    MechCMDS = f.read()

template_ext = GetTemplate(TemplateName="External Model")
system_ext = template_ext.CreateSystem()
setup_ext = system_ext.GetContainer(ComponentName="Setup")
externalModelFileData1 = setup_ext.AddDataFile(FilePath={cdb_path})

template_static = GetTemplate(TemplateName="Static Structural", Solver="ANSYS")
system_static = template_static.CreateSystem(Position="Right", RelativeTo=system_ext)

setupComponent_ext = system_ext.GetComponent(Name="Setup")
modelComponent_static = system_static.GetComponent(Name="Model")
setupComponent_ext.TransferData(TargetComponent=modelComponent_static)

modelComponent_static.Update(AllDependencies=True)

model_container = system_static.GetContainer(ComponentName="Model")
try:
    model_container.Edit(Hidden=True)
except:
    model_container.Edit()

model_container.SendCommand(Command=MechCMDS, Language="Python")
"""
    
    @staticmethod
    def resolve_runwb2_path(version: int, override: Optional[str]) -> Path:
        """
        Resolve RunWB2.exe path.
        
        Args:
            version: ANSYS version number
            override: Optional explicit path to RunWB2.exe
            
        Returns:
            Path to RunWB2.exe
            
        Raises:
            FileNotFoundError: If RunWB2.exe not found
            EnvironmentError: If ANSYS environment variable not set
        """
        if override:
            path = Path(override)
            if not path.is_file():
                raise FileNotFoundError(f"RunWB2 override not found: {path}")
            return path
        
        # Use environment variable to locate ANSYS installation
        env_var = f"ANSYS{version}_DIR"
        ansys_root = os.environ.get(env_var)
        if not ansys_root:
            raise EnvironmentError(f"Environment variable not set: {env_var}")
        
        # Navigate to Framework/bin directory
        v_root = Path(ansys_root).parent
        runwb2 = v_root / "Framework" / "bin" / "Win64" / "RunWB2.exe"
        
        if not runwb2.is_file():
            raise FileNotFoundError(f"RunWB2 not found: {runwb2}")
        
        return runwb2
    
    @classmethod
    def execute_mechanical_script(
        cls,
        version: int,
        runwb2_override: Optional[str],
        ipy_script: str,
        paths: JobPaths,
        units_display: UnitSystem,
        verbose: bool,
    ) -> None:
        """
        Execute IronPython script via Workbench journal.
        
        Creates temporary journal and script files, launches RunWB2 in batch mode,
        and cleans up temporary files based on verbose setting.
        
        Args:
            version: ANSYS version number
            runwb2_override: Optional explicit path to RunWB2.exe
            ipy_script: IronPython script content
            paths: Job file paths
            units_display: Display unit system
            verbose: Keep temporary files if True
            
        Raises:
            RuntimeError: If Workbench execution fails
        """
        runwb2_exe = cls.resolve_runwb2_path(version, runwb2_override)
        
        # Write IronPython script and journal files
        mech_script_path = paths.job_dir / "MechScript.py"
        journal_path = paths.job_dir / "main.wbjn"
        
        FileOperations.write_text_safe(mech_script_path, ipy_script)

        project_units_name = get_project_unit_system_name(units_display)
        
        journal = cls.JOURNAL_TEMPLATE.format(
            mech_script_path=repr(str(mech_script_path)),
            cdb_path=repr(str(paths.cdb_path)),
            rst_path=repr(str(paths.rst_path)),
            project_unit_system=repr(project_units_name)
        )
        FileOperations.write_text_safe(journal_path, journal)
        
        # Execute RunWB2 in batch mode
        cmd = [str(runwb2_exe), "-B", "-R", str(journal_path)]
        logger.info(f"Executing: {' '.join(cmd)}")
        
        result = subprocess.run(
            cmd,
            cwd=str(paths.job_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        
        if verbose:
            # сюда прилетают все [IPY-INFO]/[IPY-DEBUG] из mech_export_avz.ipy
            logger.info("Mechanical stdout:\n" + result.stdout)
            if result.stderr:
                logger.info("Mechanical stderr:\n" + result.stderr)

        if result.returncode != 0:
            logger.error(f"RunWB2 failed (code {result.returncode})")
            logger.debug(f"stdout: {result.stdout}")
            logger.debug(f"stderr: {result.stderr}")
            raise RuntimeError("Workbench execution failed")
        
        # Cleanup temporary files (depends on verbose mode)
        if not verbose:
            safe_remove(journal_path)
            safe_remove(mech_script_path)


# ==============================================================================
# BUNDLE CREATION
# ==============================================================================

class AVZMBundler:
    """AVZM bundle (zip archive) creation."""
    
    @staticmethod
    def create_bundle(avz_files: List[Path], avzm_path: Path) -> None:
        """
        Create AVZM zip archive from AVZ files.
        
        AVZM is simply a ZIP file containing multiple AVZ files for
        convenient distribution and loading in visualization tools.
        
        Args:
            avz_files: List of AVZ file paths to include
            avzm_path: Target AVZM (zip) file path
        """
        if not avz_files:
            logger.debug("No AVZ files to bundle")
            return
        
        FileOperations.ensure_directory(avzm_path.parent)
        
        with zipfile.ZipFile(avzm_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for avz in avz_files:
                if avz.is_file():
                    # Store with filename only (no directory structure)
                    zf.write(avz, avz.name)
        
        logger.info(f"Created bundle: {avzm_path}")


# ==============================================================================
# JOB PROCESSING
# ==============================================================================

class JobProcessor:
    """Single job processing logic."""
    
    def __init__(self, config: ExportConfig, template: str):
        """
        Initialize job processor.
        
        Args:
            config: Export configuration
            template: IronPython template string
        """
        self.config = config
        self.template = template
    
    def process_job(self, job_name: str) -> ExportResult:
        """
        Process a single job (complete workflow).
        
        Args:
            job_name: Name of job to process
            
        Returns:
            ExportResult with success status and file list
        """
        paths = JobPaths(name=job_name, base_dir=self.config.base_dir)
        
        logger.info("=" * 60)
        logger.info(f"Job: {job_name}")
        logger.info(f"Directory: {paths.job_dir}")
        
        try:
            # Step 1: Validate files exist
            self._validate_files(paths)
            
            # Step 2: Resolve unit systems
            cdb_units = self._resolve_units(paths)
            logger.info(f"CDB units: {cdb_units.value}")
            logger.info(f"Display units: {self.config.units_display.value}")
            
            # Step 3: Resolve time selection
            time_sel = TimeResolver.resolve(paths.rst_path, self.config.time_selection)
            logger.info(f"Time selection: {time_sel}")
            
            # Step 4: Get result list
            results = self.config.results_preset.get_result_names(
                self.config.custom_results
            )
            logger.info(f"Results: {', '.join(results)}")
            
            # Step 5: Prepare output directory
            output_dir = paths.get_output_dir(self.config.output_mode)
            FileOperations.ensure_directory(output_dir)
            
            # Step 6: Render IronPython script
            context = TemplateRenderer.build_context(
                paths, self.config, time_sel, cdb_units, results
            )
            ipy_script = TemplateRenderer.render(self.template, context)
            
            # Step 7: Execute (or skip if dry run)
            if self.config.dry_run:
                logger.info("Dry run: skipping execution")
                return ExportResult(job_name=job_name, success=True)
            
            logger.info("Executing in Mechanical...")
            WorkbenchLauncher.execute_mechanical_script(
                self.config.version,
                self.config.runwb2_override,
                ipy_script,
                paths,
                self.config.units_display,
                self.config.verbose,
            )
            
            # Step 8: Collect results from status JSON
            status_path = paths.get_status_json(self.config.output_mode)
            status = self._read_status(status_path)
            
            if not status.get("ok", False):
                error = status.get("error", "Unknown error")
                logger.error(f"Mechanical reported failure: {error}")
                return ExportResult(
                    job_name=job_name,
                    success=False,
                    error_message=error,
                )
            
            # Extract AVZ file paths
            files_raw = status.get("avz") or status.get("files") or []
            avz_files = [Path(p) for p in files_raw]

            logger.info(f"Exported {len(avz_files)} AVZ file(s)")
            
            # Step 9: Create bundle if requested
            if self.config.make_bundle and avz_files:
                avzm_path = output_dir / f"{job_name}.avzm"
                AVZMBundler.create_bundle(avz_files, avzm_path)
            
            return ExportResult(
                job_name=job_name,
                success=True,
                avz_files=avz_files,
            )
            
        except Exception as e:
            logger.error(f"Job failed: {e}")
            return ExportResult(
                job_name=job_name,
                success=False,
                error_message=str(e),
            )
    
    def _validate_files(self, paths: JobPaths) -> None:
        """
        Validate required files exist.
        
        Ensures RST exists and CDB exists or can be generated from DB.
        
        Args:
            paths: Job file paths
            
        Raises:
            FileNotFoundError: If required files missing
        """
        if not paths.rst_path.exists():
            raise FileNotFoundError(f"RST not found: {paths.rst_path}")
        
        if not MAPDLRunner.generate_cdb_from_db(
            paths.cdb_path,
            paths.db_path,
            self.config.version,
            self.config.verbose,
        ):
            raise FileNotFoundError("CDB generation failed")
    
    def _resolve_units(self, paths: JobPaths) -> UnitSystem:
        """
        Resolve CDB import units.
        
        Uses auto-detection if configured, otherwise uses explicit or display units.
        
        Args:
            paths: Job file paths
            
        Returns:
            Resolved UnitSystem
        """
        if self.config.units_cdb.upper() == "AUTO":
            detected = CDBAnalyzer.detect_units(paths.cdb_path)
            if detected:
                logger.info(f"Auto-detected CDB units: {detected.value}")
                return detected
            
            logger.warning("Auto-detection failed, using display units")
            return self.config.units_display
        
        try:
            return UnitSystem.from_string(self.config.units_cdb)
        except ValueError as e:
            logger.error(f"{e}, falling back to display units")
            return self.config.units_display
    
    def _read_status(self, status_path: Path) -> Dict[str, Any]:
        """
        Read Mechanical status JSON.
        
        Args:
            status_path: Path to status JSON file
            
        Returns:
            Parsed JSON dictionary
            
        Raises:
            FileNotFoundError: If status file not found
        """
        if not status_path.is_file():
            raise FileNotFoundError(f"Status JSON not found: {status_path}")
        
        return json.loads(status_path.read_text(encoding="utf-8"))


# ==============================================================================
# BATCH EXECUTION
# ==============================================================================

class BatchExecutor:
    """Batch job executor with summary reporting."""
    
    def __init__(self, config: ExportConfig):
        """
        Initialize batch executor.
        
        Args:
            config: Export configuration
        """
        self.config = config
        self.template = self._load_template()
        self.processor = JobProcessor(config, self.template)
    
    def _load_template(self) -> str:
        """
        Load IronPython template from file.
        
        Returns:
            Template string
            
        Raises:
            FileNotFoundError: If template file not found
        """
        template = FileOperations.read_text_safe(self.config.template_path)
        if not template:
            raise FileNotFoundError(
                f"Template not found: {self.config.template_path}"
            )
        return template
    
    def execute(self) -> int:
        """
        Execute batch export for all jobs.
        
        Returns:
            Exit code (0 = success, 2 = some failures)
        """
        logger.info(f"Processing {len(self.config.jobs)} job(s)")
        
        results: List[ExportResult] = []
        
        # Process each job sequentially
        for job_name in self.config.jobs:
            result = self.processor.process_job(job_name)
            results.append(result)
        
        # Generate summary
        successful = [r for r in results if r.success]
        failed = [r for r in results if not r.success]
        total_avz = sum(len(r.avz_files) for r in successful)
        
        logger.info("=" * 60)
        logger.info("BATCH SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Total jobs:    {len(results)}")
        logger.info(f"Successful:    {len(successful)}")
        logger.info(f"Failed:        {len(failed)}")
        logger.info(f"AVZ exported:  {total_avz}")
        
        if failed:
            logger.info("")
            logger.info("Failed jobs:")
            for r in failed:
                logger.info(f"  - {r.job_name}: {r.error_message}")
        
        return 2 if failed else 0


# ==============================================================================
# CLI INTERFACE
# ==============================================================================

class CLIInterface:
    """Command-line interface builder."""
    
    @staticmethod
    def create_parser() -> argparse.ArgumentParser:
        """
        Create argument parser with all CLI options.
        
        Returns:
            Configured ArgumentParser
        """
        parser = argparse.ArgumentParser(
            description="Export ANSYS CDB+RST to AVZ via Mechanical",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="""
Examples:
  %(prog)s --base D:\\ansys --list jobs.txt --version 222
  %(prog)s --base D:\\ansys --list jobs.txt --units-out BIN --time "INDEX:5"
  %(prog)s --base D:\\ansys --list jobs.txt --results-list "Total Deformation,Equivalent Stress"
""",
        )
        
        # === Required Arguments ===
        parser.add_argument(
            "--base",
            type=Path,
            required=True,
            help="Base directory containing job folders",
        )
        parser.add_argument(
            "--list",
            type=Path,
            required=True,
            help="Job list file (one name per line, # for comments)",
        )
        
        # === Version ===
        parser.add_argument(
            "--version",
            type=int,
            default=222,
            help="ANSYS version (e.g., 221, 222, 241)",
        )
        
        # === Unit Systems ===
        parser.add_argument(
            "--units-out",
            default="BIN",
            choices=[u.value for u in UnitSystem],
            help="Display unit system for results",
        )
        parser.add_argument(
            "--cdb-units",
            default="AUTO",
            help='CDB import units (e.g., BIN, NMM) or "AUTO" for detection',
        )
        
        # === Results ===
        parser.add_argument(
            "--results-preset",
            default="default",
            choices=[p.value for p in ResultsPreset],
            help="Result preset (default, minimal, extended, custom)",
        )
        parser.add_argument(
            "--results-list",
            default="",
            help="Custom results (comma-separated, e.g., 'Total Deformation:X,Equivalent Stress')",
        )
        
        # === Time Selection ===
        parser.add_argument(
            "--time",
            default="LAST",
            help="Time selection: LAST | INDEX:n | VALUE:t | t",
        )
        
        # === Visualization ===
        parser.add_argument(
            "--true-scale",
            type=int,
            default=1,
            choices=[0, 1],
            help="1 = true scale deformation, 0 = auto scale",
        )
        parser.add_argument(
            "--deform-scale",
            type=float,
            default=1.0,
            help="Deformation scale multiplier (e.g., 1.0, 0.5, 2.0)",
        )
        parser.add_argument(
            "--show-thickness",
            type=int,
            default=0,
            choices=[0, 1],
            help="1 = show shell/beam thickness, 0 = hide",
        )
        parser.add_argument(
            "--show-mesh",
            type=int,
            default=0,
            choices=[0, 1],
            help="1 = show mesh, 0 = hide",
        )
        
        # === Output ===
        parser.add_argument(
            "--out-mode",
            default="root",
            choices=[m.value for m in OutputMode],
            help="Output location: root (job folder) | subdir (job/avz)",
        )
        parser.add_argument(
            "--make-avzm",
            type=int,
            default=1,
            choices=[0, 1],
            help="1 = create AVZM bundle, 0 = skip",
        )
        parser.add_argument(
            "--do-eval",
            type=int,
            default=1,
            choices=[0, 1],
            help="1 = evaluate results in Mechanical, 0 = skip",
        )
        
        # === Advanced Paths ===
        parser.add_argument(
            "--ipy",
            dest="ipy_template",
            default="",
            help="Custom IronPython template path (default: mech_export_avz.ipy)",
        )
        parser.add_argument(
            "--mech-exe",
            default="",
            help="Custom RunWB2.exe path (default: auto-detect from ANSYS{VERSION}_DIR)",
        )
        
        # === Execution Mode ===
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Dry run mode: prepare but don't execute",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Enable verbose logging (DEBUG level)",
        )
        
        return parser
    
    @staticmethod
    def build_config(args: argparse.Namespace) -> ExportConfig:
        """
        Build configuration from parsed arguments.
        
        Args:
            args: Parsed command-line arguments
            
        Returns:
            ExportConfig object
            
        Raises:
            ValueError: If configuration is invalid
        """
        # Validate paths
        if not args.base.is_dir():
            raise ValueError(f"Base directory not found: {args.base}")
        
        if not args.list.is_file():
            raise ValueError(f"Job list not found: {args.list}")
        
        # Parse job list
        jobs = FileOperations.parse_list_file(args.list)
        if not jobs:
            raise ValueError(f"Empty job list: {args.list}")
        
        # Resolve template path
        if args.ipy_template:
            template_path = Path(args.ipy_template)
        else:
            script_dir = Path(__file__).parent
            template_path = script_dir / "mech_export_avz.ipy"
        
        if not template_path.is_file():
            raise ValueError(f"Template not found: {template_path}")
        
        # Parse enums
        units_display = UnitSystem.from_string(args.units_out)
        results_preset = ResultsPreset(args.results_preset)
        output_mode = OutputMode(args.out_mode)
        
        # Parse custom results
        custom_results = FileOperations.split_delimited(args.results_list)
        if results_preset == ResultsPreset.CUSTOM and not custom_results:
            raise ValueError("CUSTOM preset requires --results-list")
        
        return ExportConfig(
            base_dir=args.base,
            jobs=jobs,
            version=args.version,
            units_display=units_display,
            units_cdb=args.cdb_units,
            results_preset=results_preset,
            custom_results=custom_results,
            time_selection=args.time,
            true_scale=bool(args.true_scale),
            deform_scale=args.deform_scale,
            show_thickness=bool(args.show_thickness),
            show_mesh=bool(args.show_mesh),
            output_mode=output_mode,
            make_bundle=bool(args.make_avzm),
            evaluate_results=bool(args.do_eval),
            template_path=template_path,
            runwb2_override=args.mech_exe or None,
            dry_run=args.dry_run,
            verbose=bool(args.verbose),
        )


# ==============================================================================
# APPLICATION ENTRY POINT
# ==============================================================================

class Application:
    """Main application controller."""
    
    @staticmethod
    def run(argv: Optional[Sequence[str]] = None) -> int:
        """
        Run application main workflow.
        
        Args:
            argv: Command-line arguments (None = use sys.argv)
            
        Returns:
            Exit code (0 = success, non-zero = failure)
        """
        try:
            # Parse command-line arguments
            parser = CLIInterface.create_parser()
            args = parser.parse_args(argv)
            
            # Setup logging with verbosity level
            global logger
            logger = LoggerSetup.get_logger(verbose=args.verbose)
            
            # Build configuration from arguments
            config = CLIInterface.build_config(args)
            
            # Log configuration summary
            Application._log_config(config)
            
            # Execute batch export
            executor = BatchExecutor(config)
            return executor.execute()
            
        except KeyboardInterrupt:
            logger.warning("Interrupted by user")
            return 130
        
        except Exception as e:
            logger.error(f"Fatal error: {e}")
            if logger.level == logging.DEBUG:
                import traceback
                logger.debug(traceback.format_exc())
            return 1
    
    @staticmethod
    def _log_config(config: ExportConfig) -> None:
        """
        Log configuration summary.
        
        Args:
            config: Export configuration to log
        """
        logger.info("=" * 60)
        logger.info("CONFIGURATION")
        logger.info("=" * 60)
        logger.info(f"Base directory: {config.base_dir}")
        logger.info(f"Jobs: {len(config.jobs)}")
        logger.info(f"ANSYS version: {config.version}")
        logger.info(f"Units: CDB={config.units_cdb}, Display={config.units_display.value}")
        logger.info(f"Results: {config.results_preset.value}")
        if config.results_preset == ResultsPreset.CUSTOM:
            logger.info(f"Custom results: {config.custom_results}")
        logger.info(f"Time: {config.time_selection}")
        logger.info(f"Deformation: scale={config.true_scale}, factor={config.deform_scale}")
        logger.info(f"Thickness display: {config.show_thickness}")
        logger.info(f"Mesh display: {config.show_mesh}")
        logger.info(f"Output: mode={config.output_mode.value}, bundle={config.make_bundle}")
        logger.info(f"Evaluate: {config.evaluate_results}")
        logger.info(f"Template: {config.template_path}")
        logger.info(f"Dry run: {config.dry_run}")
        logger.info(f"Verbose: {config.verbose}")
        logger.info("=" * 60)


# ==============================================================================
# MAIN
# ==============================================================================

def main(argv: Optional[Sequence[str]] = None) -> int:
    """
    Main entry point.
    
    Args:
        argv: Command-line arguments
        
    Returns:
        Exit code
    """
    return Application.run(argv)


if __name__ == "__main__":
    sys.exit(main())