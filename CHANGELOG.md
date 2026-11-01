# GreenBoost Gaming Suite , Changelog

## 1.1.0 , 2026-11-01

### The banner stayed on a gamepad icon for STAR WARS Galactic Racer

Steam has the game's hero image cached, and the sidebar thumbnail showed it,
but the big banner at the top of the game page didn't. The banner remembered
"my image failed to load" and never forgot it: if any earlier game's image
had failed once, every game you clicked afterwards got the placeholder. It
now retries whenever the image changes. Nothing about the art lookup itself
was wrong.

### "Update to v2.14.16.0" kept showing after you updated

The badge was telling the truth: the files in the game folder were still the
ones Steam installed (Streamline 2.11.1.0, Frame Generation 310.6.0.0), and
the update had never written anything. "Update DLSS" fetched the libraries
into the cache first, then ignored that cache and downloaded each DLL again,
which for Streamline means the whole ~270 MB SDK once per DLL. With a slow or
rate-limited connection every one of those downloads failed. It now installs
from the cache and only downloads what the cache doesn't have.

Folders named like `_DLSS5_Backup` are skipped too. Before, the scanner listed
the backed-up originals as if they were the game's live DLLs, and Update could
have overwritten them. Per-DLL "Apply" and Update both skip them now.

Still true: a game can ship a newer DLL than the cache, and Update never
downgrades. To see the change, run Upgrade DLSS on the game, then Re-scan.

### Game mode

Each game can now switch on a few desktop changes for the length of a session:
GNOME extensions, user units and screen-blanking. Everything it changes is
restored exactly as it was, including after a crash, because the screen-awake
inhibit is held on a D-Bus connection that the desktop releases when the
process dies. The new `gb_gaming/session_bus.py` does this with only the
standard library, since the Steam runtime container has no `gsettings`,
`systemctl` or `qdbus`.

### Neural Rendering was never running in Final Fantasy VII Rebirth

DLSS Super Resolution failed to create every time. A one-time NvAPI check lost
a race against dxvk-nvapi's Vulkan start-up under Proton, and the miss was
cached for the rest of the process. Neural Rendering only runs after Super
Resolution works, so it never got a chance. Fixed in the fork. The shipped
ini template also stopped writing `GreenBoostDLSS5.log` after a redeploy
(`LogToFile` had reverted to its default of off); that's restored.

Not fixed: the `0xbad00002` Frame Generation errors in the same log come from
Streamline's closed-source platform probe. They're recorded in
`libraries/dlssnr/FORK_NOTES.md` for the next playtest.

### Interface and other

New Status hero, live gauges and toggle components; the Games view is split
into smaller pieces. Settings precedence between global and per-game values
is covered by tests now. Added `docs/dlss5-galactic-racer.md`, a research
note on whether DLSS 5 could work in that game: it's labelled plausible and
untested, not a "works" claim.

## 1.0 , 2026-09-02

The interface got a clean-up: settings belonging to unfinished parts of the
app are now hidden rather than shown with nothing behind them, so what's on
screen is what actually works. DLSS 5 Neural Rendering can be switched on per
game, with its own panel of settings. It's confirmed working in 007 First
Light; the same mechanism is meant for every game, but nothing else has been
tested yet.

## 0.2.2 , 2026-08-27

Some games sat forever on Steam's ownership check instead of starting. Fixed.
You can also point the Suite at your own folder of DLL files and have them
loaded into a game , the way to supply files GreenBoost isn't allowed to ship
for you.

## 0.2.1 , 2026-08-21

GreenBoost printed an error into your boot log every start-up. Nothing was
broken by it and everything ended up configured correctly seconds later, but
it taught you to ignore GreenBoost's boot messages. The installer now sets
itself up for whichever boot system your distribution actually uses, and the
message is gone.

## 0.2.0 , 2026-08-20

Closing the window no longer abandons your game: the Suite hides to the tray
and can stop the game for you, and a game that crashes no longer leaves your
machine tuned for gaming. Games added from Battle.net, Epic and other
launchers now show up in your library. Updates install with one click. A
security hole in the fan-control permissions was closed , reinstall to get
the fix.

## 0.1.1 , 2026-08-16

Documentation and README corrections following the first public release. No
change in behaviour.

## 0.1.0 , 2026-08-12

First public release. Per-game profiles for your Steam library, a Proton
version that prepares the machine before a game starts, DLSS file management
that fetches current files from NVIDIA directly, fan and power control, and a
live view of what the GPU is doing. Built and tested on one machine , an RTX
5070 on GNOME/Wayland , so treat anything else as untested.
