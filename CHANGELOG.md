# GreenBoost Gaming Suite , Changelog

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
