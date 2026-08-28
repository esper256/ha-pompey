# Changelog

## 0.2.22

- Fixed the wait screen getting stuck on **Could not finish connecting search** after a media-folder change. Search already had Radarr; updating the library path is allowed again.

## 0.2.21

- Media folder defaults match this house: `/media/dlna`, with `Movies/Not Kid Friendly`, `Movies/Kid Friendly`, `TV/Not Kid Friendly`, and `TV/Kid Friendly`. Change the Home Assistant options if a share or library folder is named differently. If you already saved the old `/media` defaults, update Configuration once and restart.

## 0.2.20

- Home Assistant options for **where files go**: media folder (default `/media`) plus movies, kid movies, TV, and kid TV folders relative to it. In-progress downloads use `downloads/` under the media folder. Point Plex at those same library folders. A network Media share is `/media/<the name you gave it>`. Saving the options and restarting the app updates an existing install.

## 0.2.19

- Home Assistant no longer has Plex or source fields for this app. Connect Plex in Seerr’s first-run wizard. Add sources with **Open sources** (Prowlarr).

## 0.2.18

- **Open sources** in the sidebar. Prowlarr is on this machine’s port **9696**. First visit asks you to set a login.
- Radarr, Sonarr, and qBittorrent stay unpublished. Search stays on port **5055**.
- Updating keeps existing Prowlarr sources.

## 0.2.17

- Seerr no longer warns that `/config/seerr` is not a volume. Search data already persists.
- Sidebar stays Pompey’s wait and status screen. Search stays on port **5055**.

## 0.2.16

- Search is on this machine’s port **5055**. The sidebar shows **Open search** when ready; it is not an iframe of Seerr.
- Plex is a separate app. Pompey does not run Plex.

## 0.2.15

- Fixed Plex setup on the search screen doing nothing.

## 0.2.14

- Fixed the wait screen never finishing: search opens for the Plex wizard, then the movie and TV engines connect after the first admin exists.

## 0.2.13

- Fixed the first-run Plex button doing nothing.
- Plex in Home Assistant options is optional. You can finish Plex in Seerr. Use a numeric IP (Proton DNS will not resolve LAN names).

## 0.2.12

- Wait screen stays up if search cannot be connected, instead of opening an empty search page.

## 0.2.11

- First start can download the engines on Home Assistant OS (unpack no longer fails).

## 0.2.10

- Proton tunnel stays up (WireGuard config is accepted).

## 0.2.9

- Proton tunnel stays up on Home Assistant OS when network sysctl is read-only.
- App log timestamps WireGuard lines.
- Engines wait until the VPN handshake exists, not only until the Proton file is saved.

## 0.2.8

- A failed Proton tunnel no longer takes down the whole app. The wait screen stays up.

## 0.2.7

- App log is quieter and every Pompey line has a time.

## 0.2.5

- Pasting a Proton WireGuard file works on Home Assistant OS.
- Engines no longer retry the VPN before a Proton file exists.

## 0.2.4

- Proton paste box no longer flickers during startup.

## 0.2.3

- Home Assistant options are Plex address, Plex token, source URL, and source key.
- Paste the Proton WireGuard file on the wait screen. Missing Proton no longer stops the app.

## 0.2.2

- Pompey appears under **Install app** (start timeout was too high and hid it).
- Start wait is 300 seconds. Engines still download after the app is up.

## 0.2.1

- First Home Assistant OS build: wait screen, Proton, kill switch, starts on reboot.
- Proton server hostnames resolve so the tunnel can connect.
- Use a numeric IP for Plex. LAN names will not resolve through Proton.
- Titles route to kid or general folders from the rating (unknown goes to general).
- Store icon and logo.

## 0.2.0

- After Proton is up, downloads search and the hidden engines and wires them.
- Wait screen, then search.
- Kid vs general library folders.
- NAT-PMP for downloads.

## 0.1.1

- Named **Pompey**.
- Supervisor builds the app on the machine. No Docker image to pull.

## 0.1.0

- Starting point: Proton WireGuard and kill switch. Search is not connected yet.
