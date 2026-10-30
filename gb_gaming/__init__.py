# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 Ferran Duarri.
"""gb_gaming , GreenBoost Gaming Suite backend modules.

These modules contain the heavy logic the Tauri GUI's Rust backend shells
out to via `python3 -c` (see src/src-tauri/src/py_bootstrap.rs):

  • game_scanner , parses ~/.steam libraryfolders.vdf, enumerates installed
    apps, extracts metadata (name, install_dir, last_played, has_dlss_dll).
  • dlss_updater , finds DLSS / FSR / XeSS DLLs across all Proton prefixes
    and compares against the latest known versions.
  • gpu_profile  , reads / writes GPU clock / power / fan via nvidia-smi
    and sysfs.  Reading is always safe; writing requires CAP_SYS_NICE or
    root.

Each module exports a small, JSON-shaped API so the same backend can feed
the Tauri GUI and, in the future, a CLI / IPC server. (Tauri is the only
GUI , the earlier GTK4 fallback was removed 2026-08-20.)
"""
__all__ = ["game_scanner", "dlss_updater", "gpu_profile"]
