# Agent instructions - shani-link

Read README.md first. This repo is the device plumbing that used to live inside
Shani Chronoa: it must stay desktop-neutral (no shell, no assistant code) and
everything it does is reachable over its D-Bus API.

## Rules
- **Verify by running.** `python3 -m pytest` runs the engines against fakes and
  the service on a private bus. A change to the API needs a test in
  tests/test_service.py that calls it through gdbus.
- **Never the real devices in a test.** No real session bus, no real Bluetooth,
  no real `~/.config` (conftest.py points XDG_CONFIG_HOME at a temp dir).
- **Names the outside world knows do not change.** The router knows the computer
  as `Chronoa<host>` (provision.py) and the Android app's id is
  `dev.shani.chronoa.cast`: renaming either breaks existing setups.
- **The app has no assistant consent.** A page acts when its button is pressed;
  only what runs with no window open gets a switch (settings.KEYS, the
  Background page). Never bring Chronoa's "Let Chronoa ..." banners here.
- **GTK tests never use the person's display**: conftest.py runs them on a
  private Broadway display, and builds that start device reads stub the engine
  at the name the page calls.
- **Moving a module here from Chronoa:** bring its engine tests, cut its imports
  of Chronoa (helpers are re-implemented small, not imported), expose it on the
  bus, then switch Chronoa's callers to the bus in the same change.
