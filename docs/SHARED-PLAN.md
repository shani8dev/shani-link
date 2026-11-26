# shani-link: its part of the shared architecture

The plan for all repos is `../shani-shell/docs/SHARED-ARCHITECTURE.md` (https://github.com/shani8dev/shani-shell/blob/master/docs/SHARED-ARCHITECTURE.md). Read it first; its rules apply
here. This file is only this repo's part; change the plan there, not here.

**Role:** the phone app for GNOME/Plasma/COSMIC; after phase 2 a **GTK window only**.

**Do as much of this as your task allows** (never breaking a consumer), and
add a line to the plan's Status section when you finish something:

- Its engines (`shani_link/*` outside `app/`) are a second copy of `shani-shell/bridge/phone`, which is the seed. Fix bugs there first, then port here, and say so in both commits. No new engine features here.
- Never change the landline media engine's internals (`ua`, `media`, `video`).
- Phase 2 merges the two copies into `shani-services-phone`; this repo keeps `app/` and becomes a client of the one device service.
