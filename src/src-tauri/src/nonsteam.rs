// Copyright 2026 Ferran Duarri , GPL v2
// GreenBoost Gaming Suite , non-Steam game discovery.
//
// Two parsers, no dependencies beyond std:
//
//   1. `shortcuts.vdf` , Steam's binary KeyValues file listing every
//      "Add a Non-Steam Game" entry. This is what makes a Battle.net /
//      Epic / itch launcher living inside a Proton prefix visible at all;
//      the Steam library walk in scanner.rs cannot see them, because they
//      have no appmanifest and live under compatdata, not steamapps/common.
//
//   2. Battle.net's own `product.db` , a protobuf database Blizzard keeps
//      INSIDE the prefix, listing the games actually installed under the
//      launcher. Reading it is the difference between showing the user one
//      "Battle.net" tile and showing them the games they can play. The
//      schema below mirrors Lutris' decoder (lutris/util/battlenet/
//      product_db.py) field-for-field , same wire fields, reimplemented,
//      not copied (Lutris is GPLv3, this file is GPL-2.0).
//
// Both parsers are total: malformed input yields fewer entries, never a
// panic. Steam rewrites shortcuts.vdf in place and can be caught mid-write.

use std::path::{Path, PathBuf};

// ── shortcuts.vdf ─────────────────────────────────────────────────────
//
// Binary KeyValues. Each token is a type byte, then a NUL-terminated key,
// then a payload that depends on the type:
//
//   0x00  nested map , contents follow until a matching 0x08
//   0x01  string      , NUL-terminated value
//   0x02  int32       , 4 bytes little-endian
//   0x08  end of the current map
//
// Layout is  shortcuts -> "0","1","2"… -> the fields below.

#[derive(Debug, Clone, Default)]
pub struct Shortcut {
    /// Steam's own 32-bit id for the shortcut. Also the compatdata
    /// directory name for its Proton prefix.
    pub appid: u32,
    pub app_name: String,
    /// Linux-side path to the .exe, already unquoted.
    pub exe: String,
    pub start_dir: String,
    pub launch_options: String,
    pub icon: String,
    pub is_hidden: bool,
}

/// Every `shortcuts.vdf` on this machine, one per Steam user, deduplicated
/// by canonical path , `~/.steam/steam` is normally a symlink into
/// `~/.local/share/Steam`, so the naive glob returns each file twice.
pub fn shortcut_files() -> Vec<PathBuf> {
    let home = match std::env::var("HOME") { Ok(h) => h, Err(_) => return Vec::new() };
    let mut seen: Vec<PathBuf> = Vec::new();
    for root in [
        format!("{home}/.local/share/Steam/userdata"),
        format!("{home}/.steam/steam/userdata"),
        format!("{home}/.steam/root/userdata"),
    ] {
        let Ok(users) = std::fs::read_dir(&root) else { continue };
        for user in users.flatten() {
            let f = user.path().join("config").join("shortcuts.vdf");
            if !f.is_file() { continue; }
            let canon = std::fs::canonicalize(&f).unwrap_or(f);
            if !seen.contains(&canon) { seen.push(canon); }
        }
    }
    seen
}

/// Read a NUL-terminated string starting at `i`, advancing `i` past the NUL.
fn read_cstr(data: &[u8], i: &mut usize) -> String {
    let start = *i;
    while *i < data.len() && data[*i] != 0 { *i += 1; }
    let s = String::from_utf8_lossy(&data[start..*i]).into_owned();
    if *i < data.len() { *i += 1; } // consume the NUL
    s
}

/// Steam quotes the Exe field (`"/path/to/game.exe"`). Callers want a path.
fn unquote(s: &str) -> String {
    s.trim().trim_matches('"').to_string()
}

pub fn parse_shortcuts(data: &[u8]) -> Vec<Shortcut> {
    let mut out: Vec<Shortcut> = Vec::new();
    let mut i = 0usize;
    let mut depth = 0i32;
    // Fields accumulate into `cur` while we are inside one shortcut entry
    // (depth 2: shortcuts -> "<n>" -> fields).
    let mut cur = Shortcut::default();
    let mut in_entry = false;

    while i < data.len() {
        let ty = data[i];
        i += 1;
        match ty {
            0x00 => {
                let _key = read_cstr(data, &mut i);
                depth += 1;
                if depth == 2 {
                    cur = Shortcut::default();
                    in_entry = true;
                }
            }
            0x08 => {
                if depth == 2 && in_entry {
                    // An entry with neither a name nor an exe is a
                    // half-written record , drop it rather than surfacing
                    // a blank tile.
                    if !cur.app_name.is_empty() || !cur.exe.is_empty() {
                        out.push(std::mem::take(&mut cur));
                    }
                    in_entry = false;
                }
                depth -= 1;
                if depth < 0 { break; }
            }
            0x01 => {
                let key = read_cstr(data, &mut i).to_ascii_lowercase();
                let val = read_cstr(data, &mut i);
                if !in_entry { continue; }
                match key.as_str() {
                    "appname"       => cur.app_name       = val,
                    "exe"           => cur.exe            = unquote(&val),
                    "startdir"      => cur.start_dir      = unquote(&val),
                    "launchoptions" => cur.launch_options = val,
                    "icon"          => cur.icon           = unquote(&val),
                    _ => {}
                }
            }
            0x02 => {
                let key = read_cstr(data, &mut i).to_ascii_lowercase();
                if i + 4 > data.len() { break; }
                let v = u32::from_le_bytes([data[i], data[i + 1], data[i + 2], data[i + 3]]);
                i += 4;
                if !in_entry { continue; }
                match key.as_str() {
                    "appid"    => cur.appid     = v,
                    "ishidden" => cur.is_hidden = v != 0,
                    _ => {}
                }
            }
            // Unknown type byte , the file is not the shape we expect.
            // Stop rather than walk off into garbage.
            _ => break,
        }
    }
    out
}

pub fn read_shortcuts() -> Vec<Shortcut> {
    let mut all = Vec::new();
    for f in shortcut_files() {
        if let Ok(bytes) = std::fs::read(&f) {
            all.extend(parse_shortcuts(&bytes));
        }
    }
    all
}

// ── Wine prefix helpers ───────────────────────────────────────────────

/// Given any Linux path that points inside a Wine prefix, return the
/// prefix root (the directory that *contains* `drive_c`).
///
/// This one line is how a launcher .exe path becomes a WINEPREFIX , the
/// same derivation Lutris uses (services/battlenet.py).
pub fn prefix_root_of(path: &str) -> Option<PathBuf> {
    let idx = path.find("drive_c")?;
    let root = path[..idx].trim_end_matches('/');
    if root.is_empty() { None } else { Some(PathBuf::from(root)) }
}

/// `C:\Program Files (x86)\Hearthstone` -> `<prefix>/drive_c/Program Files (x86)/Hearthstone`.
/// Only drive C is mapped; anything else returns None, because a prefix
/// that maps other drives is doing something we have not verified.
pub fn win_path_to_linux(prefix_root: &Path, win: &str) -> Option<PathBuf> {
    let w = win.replace('\\', "/");
    let rest = w.strip_prefix("C:/").or_else(|| w.strip_prefix("c:/"))?;
    Some(prefix_root.join("drive_c").join(rest))
}

// ── Battle.net product.db (protobuf) ──────────────────────────────────

#[derive(Debug, Clone, Default)]
pub struct BnetProduct {
    pub uid: String,
    pub product_code: String,
    /// Windows-side install path as Battle.net recorded it.
    pub install_path: String,
    pub installed: bool,
    pub playable: bool,
}

/// Read a protobuf varint. Returns None on truncation.
fn read_varint(data: &[u8], i: &mut usize) -> Option<u64> {
    let mut val: u64 = 0;
    let mut shift = 0u32;
    loop {
        if *i >= data.len() || shift > 63 { return None; }
        let b = data[*i];
        *i += 1;
        val |= ((b & 0x7f) as u64) << shift;
        if b & 0x80 == 0 { return Some(val); }
        shift += 7;
    }
}

/// Skip one field whose wire type is not the one we wanted.
fn skip_field(data: &[u8], i: &mut usize, wire: u8) -> Option<()> {
    match wire {
        0 => { read_varint(data, i)?; }
        1 => { *i = i.checked_add(8)?; }
        2 => { let n = read_varint(data, i)? as usize; *i = i.checked_add(n)?; }
        5 => { *i = i.checked_add(4)?; }
        _ => return None,
    }
    if *i > data.len() { None } else { Some(()) }
}

/// Iterate `(field_number, wire_type, payload_range)` over one message.
fn each_field(data: &[u8], mut f: impl FnMut(u64, u8, &[u8], u64)) {
    let mut i = 0usize;
    while i < data.len() {
        let Some(key) = read_varint(data, &mut i) else { return };
        let field = key >> 3;
        let wire = (key & 0x7) as u8;
        if wire == 2 {
            let Some(n) = read_varint(data, &mut i) else { return };
            let n = n as usize;
            if i + n > data.len() { return; }
            f(field, wire, &data[i..i + n], 0);
            i += n;
        } else if wire == 0 {
            let Some(v) = read_varint(data, &mut i) else { return };
            f(field, wire, &[], v);
        } else if skip_field(data, &mut i, wire).is_none() {
            return;
        }
    }
}

/// Decode `<prefix>/drive_c/ProgramData/Battle.net/Agent/product.db`.
///
/// Schema (wire fields only , mirrors Lutris' decoder):
///   ProductDb            { 1: repeated ProductInstall }
///   ProductInstall       { 1: uid, 2: product_code, 3: UserSettings,
///                          4: CachedProductState }
///   UserSettings         { 1: install_path }
///   CachedProductState   { 1: BaseProductState }
///   BaseProductState     { 1: installed(bool), 2: playable(bool) }
pub fn parse_product_db(data: &[u8]) -> Vec<BnetProduct> {
    let mut out = Vec::new();
    each_field(data, |field, wire, payload, _| {
        if field != 1 || wire != 2 { return; }
        let mut p = BnetProduct::default();
        each_field(payload, |f2, w2, pay2, _| {
            match (f2, w2) {
                (1, 2) => p.uid = String::from_utf8_lossy(pay2).into_owned(),
                (2, 2) => p.product_code = String::from_utf8_lossy(pay2).into_owned(),
                (3, 2) => each_field(pay2, |f3, w3, pay3, _| {
                    if f3 == 1 && w3 == 2 {
                        p.install_path = String::from_utf8_lossy(pay3).into_owned();
                    }
                }),
                (4, 2) => each_field(pay2, |f3, w3, pay3, _| {
                    if f3 == 1 && w3 == 2 {
                        each_field(pay3, |f4, w4, _, v4| {
                            match (f4, w4) {
                                (1, 0) => p.installed = v4 != 0,
                                (2, 0) => p.playable  = v4 != 0,
                                _ => {}
                            }
                        });
                    }
                }),
                _ => {}
            }
        });
        // `bna` and `agent` are the launcher and its updater, not games.
        let code = p.product_code.to_ascii_lowercase();
        if code == "bna" || code == "agent" { return; }
        if p.uid.is_empty() && p.product_code.is_empty() { return; }
        out.push(p);
    });
    out
}

/// Games installed under a Battle.net launcher, given the launcher's own
/// .exe path. Empty when the prefix has no product.db (launcher present
/// but nothing installed through it yet) , that is a normal state, not an
/// error.
pub fn battlenet_games(launcher_exe: &str) -> Vec<(String, PathBuf)> {
    let Some(prefix) = prefix_root_of(launcher_exe) else { return Vec::new() };
    let db = prefix
        .join("drive_c/ProgramData/Battle.net/Agent/product.db");
    let Ok(bytes) = std::fs::read(&db) else { return Vec::new() };
    let mut out = Vec::new();
    for p in parse_product_db(&bytes) {
        if !p.installed || p.install_path.is_empty() { continue; }
        let Some(dir) = win_path_to_linux(&prefix, &p.install_path) else { continue };
        if !dir.is_dir() { continue; }
        let name = dir.file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_else(|| p.product_code.clone());
        out.push((name, dir));
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Build a minimal shortcuts.vdf in memory and read it back.
    fn vdf_fixture() -> Vec<u8> {
        let mut v: Vec<u8> = Vec::new();
        v.push(0x00); v.extend(b"shortcuts\0");
        v.push(0x00); v.extend(b"0\0");
        v.push(0x02); v.extend(b"appid\0"); v.extend(&0xc32c70ebu32.to_le_bytes());
        v.push(0x01); v.extend(b"AppName\0"); v.extend(b"Battle.net\0");
        v.push(0x01); v.extend(b"Exe\0");
        v.extend(b"\"/home/u/.steam/steam/steamapps/compatdata/3274469611/pfx/drive_c/Program Files (x86)/Battle.net/Battle.net Launcher.exe\"\0");
        v.push(0x01); v.extend(b"LaunchOptions\0"); v.extend(b"WINE_SIMULATE_WRITECOPY=1 %command%\0");
        v.push(0x02); v.extend(b"IsHidden\0"); v.extend(&0u32.to_le_bytes());
        v.push(0x08);
        v.push(0x08);
        v.push(0x08);
        v
    }

    #[test]
    fn parses_a_shortcut_entry() {
        let s = parse_shortcuts(&vdf_fixture());
        assert_eq!(s.len(), 1);
        assert_eq!(s[0].app_name, "Battle.net");
        assert_eq!(s[0].appid, 3274469611);
        assert!(!s[0].is_hidden);
        assert!(s[0].exe.ends_with("Battle.net Launcher.exe"));
        assert!(!s[0].exe.starts_with('"'), "Exe must be unquoted");
    }

    #[test]
    fn truncated_file_yields_no_panic() {
        let full = vdf_fixture();
        for cut in 1..full.len() {
            let _ = parse_shortcuts(&full[..cut]);
        }
    }

    #[test]
    fn derives_prefix_root_and_maps_windows_paths() {
        let exe = "/home/u/.steam/steam/steamapps/compatdata/3274469611/pfx/drive_c/Program Files (x86)/Battle.net/Battle.net Launcher.exe";
        let root = prefix_root_of(exe).unwrap();
        assert!(root.ends_with("3274469611/pfx"));
        let mapped = win_path_to_linux(&root, "C:\\Program Files (x86)\\Hearthstone").unwrap();
        assert!(mapped.ends_with("pfx/drive_c/Program Files (x86)/Hearthstone"));
        assert!(prefix_root_of("/not/a/prefix/game.exe").is_none());
    }

    /// Hand-encode ProductDb{1: ProductInstall{1:uid, 2:code,
    /// 3:{1:install_path}, 4:{1:{1:true,2:true}}}}.
    #[test]
    fn parses_product_db() {
        fn ld(field: u64, payload: &[u8]) -> Vec<u8> {
            let mut v = vec![((field << 3) | 2) as u8];
            v.push(payload.len() as u8);
            v.extend(payload);
            v
        }
        let base = [0x08u8, 0x01, 0x10, 0x01];          // 1:true, 2:true
        let cached = ld(1, &base);
        let settings = ld(1, b"C:\\Program Files (x86)\\Hearthstone");
        let mut install = Vec::new();
        install.extend(ld(1, b"hs"));
        install.extend(ld(2, b"hsb"));
        install.extend(ld(3, &settings));
        install.extend(ld(4, &cached));
        let db = ld(1, &install);

        let got = parse_product_db(&db);
        assert_eq!(got.len(), 1);
        assert_eq!(got[0].uid, "hs");
        assert_eq!(got[0].install_path, "C:\\Program Files (x86)\\Hearthstone");
        assert!(got[0].installed && got[0].playable);
    }

    #[test]
    fn skips_launcher_pseudo_products() {
        fn ld(field: u64, payload: &[u8]) -> Vec<u8> {
            let mut v = vec![((field << 3) | 2) as u8];
            v.push(payload.len() as u8);
            v.extend(payload);
            v
        }
        let mut install = Vec::new();
        install.extend(ld(1, b"bna"));
        install.extend(ld(2, b"bna"));
        let db = ld(1, &install);
        assert!(parse_product_db(&db).is_empty());
    }
}
