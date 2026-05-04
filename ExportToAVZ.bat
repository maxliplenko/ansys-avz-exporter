@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM ============================================================================
REM ANSYS AVZ/AVZM Batch Exporter - Launcher Script
REM ============================================================================
REM
REM This script launches the Python-based AVZ exporter for ANSYS Mechanical.
REM It processes multiple jobs in batch, converting CDB+RST to AVZ visualization.
REM
REM Architecture:
REM   - This batch file: Configuration and launcher
REM   - export_avz_from_rst.py: Python 3 orchestrator
REM   - mech_export_avz.ipy: IronPython 2.7 template (executed in Mechanical)
REM
REM Requirements:
REM   - Python 3.x with ansys-mechanical-core
REM   - ANSYS Mechanical installation
REM   - Environment variable: ANSYS{VERSION}_DIR
REM
REM ============================================================================

REM ----------------------------------------------------------------------------
REM CONFIGURATION SECTION
REM ----------------------------------------------------------------------------

REM === Directories ===
REM BASE: Root directory containing job folders
REM SCRIPTS: Directory containing Python scripts and templates
set "BASE=D:\ansys"
set "SCRIPTS=D:\ansys\Scripts"

REM === Job List ===
REM Text file listing job folder names (one per line, # for comments)
set "LIST=%SCRIPTS%\out_list.txt"

REM === Python Executable ===
REM Path to Python 3 interpreter (or just "python" if in PATH)
set "PYTHON=python"

REM === ANSYS Version ===
REM Mechanical version number (e.g., 241 = 2024R1, 242 = 2024R2)
set "MECH_VER=241"

REM === Script Paths ===
REM Python orchestrator script
set "SCRIPT=%SCRIPTS%\export_avz_from_rst.py"

REM IronPython template (optional override)
REM Leave empty to use default location (same directory as Python script)
set "IPY_TEMPLATE=%SCRIPTS%\mech_export_avz.ipy"

REM ----------------------------------------------------------------------------
REM UNIT SYSTEM CONFIGURATION
REM ----------------------------------------------------------------------------

REM === CDB Import Units ===
REM Units for CDB geometry import
REM   AUTO: Auto-detect from CDB /UNITS directive (recommended)
REM   BIN, BFT, NMM, MKS, CGS, NMMdat, NMMTon, UMKS: Explicit unit system
REM
REM IMPORTANT: Must match the unit system used by MAPDL solver
set "CDB_UNITS=AUTO"

REM === Display/Result Units ===
REM Unit system for result visualization (stress, displacement legends)
REM   BIN, BFT, NMM, MKS, CGS, NMMdat, NMMTon, UMKS
REM
REM NOTE: This only affects display units, NOT geometry scale
set "UNITS_OUT=BIN"

REM ----------------------------------------------------------------------------
REM RESULT CONFIGURATION
REM ----------------------------------------------------------------------------

REM === Result Preset ===
REM Predefined sets of result objects to export
REM   default: Total Deformation, Equivalent Stress
REM   minimal: Total Deformation only
REM   extended: Total Deformation, Equivalent Stress, Total Strain, Plastic Strain
REM   custom: Use RESULTS_LIST below
set "RESULTS_PRESET=custom"

REM === Custom Result List ===
REM Comma or semicolon separated list of result names
REM Only used when RESULTS_PRESET=custom
REM
REM Examples:
REM   - "Total Deformation"
REM   - "Equivalent Stress"
REM   - "Equivalent Total Strain"
REM   - "Equivalent Plastic Strain"
REM   - "Directional Deformation:Y"  (with axis specification)
set "RESULTS_LIST=Equivalent Stress"

REM ----------------------------------------------------------------------------
REM TIME/RESULT SET SELECTION
REM ----------------------------------------------------------------------------

REM === Time Selection ===
REM Specify which result set (time step) to export
REM
REM Formats:
REM   LAST            - Use last available time step (default)
REM   INDEX:n         - Use nth time step (1-based index)
REM   VALUE:t         - Use time step closest to value t
REM   t               - Legacy: plain number treated as VALUE:t
REM
REM Examples:
REM   set "TIME_SEL=LAST"
REM   set "TIME_SEL=INDEX:5"
REM   set "TIME_SEL=VALUE:0.25"
REM   set "TIME_SEL=0.25"
set "TIME_SEL=VALUE:0.25"

REM ----------------------------------------------------------------------------
REM VISUALIZATION SETTINGS
REM ----------------------------------------------------------------------------

REM === Deformation Scale Mode ===
REM Control how deformation is scaled in visualization
REM   1: True Scale (actual model units, use DEFORM_SCALE multiplier)
REM   0: Auto Scale (Mechanical automatic scaling)
set "TRUE_SCALE=1"

REM === Deformation Scale Multiplier ===
REM Multiplier for True Scale mode (only used if TRUE_SCALE=1)
REM
REM Examples:
REM   1.0   - No scaling (actual deformation)
REM   0.1   - 10% of actual deformation
REM   5.0   - 5x actual deformation
set "DEFORM_SCALE=1.0"

REM ----------------------------------------------------------------------------
REM OUTPUT CONFIGURATION
REM ----------------------------------------------------------------------------

REM === Output Directory Mode ===
REM Control where AVZ files are written
REM   root: Write to job directory (D:\ansys\job_name\*.avz)
REM   subdir: Write to subdirectory (D:\ansys\job_name\avz\*.avz)
set "OUT_MODE=root"

REM === AVZM Bundle Creation ===
REM Create AVZM archive containing all AVZ files
REM   1: Create bundle (job_name.avzm)
REM   0: Skip bundle creation
set "MAKE_AVZM=0"

REM ----------------------------------------------------------------------------
REM EXECUTION OPTIONS
REM ----------------------------------------------------------------------------

REM === Result Evaluation ===
REM Evaluate results in Mechanical before export
REM   1: Evaluate all results (recommended, ensures data is computed)
REM   0: Skip evaluation (faster but may have incomplete data)
set "DO_EVAL=1"

REM === Dry Run Mode ===
REM Preview what would be done without executing
REM   1: Dry run (no Mechanical launch, just show what would happen)
REM   0: Normal execution
set "DRY_RUN=0"

REM === Verbose Logging ===
REM Enable detailed debug logging
REM   1: Verbose mode (DEBUG level)
REM   0: Normal mode (INFO level)
set "VERBOSE=0"

REM ============================================================================
REM VALIDATION SECTION
REM ============================================================================

echo.
echo ========================================
echo ANSYS AVZ Batch Exporter
echo ========================================
echo.

REM --- Change to scripts directory ---
cd /d "%SCRIPTS%" 2>nul
if errorlevel 1 (
    echo [ERROR] Cannot access scripts directory: %SCRIPTS%
    pause
    exit /b 1
)

REM --- Validate Python script ---
if not exist "%SCRIPT%" (
    echo [ERROR] Python export script not found:
    echo         %SCRIPT%
    echo.
    echo Please check SCRIPTS path configuration.
    pause
    exit /b 2
)

REM --- Validate job list ---
if not exist "%LIST%" (
    echo [ERROR] Job list file not found:
    echo         %LIST%
    echo.
    echo Please create a job list file with one job name per line.
    pause
    exit /b 2
)

REM --- Validate IPY template (if specified) ---
if not "%IPY_TEMPLATE%"=="" (
    if not exist "%IPY_TEMPLATE%" (
        echo [ERROR] IronPython template not found:
        echo         %IPY_TEMPLATE%
        echo.
        echo Either provide correct path or leave IPY_TEMPLATE empty for auto-detection.
        pause
        exit /b 2
    )
)

REM --- Validate base directory ---
if not exist "%BASE%" (
    echo [WARNING] Base directory not found: %BASE%
    echo           Jobs may fail if directories don't exist.
    echo.
)

REM --- Validate ANSYS environment ---
set "ANSYS_ENV=ANSYS%MECH_VER%_DIR"
call set "ANSYS_DIR=%%%ANSYS_ENV%%%"
if "%ANSYS_DIR%"=="" (
    echo [WARNING] Environment variable not set: %ANSYS_ENV%
    echo           Make sure ANSYS %MECH_VER% is installed.
    echo.
)

REM ============================================================================
REM CONFIGURATION SUMMARY
REM ============================================================================

echo === Configuration ===
echo.
echo [Directories]
echo   BASE          : %BASE%
echo   SCRIPTS       : %SCRIPTS%
echo   LIST          : %LIST%
echo.
echo [ANSYS]
echo   VERSION       : %MECH_VER%
echo   PYTHON        : %PYTHON%
echo   %ANSYS_ENV%   : %ANSYS_DIR%
echo.
echo [Unit Systems]
echo   CDB_UNITS     : %CDB_UNITS%
echo   UNITS_OUT     : %UNITS_OUT%
echo.
echo [Results]
echo   PRESET        : %RESULTS_PRESET%
if /I "%RESULTS_PRESET%"=="custom" (
    echo   LIST          : %RESULTS_LIST%
)
echo.
echo [Time Selection]
echo   TIME_SEL      : %TIME_SEL%
echo.
echo [Visualization]
echo   TRUE_SCALE    : %TRUE_SCALE%
echo   DEFORM_SCALE  : %DEFORM_SCALE%
echo.
echo [Output]
echo   OUT_MODE      : %OUT_MODE%
echo   MAKE_AVZM     : %MAKE_AVZM%
echo   DO_EVAL       : %DO_EVAL%
echo.
echo [Execution]
echo   DRY_RUN       : %DRY_RUN%
echo   VERBOSE       : %VERBOSE%
echo.
echo [Scripts]
echo   SCRIPT        : %SCRIPT%
if not "%IPY_TEMPLATE%"=="" (
    echo   IPY_TEMPLATE  : %IPY_TEMPLATE%
)
echo.
echo ========================================
echo.

REM ============================================================================
REM BUILD COMMAND LINE ARGUMENTS
REM ============================================================================

set "ARGS="

REM --- Required arguments ---
call :AddArg --base "%BASE%"
call :AddArg --list "%LIST%"
call :AddArg --version "%MECH_VER%"

REM --- Unit systems ---
call :AddArg --cdb-units "%CDB_UNITS%"
call :AddArg --units-out "%UNITS_OUT%"

REM --- Results ---
call :AddArg --results-preset "%RESULTS_PRESET%"
if /I "%RESULTS_PRESET%"=="custom" (
    if "%RESULTS_LIST%"=="" (
        echo [ERROR] RESULTS_PRESET=custom but RESULTS_LIST is empty
        pause
        exit /b 2
    )
    call :AddArg --results-list "%RESULTS_LIST%"
)

REM --- Time selection ---
call :AddArg --time "%TIME_SEL%"

REM --- Visualization ---
call :AddArg --true-scale "%TRUE_SCALE%"
call :AddArg --deform-scale "%DEFORM_SCALE%"

REM --- Output ---
call :AddArg --out-mode "%OUT_MODE%"
call :AddArg --make-avzm "%MAKE_AVZM%"
call :AddArg --do-eval "%DO_EVAL%"

REM --- Template ---
if not "%IPY_TEMPLATE%"=="" (
    call :AddArg --ipy "%IPY_TEMPLATE%"
)

REM --- Execution options ---
if "%DRY_RUN%"=="1" (
    call :AddArg --dry-run
)
if "%VERBOSE%"=="1" (
    call :AddArg --verbose
)

REM ============================================================================
REM EXECUTE PYTHON SCRIPT
REM ============================================================================

echo [RUN] %PYTHON% "%SCRIPT%" %ARGS%
echo.
echo ========================================
echo Starting export...
echo ========================================
echo.

"%PYTHON%" "%SCRIPT%" %ARGS%

set "RC=%ERRORLEVEL%"

REM ============================================================================
REM COMPLETION
REM ============================================================================

echo.
echo ========================================
if %RC%==0 (
    echo Export completed successfully
) else if %RC%==2 (
    echo Export completed with failures
) else (
    echo Export failed with errors
)
echo Exit code: %RC%
echo ========================================
echo.

pause
exit /b %RC%

REM ============================================================================
REM HELPER SUBROUTINE: Add argument to command line
REM ============================================================================
REM Usage:
REM   call :AddArg --flag "value"    - Add flag with value
REM   call :AddArg --flag            - Add flag without value
REM ============================================================================
:AddArg
    if "%~1"=="" goto :eof
    
    if "%~2"=="" (
        REM Flag without value
        set "ARGS=%ARGS% %~1"
    ) else (
        REM Flag with value (quoted)
        set "ARGS=%ARGS% %~1 "%~2""
    )
goto :eof

REM ============================================================================
REM END OF SCRIPT
REM ============================================================================