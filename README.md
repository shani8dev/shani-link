# shani-link

The computer's phone line, phone and watch, as one user service on the session
bus (`dev.shani.Link`). It is plumbing, not an assistant and not a settings app:
the desktop shell shows it (call card, dialer, phone tile), Shani Chronoa drives
it (its skills ask it to dial, read a text, check the watch), and any desktop -
niri with shani-shell, GNOME, Plasma - gets the same line.

## What is here
| Object | Interface | What |
|---|---|---|
| `/dev/shani/Link/Landline` | `dev.shani.Link.Landline` | The JioFiber landline through the router, like JioJoin: set up once with the texted code, then calls, keypad, mute, hold, swap, merge, history; `Event(kind, info_json)` for everything that happens on the line |

Moving here from Shani Chronoa, in phases - the engines and their screens;
Chronoa keeps thin skills that call this package:
- the phone link (the KDE Connect protocol, and GSConnect/KDE Connect when installed)
- the phone over plain Bluetooth: calls (PipeWire telephony), texts (MAP), its screen
- MoYoung ("Da Fit") watches: health data, staying connected, the watch's buttons
- this computer's Bluetooth roles: a remote (BLE), music control (AVRCP), battery
- pairing, and receiving files (OBEX); Android over adb; this screen on the phone
- FM radio (an RTL-SDR, demodulated here), NFC tags, infrared remotes
- the Android companion app (android/)

The parts that hold a connection open (the line, the phone link, the watch, the
Bluetooth roles, receiving files) run in the service; the rest is a library
(`shani_link`) the app, the shell and Chronoa's skills import.

## The app
`shani-link-app` (`dev.shani.Link.App`, "Shani Link") is the same plumbing for a
person: Phone (the landline dialer, the paired phone's calls, texts, contacts,
music, files, and its keys as a Bluetooth remote), Watch, Devices (paired
devices, Android over adb - battery, screenshot, a link or a file to the phone -
the phone link, this screen on the phone - and Connect a device), Radio, NFC, Remote, and
Background. It has no assistant consent: a button is pressed by the person it
acts for. What runs with the window closed has a plain switch on Background
(`shani_link/settings.py`, set through the service so it applies at once).
`shani-link-app --page=watch` opens a page; `--connect[=bluetooth|android|kdeconnect]`
opens Connect a device.

Video is a local socket, not D-Bus: `VideoSocket()` names it; each frame is a
12-byte header (width, height, length; big-endian u32) and RGB24.

## Layout
```
shani_link/landline/   the line: provisioning, SIP, RTP audio (pw-cat), video
shani_link/service.py  the D-Bus service
shani_link/app/        the app's window and pages (moved from Chronoa's device panels)
bin/shani-link         the service's launcher
bin/shani-link-app     the app's launcher
data/                  the systemd user unit, D-Bus activation file, the app's desktop file
tests/                 a fake JioFiber router, fake BlueZ/adb/KDE Connect; the service on a
                       private bus; the app's pages on a private Broadway display
```
Settings live in `~/.config/shani-link/` (carried over once from
`~/.config/shani-chronoa/`, where the landline began).

## Tests
`python3 -m pytest` - everything runs against fakes: a fake router (provisioning,
SIP digest, RTP both ways) and a private session bus. Nothing reaches the real
line, Bluetooth or the person's own config. tests/conftest.py starts a private
Broadway display for the app's pages (GTK tests skip without `gtk4-broadwayd`;
the person's own display is never used) and points both buses nowhere.
