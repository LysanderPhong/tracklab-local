"""Keep SDK caches inside the project, without changing HOME or system config.

This one pinned-version adapter overrides the SDK cache root before CLI imports.
Its behavior must be rechecked before upgrading pymobiledevice3.
"""
from pathlib import Path
import runpy

import pymobiledevice3.common as common

common._HOMEFOLDER = Path(__file__).resolve().parent / "runtime" / "sdk-cache"

if __name__ == "__main__":
    runpy.run_module("pymobiledevice3", run_name="__main__")
