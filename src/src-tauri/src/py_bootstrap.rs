// Copyright 2026 Ferran Duarri , GPL v2
// Shared Python sys.path bootstrap for all subprocesses.
//
// Tries the installed path first (/usr/local/lib/greenboost-gaming).
// The dev fallback uses CARGO_MANIFEST_DIR at compile time , no
// hard-coded developer home paths in installed code.

/// Compile-time project root: CARGO_MANIFEST_DIR is src/src-tauri,
/// so /../.. goes up two levels to greenboost_gaming/.
const DEV_ROOT: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../..");

/// Compile-time path to the sibling core `greenboost` checkout
/// (`greenboost_all/greenboost_gaming/src/src-tauri` → `../../../greenboost`).
/// Dev-only fallback for `import gb_dataflux` etc.; installed code always
/// finds core at `/usr/local/lib/greenboost` (GB_PY_ROOT) first , see
/// `greenboost_setup.sh`'s "Install Python files" step, which is where
/// gb_dataflux.py and the rest of the orchestration stack actually land.
const DEV_CORE_ROOT: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../../../greenboost");

/// Returns a Python snippet that inserts the installed + dev-tree roots for
/// both this suite's own modules (gb_gaming) and core GreenBoost's
/// orchestration stack (gb_dataflux, gb_cluster, ...) into sys.path , the
/// gaming layers and core share one dataflux log
/// (~/.local/share/greenboost/dataflux.jsonl) and one import surface.
pub fn py_bootstrap() -> String {
    format!(
        "import sys\n\
         sys.path.insert(0, '/usr/local/lib/greenboost-gaming')\n\
         sys.path.insert(0, '{}')\n\
         sys.path.insert(0, '/usr/local/lib/greenboost')\n\
         sys.path.insert(0, '{}')\n",
        DEV_ROOT, DEV_CORE_ROOT
    )
}
