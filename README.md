# Shani Link

**Your phone, your landline and your watch, on your computer.**

Shani Link lets you make and answer phone calls and send texts from your
computer, keep one address book and one call history for every line, see
your watch's steps and sleep, and work with the gadgets around you - an
Android phone on a USB cable, an FM radio stick, an NFC reader, an infrared
receiver. Nothing has to be typed into a settings file: you pick from what it
finds, and every page says plainly what it needs when something is missing.

![The Phone page, with a phone connected](docs/screenshots/phone-calls.png)

## What it does for you

- **Calls on the computer** - on your mobile phone (paired over Bluetooth),
  on your JioFiber landline, or on the computer's own mobile modem. One dial
  pad, one call history, contact names as you type.
- **Texts** - read your phone's conversations and reply from the keyboard;
  new texts and missed calls arrive as notifications with Reply and Call back.
- **Contacts** - your own address book (standard vCard files), with your
  phone's contacts beside it.
- **Your watch or fitness band** - steps, sleep, stress and workouts at a
  glance, and Find when it is lost.
- **Android phone tools** - battery and storage, a screenshot, a screen
  recording, a link opened, an app opened or a file sent to the phone, over a
  USB cable.
- **Sharing files** - send a file to your phone; receive what your phone
  shares to the computer over Bluetooth into Downloads.
- **This screen on your phone** - over adb (a USB cable, or Wi-Fi debugging) at
  the phone's own size, or over the phone's Bluetooth network when adb cannot
  reach it. Share the whole screen or **one window** (the desktop asks which);
  on niri the window can then be changed with the dynamic cast target, without
  asking again. The same portal stream can be **recorded to a file**
  (`python3 -m shani_link.screen_stream --record out.webm`), or **a single frame
  saved** (`--stills shot.png`); add `--window` for one window. **Touch and type** (off until you turn it on) makes fingers, a
  stylus and the phone's keyboard drive the desktop through virtual devices; a
  window with `block-out-from "screencast"` is black on the phone. On the phone
  the same page can **play the desktop's sound**, and **lend the desktop its
  microphone** (cable) or **camera** (PipeWire-aware apps) - each a button that
  is off until pressed there, and off again when pressed once more.
- **FM radio, NFC and infrared** - listen to an FM station live or play a
  recording, read NFC tags and write links to stickers, learn the buttons of
  a remote control and press them from here.

### What you need for each

| Feature | What it needs |
|---|---|
| Calls and texts through your phone | A phone paired with this computer over Bluetooth (calls need PipeWire's Bluetooth telephony; texts need the phone to share messages over Bluetooth). With KDE Connect or GSConnect instead, Call opens the dialer on the phone. |
| Landline calls | A JioFiber landline and its router; set up once on the Phone page with the code the router texts you |
| Modem calls | A computer with a built-in mobile modem that can make voice calls (through ModemManager) |
| Contacts and call history | Nothing - they are kept on this computer |
| Watch dashboard | A MoYoung watch or band (the ones the Da Fit app uses). Other Bluetooth Low Energy devices show their standard details (battery, firmware) |
| Android phone tools | A USB cable and USB debugging turned on in the phone's Developer options |
| Phone notifications, line, Wi-Fi and temperature | The same adb, over the service's `Phone` interface (nothing is read from a desktop that has no phone) |
| Phone link (files over Wi-Fi) | The KDE Connect app on the phone, on the same Wi-Fi; to receive, turn on "Receive files from your phone over Wi-Fi" (Background) - it opens KDE Connect's ports in the firewall until turned off or a restart |
| Phone link (one clipboard) | The KDE Connect app on the phone, paired; turn on "Share the clipboard with your phone" (Background, off by default). Copy on one, paste on the other; what a password manager marks secret is never sent, and the shell's clipboard history says what came from the phone |
| This screen on your phone | USB debugging - on a cable or over Wi-Fi - or the phone's Bluetooth tethering; GStreamer on this computer |
| Phone internet over the cable | A USB cable with USB debugging on: the phone's RNDIS network through NetworkManager |
| Touch on the phone | `/dev/uinput` (the `input` group; ShaniOS ships `80-uinput.rules`) and the same GStreamer |
| Sound on the phone | PipeWire and a default audio sink (`pw-metadata`) |
| Microphone from the phone | `snd-aloop` (in-tree; load with `index=7`) and a cable (a secure context) |
| FM radio | An RTL-SDR USB stick (RTL2832U) and `rtl_fm` (rtl-sdr) to listen live, played through PipeWire (`pw-play`); recordings play without the stick |
| NFC | A USB NFC reader that works with libnfc (for example an ACR122U) |
| Infrared remote | An infrared receiver (a `/dev/lirc*` device) to learn buttons; a transmitter (a `/dev/lirc*` that can send) to press a remote's buttons through lircd (`irsend`), or send a raw code with `ir-ctl` (v4l-utils) |

## A tour

### Phone

The first time, with no phone yet, the page says so and offers one button:

![No phone connected yet](docs/screenshots/phone-empty.png)

**Connect your phone** (or the **+** in the header) opens Connect a device,
which finds what is nearby - you pick, you do not type:

![Connect a device](docs/screenshots/connect.png)

With a phone connected, the dial pad calls on whichever line you have. The
call controls appear when a call can actually be made. Below the dial pad is
one history for every line - landline, phone and mobile modem - with Call
back on each row:

![Recent calls across every line](docs/screenshots/phone-history.png)

**Messages** shows your phone's conversations; pick a contact to start a new
one.

![Messages](docs/screenshots/phone-messages.png)

**Contacts** is a full address book - search, sections, photos, import and
export .vcf, share as a QR code - with the phone's contacts beside yours:

![Contacts](docs/screenshots/phone-contacts.png)
![A contact](docs/screenshots/phone-contact.png)

**Music** controls what is playing on the phone, **Files** sends a file to it
(and switches on receiving from it), and **Remote** turns this computer into
the phone's Bluetooth remote - take a photo, skip a song, change the volume.

| Music | Files | Remote |
|---|---|---|
| ![Music](docs/screenshots/phone-music.png) | ![Files](docs/screenshots/phone-files.png) | ![Remote](docs/screenshots/phone-remote.png) |

### Watch

Today's steps against your goal, last night's sleep by stage, the day's
stress, and your workouts. **Find** makes the watch buzz; the switch keeps
it connected so its own find-my-phone, music and camera buttons work here.

![Watch](docs/screenshots/watch.png)

| With no watch yet | Dark style |
|---|---|
| ![No watch yet](docs/screenshots/watch-empty.png) | ![Watch, dark](docs/screenshots/watch-dark.png) |

### Devices

Paired devices at a glance; an Android phone on a USB cable; your phone on
the same Wi-Fi with KDE Connect; and this screen shown on your phone - over adb
(a cable, or Wi-Fi debugging) when it can reach a phone that way, and over the
phone's Bluetooth tethering when it cannot. With nothing connected yet, the page
says so and offers the one thing to do:

The Screen page has a **Touch** switch, off until pressed: with it on, fingers on
the phone tap and drag on the desktop through a virtual touchscreen (`/dev/uinput`,
the `input` group), so the phone works as a touch display. A window marked
`block-out-from "screencast"` is black on the phone.

![No phone connected yet](docs/screenshots/devices-empty.png)

| Paired | Android |
|---|---|
| ![Paired devices](docs/screenshots/devices-paired.png) | ![Android over USB](docs/screenshots/devices-android.png) |
| **Phone link** | **Screen** |
| ![Phone link](docs/screenshots/devices-phone_link.png) | ![Screen on the phone](docs/screenshots/devices-screen.png) |

### Radio, NFC and Remote

Each says what to plug in when it is missing, and nothing has to be typed: a
frequency is picked from a list, a recording from a file chooser.

| Radio | Radio, nothing plugged in |
|---|---|
| ![Radio](docs/screenshots/radio.png) | ![Radio with no stick](docs/screenshots/radio-empty.png) |
| **NFC** | **Remote** |
| ![NFC](docs/screenshots/nfc.png) | ![Infrared remote](docs/screenshots/remote.png) |

### Background

What Shani Link does with its window closed - ring for a call, show a text,
receive a file, keep the watch connected - each with its own switch. They
start off, except two that are this computer's own: music keys on Bluetooth
headphones, and the built-in modem ringing for a call (with no such modem it
never rings).

![Background](docs/screenshots/background.png)

The Phone and Contacts pages in the dark style:

| Phone | Contacts |
|---|---|
| ![Phone, dark](docs/screenshots/phone-calls-dark.png) | ![Contacts, dark](docs/screenshots/phone-contacts-dark.png) |

The pictures are made with sample data (made-up people and calls, a stand-in
phone and watch) by `python3 tools/screenshots.py`, which renders every page
on a private display and writes `docs/screenshots/`.

---

# For developers

## shani-link
The computer's phone line, phone and watch, as one user service on the session
bus (`dev.shani.Link`). It is plumbing, not an assistant and not a settings app:
the desktop shell shows it (call card, dialer, phone tile), Shani Chronoa drives
it (its skills ask it to dial, read a text, check the watch), and any desktop -
niri with shani-shell, GNOME, Plasma - gets the same line.

### What is here
| Object | Interface | What |
|---|---|---|
| `/dev/shani/Link/Landline` | `dev.shani.Link.Landline` | The JioFiber landline through the router, like JioJoin: set up once with the texted code, then calls, keypad, mute, hold, swap, merge, history; `Event(kind, info_json)` for everything that happens on the line |
| `/dev/shani/Link/Contacts` | `dev.shani.Link.Contacts` | The address book (yours, then the phone's): search it, and name a number |
| `/dev/shani/Link/Calls` | `dev.shani.Link.Calls` | Every line at once (landline, phone, the computer's modem): the shared call history, and dial / answer / hang up / keypad on any line |
| `/dev/shani/Link/Phone` | `dev.shani.Link.Phone` | The phone's internet over Bluetooth, USB or Wi-Fi (through NetworkManager); its notifications, line, Wi-Fi and temperatures over adb |

#### Contacts, Calls and Phone, for the shell and Chronoa
Records travel as JSON strings, as `Landline.Calls` already did. Errors are
`dev.shani.Link.Error` with a sentence to show.

```
dev.shani.Link.Contacts  (/dev/shani/Link/Contacts)
  Search(s query, i limit) -> s   [{uid, name, given, family, nickname, phones:[{type, number}],
                                     emails:[{type, address}], org, role, birthday, source, has_photo}]
                                  name, number (any formatting) or email; yours first, then the
                                  phone's (source "local" / "phone"); limit <= 0 is all
  Lookup(s number) -> s           the same record for whoever has that number, or {} - the one
                                  number-to-name lookup the app, the call card and the history use

dev.shani.Link.Calls  (/dev/shani/Link/Calls)
  Lines() -> s                    [{id, name, ready}]: "landline", "phone", "modem" - only the
                                  ones this computer has
  Dial(s line, s number) -> s     line "" is the first ready one; returns what was dialled, in words
  Answer(s line) -> b
  Hangup(s line) -> b
  SendDtmf(s line, s digit) -> b  landline and modem (a phone keys its own tones)
  RecentCalls(i limit) -> s       [{id, line, number, name, direction "in"/"out", missed,
                                    when (epoch s), seconds}], newest first
  DeleteCall(s id) -> b
  ClearCalls()
  signal CallLogged(s row_json)   each call as it is logged, on any line

dev.shani.Link.Phone  (/dev/shani/Link/Phone)
  Tether(s address, b on) -> s    use the phone's internet over Bluetooth, or stop: finds or adds
                                  NetworkManager's `bluetooth`/`panu` connection for the phone
                                  ("<phone> internet") and brings it up or down. Returns what
                                  happened ("Connected through Pixel 8"); an error says to turn on
                                  Bluetooth tethering on the phone first. address "" is the phone
                                  paired over Bluetooth
  TetherState(s address) -> s     {address, phone, on, connection}
  UsbTether(b on) -> s           the phone's internet over a USB cable (USB tethering): NetworkManager
                                  brings its `usb0`/`enx*` device up, or stops. No pairing and no
                                  address; the cable is preferred when both are connected
  UsbTetherState() -> s          {device, on}
  WifiTether() -> s              the Wi-Fi this computer is on, and what the phone's hotspot needs
                                  from the person - the hotspot cannot be turned on over adb
  Notifications(i limit) -> s    the phone's notifications, as they are (dumpsys, read only)
  Usage(i limit) -> s           screen time per app in the current interval, busiest first
  Line() -> s                   the operator, registration, radio and signal (dumpsys)
  Wifi() -> s                   the phone's network, signal, link speed and band (dumpsys)
  Thermal() -> s                the phone's CPU, GPU and skin temperatures (dumpsys)
  Location() -> s               whether the phone's location is on (cmd location)
  Display() -> s                the phone's size, refresh rate, density and HDR (dumpsys)
```
A missed call on any line is also a notification with **Call back** (on the
line it came in on) and **Message**.

Moving here from Shani Chronoa, in phases - the engines and their screens;
Chronoa keeps thin skills that call this package:
- the phone link (the KDE Connect protocol, and GSConnect/KDE Connect when installed)
- the phone over plain Bluetooth: calls (PipeWire telephony), texts (MAP)
- MoYoung ("Da Fit") watches: health data, staying connected, the watch's buttons
- this computer's Bluetooth roles: a remote (BLE), music control (AVRCP), battery
- pairing, and receiving files (OBEX); Android over adb; this screen on the phone (over that adb, by cable or Wi-Fi, or Bluetooth)
- FM radio (an RTL-SDR, demodulated here), NFC tags, infrared remotes
- the Android companion app (android/chronoa-cast, now in this repo)

The parts that hold a connection open (the line, the phone link, the watch, the
Bluetooth roles, receiving files) run in the service; the rest is a library
(`shani_link`) the app, the shell and Chronoa's skills import.

### The app
`shani-link-app` (`dev.shani.Link.App`, "Shani Link") is the same plumbing for a
person: Phone (the landline dialer, the paired phone's calls, texts, contacts,
music, files, and its keys as a Bluetooth remote), Watch, Devices (paired
devices, Android over adb - battery, screenshot, a link or a file to the phone -
the phone link, this screen on the phone - and Connect a device), Radio, NFC, Remote, and
Background. It has no assistant consent: a button is pressed by the person it
acts for. What runs with the window closed has a plain switch on Background
(`shani_link/settings.py`, set through the service so it applies at once).
`shani-link-app --page=watch` opens a page; `--connect[=bluetooth|android|kdeconnect]`
opens Connect a device; `--dial[=URI]` puts a `tel:` link or a number on the dial
pad (never dialled until Call is pressed; `dev.shani.Link.Dial.desktop` makes the
app the `tel:` handler); `--message[=NUMBER]` opens Messages addressed to it.

The Phone page also does what GNOME Calls and GNOME Contacts did, so ShaniOS
ships neither:
- **Contacts**: your own address book - vCard 4.0 files in
  `$XDG_DATA_HOME/shani-link/contacts/`, one per contact, written atomically -
  with the phone's contacts beside it (read-only, "Save to my contacts"):
  sections, avatars, search by name/number/email, sort by first or last name,
  add/edit/delete with Undo, select several, import/export .vcf, share as a QR
  code (`qrencode`).
- **Calls**: one history for the landline, the phone and the modem
  (`$XDG_DATA_HOME/shani-link/calls.json`), by day, with call back, message,
  add to contacts, delete, clear; a dial pad with letters, contact suggestions
  as you type (T9), paste, hold 0 for +, and the line to call on (remembered);
  in a call: who, a timer, mute, audio output, hold (landline), keypad tones,
  hang up.
- **The computer's own mobile modem** (ModemManager's `Modem.Voice`) is a
  third line wherever a voice-capable modem exists; it rings with the same
  card as the phone (switch: "Calls on this computer's mobile modem").

Video is a local socket, not D-Bus: `VideoSocket()` names it; each frame is a
12-byte header (width, height, length; big-endian u32) and RGB24.

### A second screen, not yet

This mirrors the screen the computer has; it does not add one. niri cannot
create a virtual output (niri-wm/niri#3800, open), so there is no second
desktop to put on the phone. The one route that works on niri is a **VKMS**
virtual display — a kernel module that creates a real DRM output — and that is
a DKMS module per running kernel, Secure Boot/MOK enrolment and a polkit policy.
The project is not taking that in: a module that has to be rebuilt on every
kernel is not something an OS image can promise. When niri grows virtual
outputs, the mirror and the touch layer here are already what a second screen
would need.

Touch and typing are the half that is here: `/dev/uinput`, the `input` group, a
type-B touchscreen (ShaniOS ships `80-uinput.rules`), a pen and a keyboard. The
page sends fingers and keys over a WebSocket when it can open one - no HTTP round
trip per frame — and over POST when it cannot. A
window with `block-out-from "screencast"` is black on the phone. **Sound** is
the other half that works: the default sink's monitor, encoded to Opus and
played in the browser.

The **microphone** is the piece that works like DroidCam's Linux client: the
kernel's in-tree **snd-aloop** card (an image loads it with `index=7` so it
cannot take over the default card), the phone's PCM played into its playback
side and recorded from its capture side as `alsa_input.platform-snd_aloop.N`.
The page's **Mic on** sends signed 16-bit mono at 48 kHz as it is captured and
the link wraps it into the loopback. It needs the module loaded, which is the
image's job, and a secure context - so it is the cable road (`adb reverse` gives
the phone a `localhost` origin) and not Bluetooth tethering.

A **webcam** is a two-part answer. PipeWire itself can take a virtual camera:
`pipewiresink mode=provide` with `media.class=Video/Source` and
`media.role=Camera` puts a camera node in the graph, which PipeWire-aware
applications enumerate - Firefox with `media.webrtc.camera.allow-pipewire`, OBS
30+. **Camera on** streams JPEG frames from the phone into exactly that, and it
was checked here: the node appears in `pw-dump` beside the real webcam. Chrome
and the V4L2 applications (Zoom, Discord) do **not** enumerate PipeWire cameras,
and for those there is only `v4l2loopback` - every implementation (DroidCam's
`v4l2loopback-dc`, `scrcpy --v4l2-sink`, `obs-v4l2sink`) is that module. It is
out-of-tree, so the only way to avoid DKMS is for the image to ship it prebuilt
with its kernel. libcamera does not help: it is a capture stack, and its virtual
drivers (`vivid`, `vimc`) generate test patterns, they cannot be fed.

H.264 is a follow-up: `x264enc` ships with `shani-multimedia` (`gst-plugins-ugly`)
on a real image, and over the USB road the phone's `localhost` is a secure
context, so WebCodecs could carry sharper 1080p than VP8 does. It is not in
this tree because the encoder is absent on the dev box and a decode path nobody
has run is worse than the one that works.

### Layout
```
shani_link/landline/   the line: provisioning, SIP, RTP audio (pw-cat), video
shani_link/contacts.py the address book (vCard) and the one number-to-name lookup
shani_link/calls_log.py the call history every line shares
shani_link/mm_voice.py the computer's own modem (ModemManager voice calls)
shani_link/service.py  the D-Bus service
shani_link/app/        the app's window and pages (moved from Chronoa's device panels)
bin/shani-link         the service's launcher
bin/shani-link-app     the app's launcher
tools/screenshots.py   renders every page with sample data into docs/screenshots/
data/                  the systemd user unit, D-Bus activation file, the app's desktop file
tests/                 a fake JioFiber router, fake BlueZ/adb/KDE Connect; the service on a
                       private bus; the app's pages on a private Broadway display
```
Settings live in `~/.config/shani-link/` (carried over once from
`~/.config/shani-chronoa/`, where the landline began).

### Tests
`python3 -m pytest` - everything runs against fakes: a fake router (provisioning,
SIP digest, RTP both ways) and a private session bus. Nothing reaches the real
line, Bluetooth or the person's own config. tests/conftest.py starts a private
Broadway display for the app's pages (GTK tests skip without `gtk4-broadwayd`;
the person's own display is never used) and points both buses nowhere.
