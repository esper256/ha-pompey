# Pompey (the Home Assistant app)

The household guide is the [root README](../README.md). This folder is what Supervisor builds.

**0.2.35** is the current cut on a real Home Assistant OS machine. Sidebar is Pompey (Proton, status, Open search, Open sources). Search is Seerr on port 5055. Sources are Prowlarr on port 9696. Plex is a separate app. Media folders are Home Assistant options (defaults match this house). After a title is in the library, stop sharing is the default so finished torrents do not sit in RAM. Requesting a title offers Max, Default, or Anything. Finished movies and TV on the NAS are renamed from `downloads/complete` into the library folders (not copied). If that file was already moved, the hidden download engine forgets the torrent so it cannot start again. The app log tags every hidden service and copies qBittorrent’s file log into it. See the README for the intended journey, what is not ready, and the roadmap.

- Changelog: [CHANGELOG.md](CHANGELOG.md)
- Plan: [../VISION.md](../VISION.md)
