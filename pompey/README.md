# Pompey (the Home Assistant app)

The household guide is the [root README](../README.md). This folder is what Supervisor builds.

**0.2.27** is the current cut on a real Home Assistant OS machine. Sidebar is Pompey (Proton, status, Open search, Open sources). Search is Seerr on port 5055. Sources are Prowlarr on port 9696. Plex is a separate app. Media folders are Home Assistant options (defaults match this house). After a title is in the library, stop sharing is the default so finished torrents do not sit in RAM. Auto-grab prefers 1080p WEB-DL/BluRay encodes over remux/4K. Finished downloads are imported from `downloads/complete` into those library folders. The app log tags every hidden service and copies qBittorrent’s file log into it. See the README for the intended journey, what is not ready, and the roadmap.

- Changelog: [CHANGELOG.md](CHANGELOG.md)
- Plan: [../VISION.md](../VISION.md)
