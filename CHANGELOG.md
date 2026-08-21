# GreenBoost Gaming Suite , Changelog

---

## 2026-08-20 , closing the Suite no longer abandons the game

Three related gaps, all of them the same missing idea: the Suite launched
games but never owned them.

### A crashed game used to leave the machine mis-tuned, silently

Starting a game pins CPU governors to performance, applies a GPU power limit,
locks clocks, lowers swappiness, stops power-profile daemons and sets
greenboost.ko's `gaming_mode` to 1. The wrapper restored all of it on exit ,
unless it died hard, because every saved value lived only in memory. A SIGKILL
or an OOM kill took the only record of the original state with it.

What that cost, without announcing itself: CPUs stuck at performance (fans up,
idle power up), a power limit matching nothing, and , the expensive one ,
`gaming_mode` stuck at 1, which parks every inference buffer in system RAM at
the eviction queue's tail and makes the shim keep doubling its KV reserve. It
read as "the box got slower".

The baseline now survives the process. Before the first write, every value is
captured to `~/.local/state/greenboost-gaming/` as JSON **and** as a plain `sh`
restore script , deliberately dependency-free, because restoring has to work
when the thing that wrote it is gone. A detached watchdog
(`while kill -0 <pid>; do sleep 5; done; sh restore.sh`) puts the machine back
within about five seconds of a hard kill, then deletes itself. A clean exit
retires the watchdog before it can fire on state that is already restored.

Anything left over from an earlier crash is swept at the next launch and at
Suite startup, so yesterday's crash does not stay applied today. Items that
need root to restore are reported as needing root rather than counted as done ,
half a restore claimed as a whole one is worse than no claim.

### Closing the window quits everything, or nothing

There was no tray icon and no close handler , closing the window exited the
process, and the game kept running with nothing left supervising it (and
with greenboost.ko's `gaming_mode` still pinned at 1, which parks inference
memory at the eviction queue's tail).

The window now hides to the system tray instead, and the tray menu offers
**Show / Stop game / Quit**. Two settings under "Display & session" control
it: *Close to system tray* (on by default) and *Stop the game when you quit*
(on by default, the way Steam behaves). If the desktop has no tray , no
StatusNotifier host, or `libayatana-appindicator3` missing , the Suite says
so and falls back to quitting, rather than hiding a window you cannot get
back.

### There was no way to stop a game, at all

No SIGTERM, no SIGKILL, no signal delivery anywhere in the codebase. Stopping
a game now resolves whoever actually owns its process tree:

1. **GreenBoost Proton**, when it launched the game. The wrapper now declares
   itself a subreaper (`PR_SET_CHILD_SUBREAPER`), so a launcher that forks
   the real game and exits hands its children to the wrapper rather than to
   init , which is what makes "is it still running?" answerable at all. It
   records the session under `~/.local/state/greenboost-gaming/`, and a stop
   still runs its full restore path: perf lock, compositor, `gaming_mode`,
   session summary.
2. **Steam's own `reaper`**, when Steam launched it. Steam already wraps
   every launch as `reaper SteamLaunch AppId=<n> -- <game>`; signalling it is
   more correct than reimplementing what it already does.
3. **The wine process tree**, as a last resort.

The walk uses `/proc/<pid>/task/<tid>/children` , the kernel's own child list
, and never a PPID scan, because the children list survives an intermediate
process exiting. Termination is SIGTERM, five seconds, then SIGKILL for
whatever ignored it, so saves get flushed. `wineserver`, `services.exe`,
`explorer.exe` and the rest of Wine's shared infrastructure are never
signalled: another game in a different prefix must not be collateral.

Every stop emits a `gaming_session` event with `action="terminated"`, naming
the method used and how many processes survived both signals. A crashed
session leaves a record behind, which the Suite now prunes at startup , and
the state it leaves behind is visible as a governed verdict:

```bash
gb semantics segments gaming_mode_stuck --json
gb semantics resolve gaming_session_orphans --json
```

### Non-Steam games were invisible

Anything added through Steam's "Add a Non-Steam Game" , a Battle.net, Epic or
itch launcher installed into its own Proton prefix , never appeared in the
Games view. It has no appmanifest and it lives under
`steamapps/compatdata/<appid>/pfx`, not `steamapps/common`, so the library
walk could not see it.

The scanner now reads `userdata/*/config/shortcuts.vdf` directly. For a
Battle.net prefix it also decodes Blizzard's own `product.db` inside the
prefix, so the games installed *under* the launcher show up as their own
entries instead of one tile saying "Battle.net". Epic, EA and Start-Menu
`.lnk` discovery use the same shape and are not built yet.

---

## 2026-08-20 , the Vulkan layer was never actually running

Two defects, neither of which produced an error, that between them made the
GreenBoost Vulkan layer completely inert since the first public release.
Found while verifying an unrelated change; fixed together, because fixing
either one alone leaves the system broken.

### The layer was being skipped by the Vulkan loader

`VkLayer_greenboost.json` declared `enable_environment` but no
`disable_environment`. The loader requires both on an implicit layer and
**skips the layer entirely** when one is missing, warning only under
`VK_LOADER_DEBUG`.

Everything else looked healthy: the `.so` was installed, the Status view
reported "Vulkan Layer: Installed" (it checks the file exists), and games
launched and ran fine , with no GreenBoost involvement whatsoever. No VRAM
inflation, no T2/T3 DDR overflow, no NIS, no Reflex, no frame-pacing
telemetry.

Measured on an RTX 5070 with an 11 GB + 42 GB pool: the device-local heap
reported **11.94 GiB** before the fix and **53.00 GiB** after.

Fixed in the shipped manifest and in `install.sh`'s `write_layer_manifest()`,
which also generates the `$HOME` mirror the Steam sandbox reads.

### Every hooked Vulkan function failed to resolve

The dispatch macro in `gbvk_GetInstanceProcAddr` / `gbvk_GetDeviceProcAddr`
compared the requested name against the stringified macro argument
(`"CreateInstance"`) rather than the real Vulkan symbol
(`"vkCreateInstance"`), so no macro-declared hook ever matched. The two
hand-written comparisons beside it used correct full names, which is why the
omission read as correct.

This was masked by the manifest bug , with the layer skipped, the broken
dispatch never ran. Fixing the manifest alone surfaced it as
`loader_create_instance_chain: Failed to find 'vkCreateInstance'`, at which
point **every Vulkan application failed to start**.

`tests/run_layer_contract_test.sh` now guards both (static checks, no GPU
required).

### Headroom in the budget reported to games

Separately, `inflate_budget()` reported the **gross** T1+T2 capacity as
`heapBudget[]`, so a game that allocated up to what it was told drove the T2
pool to 100% with no margin , the situation Rule #1's 10% VRAM headroom
exists to prevent, one tier down, and most likely to bite when a served model
is holding VRAM on the same card.

It now holds back a capped fraction: 12.5%, never more than 1 GiB. A capped
fraction rather than a flat percentage, so a 76 GB virtual pool reserves
1 GiB instead of 9.5 GB. Both terms are env-tunable
(`GREENBOOST_VK_BUDGET_RESERVE_DIV`, `GREENBOOST_VK_BUDGET_RESERVE_MAX_MB`);
setting either to 0 restores the previous behaviour.

Verified live: 53.00 GiB reported with the reserve disabled, 52.00 GiB with
it at default. `heapUsage[]` is still the real driver value and was
deliberately left alone.

Technique adapted from KytyPS5's `vma.cpp` (see `AUDIT_kytyps5.md`);
independently implemented, no code copied , that project is GPL-2.0 and this
one is MIT.

---

## 2026-08-20 , one-click upgrades from the Updates card

The Updates card knew an update existed and then told you to go install it
yourself: *"Update it from its own repo, then reload the kernel module (sudo
greenboost load)"*. Every component row that reports an update now carries an
**Upgrade now** button that does it.

- **GreenBoost core** , `upgrade_core_streaming`: fetches the core sources
  (clone on first use, `git pull --rebase` after that), runs that repo's own
  `install_module.sh`, then **reloads the kernel module**. That last step is
  new and it is the one that was easiest to forget by hand: installing does
  not swap the running module, so without it `greenboost --version` and this
  Status view both keep reporting the old version and every fix in the new
  build is silently absent from the running system.
- **The Suite itself** , `upgrade_suite_streaming`: pulls this repo and
  re-runs `install.sh`. Deliberately the same script a human would run, since
  that is what also redeploys the Steam compatibility-tool copy, the `$HOME`
  mirror the Steam sandbox reads, and the polkit/sudoers/udev rules. A
  shortcut that only rebuilt the binary would reproduce the
  stale-deployed-copy bug class this repo has already been bitten by. The app
  keeps running the old code until you close and reopen it, and it says so
  rather than implying the new version is live.

Both reuse the existing `InstallStreamModal`, so the upgrade runs in the same
line-by-line console the kernel-module and Proton installs already use, behind
the same confirmation prompt and the same `pkexec` authorization. Available
from Status (the banner) and About (the full panel).

Failures are reported as what they cost, not as an exit code. A failed core
install stops before the reload rather than reloading the old module and
returning success , which would have reported an upgrade that did not happen.
A failed `git pull` (usually local edits in the checkout) stops before
building anything and says the current version still works. A failed reload
says the new module is on disk and a reboot will pick it up.

The advice sentences were rewritten to describe what the buttons do and in
which order to press them, instead of listing shell commands.

---

## 2026-08-20 , security fix, GTK4 removal, uninstall parity

### Security , local privilege escalation in the fan-control polkit rule

`scripts/60-greenboost-fan.rules` authorised passwordless root for
`/usr/bin/python3` whenever the command line merely *contained* the
substring `nvml_fan.py`. Any active local user could run

    pkexec /usr/bin/python3 /tmp/anything.py nvml_fan.py

and execute arbitrary code as root: the substring test passed on an
argument the attacker controlled, while the script actually executed was
their own. The rule now requires the whole command line to match one exact
installed script path followed by the helper's own closed argument grammar
(`auto`, or `set 0`..`set 100`). Nothing else is authorised.

`tests/polkit_rule_test.js` loads the real rule file and evaluates it
against a polkit stub, so the test cannot drift from the shipped rule the
way a re-implementation of its logic would. 22 cases, including the exact
exploit above.

The companion `scripts/60-greenboost-fan.sudoers` was reviewed and is
**not** affected: sudo matches the command path exactly, and both helpers
(`nvml_fan.py`, `nvml_control.py`) validate their arguments against a
closed set, so its trailing `*` cannot reach another script.

**If you installed a previous version, the vulnerable rule is still on your
machine.** Re-run `sudo ./install.sh` to replace it.

### Uninstall now removes everything the installer created

`--uninstall` previously left five system artifacts behind, including the
polkit rule above , so uninstalling the software left a root-granting rule
on the machine. Now removed as well:

- the fan-daemon systemd user unit (stopped and disabled first, so a
  running daemon is never orphaned from its unit file)
- `/etc/polkit-1/rules.d/60-greenboost-fan.rules`
- `/etc/sudoers.d/60-greenboost-fan`
- `/usr/share/greenboost-gaming/` (the 299 shipped per-game profiles)
- the `greenboost` group, when no members remain , it is shared with the
  core GreenBoost install, so a group that still has members is left alone
  with a note rather than deleted

The Steam compatibility-tool copy is now removed too, via the same
`greenboost_proton/install.sh` hand-off the install path already used.
That script's `--uninstall` also gained the two `$HOME` artifacts it was
missing: the staged NIS shaders and the dxvk-gplasync download cache
(~200 MB combined, and a stale cache that silently overrode
`GB_GPLASYNC_VERSION` on the next install).

Every artifact destination is now declared in one block near the top of
`install.sh` rather than next to the step that writes it, so the uninstall
path can name the same paths the install path does.

Your own data is still deliberately left in place, and the uninstall now
says so explicitly instead of leaving you to guess:
`~/.config/greenboost-gaming/`, `~/.local/share/greenboost/`, and the
downloaded DLLs under `libraries/`.

### The GTK4 GUI is gone

`ui/main.py` (876 lines) and every branch that reached it were removed.
Tauri is the only GUI.

The reason is not just dead weight. When the Tauri toolchain or the WebKit
development libraries were missing, `install.sh` **silently downgraded** to
the GTK4 app , and the generated launcher used `gtk4|*)` as its default
case, so any unexpected value ran it too. A user who did not read the
install scrollback ended up running a different, plainer program than the
one the documentation describes, with no error anywhere. Missing build
dependencies now stop the install and say what to install and what still
works without the GUI.

`python3-gi` / `python3-gobject` stays in the dependency lists: the GNOME
VRR and display helpers (`gb_gaming/_vrr_gnome.py`, `_display_config.py`)
need `Gio` and `GLib`. Only the `Gtk-4.0` and `Adw-1` typelibs, which
nothing but the removed GUI used, were dropped.

---

## 2026-08-12 , first public release (Alpha/Beta)

Nothing about the software changed here , this entry records the
publication and the repository hygiene that went with it.

- **Status is Alpha / early Beta**, and the README now says so plainly.
  Everything is built and tested on a single machine (RTX 5070,
  GNOME/Wayland). Other GPUs, other distros, and X11 sessions are
  supported by design but genuinely under-tested.
- **No NVIDIA DLLs in the repository.** The eleven DLSS / Streamline
  DLLs that had been committed under `libraries/` were untracked and
  gitignored, along with `libraries/manifest.json` and
  `libraries/versions/`. That directory was always documented as a
  runtime cache; now it behaves like one. The in-app **Update DLSS**
  fetches from NVIDIA's official GitHub repositories on demand. Repo
  size drops from ~118 MiB to ~20 MiB.
- **`CONTRIBUTING.md`, build instructions, the fix-at-the-source
  rule, the Wayland-first display rules, and the verification recipes.