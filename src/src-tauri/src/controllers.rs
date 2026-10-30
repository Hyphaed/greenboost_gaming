// Copyright 2026 Ferran Duarri , GPL v2
// GreenBoost is an independent open-source project and is not affiliated with,
// endorsed by, or sponsored by NVIDIA Corporation.
//
// Controller visibility , detection/diagnostics only, same shape as
// directstorage.rs. Real incident this exists to make visible: on this
// machine, an Xbox Wireless Controller over Bluetooth is bound to the
// xpadneo driver, kernel-side connect is clean (Linux Gamepad Specification
// compliance, rumble streaming enabled), and Steam's own controller.txt
// opens it every session , but xpadneo's own udev rule
// (70-xpadneo-disable-hidraw.rules) tries to lock Steam out of the hidraw
// node to work around known xpadneo/Steam Input conflicts (upstream issues
// 291, 457), and Steam's own 60-steam-input.rules re-tags it afterwards.
// Neither side loses cleanly, and none of it lives in GreenBoost , the
// Proton wrapper (`greenboost_proton/gb_proton_main.py`) contains no
// input/joystick/SDL/hidraw code at all and only mutates the environment
// before handing off to Proton, so the controller path is entirely Steam's
// and the kernel's. This module surfaces what's actually true instead of
// leaving the user with nothing to look at.

use std::path::{Path, PathBuf};
use std::process::Command;
use serde::{Serialize, Deserialize};

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct ControllerInfo {
    pub name:            String,
    /// Kernel driver bound to the HID device , "xpadneo", "xpad",
    /// "hid-generic", or "unknown" when the driver symlink can't be read.
    pub driver:          String,
    /// "Bluetooth", "USB", or "unknown" , inferred from `uniq` (a MAC for
    /// a Bluetooth HID device) and `phys`, never guessed beyond that.
    pub connection:      String,
    /// None when no matching UPower device was found (wired pads usually
    /// don't report one), Some(0..=100) otherwise.
    pub battery_percent: Option<u8>,
    pub js_node:         String,
    pub event_node:      Option<String>,
    /// None when no hidraw node exists for this device at all. Some(false)
    /// is the interesting case: this is what makes the xpadneo-vs-Steam
    /// hidraw fight (see module comment) visible instead of silent.
    pub hidraw_readable: Option<bool>,
}

/// Enumerates every joystick (`/dev/input/js*`) currently attached and
/// resolves each one's driver, connection type, hidraw readability, and
/// (best-effort) battery level. Never touches the device beyond a
/// zero-side-effect open-then-close on its hidraw node to test readability.
pub fn get_controllers() -> Vec<ControllerInfo> {
    let mut out = Vec::new();
    let js_glob = match std::fs::read_dir("/sys/class/input") {
        Ok(rd) => rd,
        Err(_) => return out,
    };

    for entry in js_glob.flatten() {
        let fname = entry.file_name().to_string_lossy().to_string();
        if !fname.starts_with("js") { continue; }

        let js_link = entry.path();
        // js_link resolves to .../input<N>/js<M> , the input device
        // directory is its parent.
        let Some(js_real) = std::fs::canonicalize(&js_link).ok() else { continue };
        let Some(input_dir) = js_real.parent().map(PathBuf::from) else { continue };

        let name = read_trimmed(&input_dir.join("name")).unwrap_or_else(|| "Unknown controller".into());
        let uniq = read_trimmed(&input_dir.join("uniq")).unwrap_or_default();
        let phys = read_trimmed(&input_dir.join("phys")).unwrap_or_default();

        let driver = read_link_basename(&input_dir.join("device").join("driver"))
            .unwrap_or_else(|| "unknown".into());

        let connection = if is_bt_mac(&uniq) {
            "Bluetooth".to_string()
        } else if phys.contains("usb") {
            "USB".to_string()
        } else {
            "unknown".to_string()
        };

        let event_node = std::fs::read_dir(&input_dir).ok()
            .and_then(|rd| rd.flatten().find(|e| {
                e.file_name().to_string_lossy().starts_with("event")
            }))
            .map(|e| format!("/dev/input/{}", e.file_name().to_string_lossy()));

        let hidraw_readable = find_hidraw_node(&input_dir.join("device"))
            .map(|node| std::fs::OpenOptions::new().read(true).open(&node).is_ok());

        let battery_percent = if is_bt_mac(&uniq) { upower_battery_percent(&uniq) } else { None };

        out.push(ControllerInfo {
            name,
            driver,
            connection,
            battery_percent,
            js_node: format!("/dev/input/{fname}"),
            event_node,
            hidraw_readable,
        });
    }

    out
}

fn read_trimmed(path: &Path) -> Option<String> {
    std::fs::read_to_string(path).ok().map(|s| s.trim().to_string())
}

fn read_link_basename(path: &Path) -> Option<String> {
    std::fs::canonicalize(path).ok()
        .and_then(|p| p.file_name().map(|n| n.to_string_lossy().to_string()))
}

/// `uniq` looks like a MAC address ("3c:fa:06:23:6e:fb") only for Bluetooth
/// HID devices , USB pads leave it empty.
fn is_bt_mac(uniq: &str) -> bool {
    uniq.len() == 17 && uniq.matches(':').count() == 5
}

/// hidraw nodes live under `<hid-device>/hidraw/hidrawN`, a sibling of the
/// input subsystem's own device directory , not the input device itself.
fn find_hidraw_node(hid_device_dir: &Path) -> Option<PathBuf> {
    let hidraw_dir = hid_device_dir.join("hidraw");
    std::fs::read_dir(&hidraw_dir).ok()?
        .flatten()
        .find(|e| e.file_name().to_string_lossy().starts_with("hidraw"))
        .map(|e| PathBuf::from("/dev").join(e.file_name()))
}

/// Best-effort battery percentage via UPower, which is how this class of
/// Bluetooth HID gamepad actually reports battery on this system (through
/// BlueZ's own battery1 interface, not the kernel's power_supply class , a
/// `find`/`grep` across /sys/class/power_supply confirmed no entry exists
/// for this device at all, so upower is not a fallback, it's the only
/// source here). Returns None on any missing tool, unmatched device, or
/// unparseable output , never a guessed value.
fn upower_battery_percent(uniq_mac: &str) -> Option<u8> {
    let native_suffix = uniq_mac.to_uppercase().replace(':', "_");
    let list = Command::new("upower").arg("-e").output().ok()?;
    if !list.status.success() { return None; }
    let device_path = String::from_utf8_lossy(&list.stdout)
        .lines()
        .find(|l| l.contains(&native_suffix))
        .map(|l| l.trim().to_string())?;

    let info = Command::new("upower").args(["-i", &device_path]).output().ok()?;
    if !info.status.success() { return None; }
    String::from_utf8_lossy(&info.stdout)
        .lines()
        .find_map(|l| {
            let l = l.trim();
            l.strip_prefix("percentage:")
                .map(|v| v.trim().trim_end_matches('%'))
                .and_then(|v| v.parse::<u8>().ok())
        })
}
