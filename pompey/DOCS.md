# Home Assistant notes

The user journey — add the repo, install, paste Proton, connect Plex, what you open, how updates should work — is the [root README](../README.md). This page is only Supervisor, Plex-in-Docker, and storage details that do not belong in that guide.

## If Pompey does not appear in the store

The GitHub repo can be public. Home Assistant still **does not publish or pull a Docker image**; Supervisor builds `pompey/` on the machine.

1. **Settings → Apps → Install app** (the store, not already-installed apps).
2. ⋮ → Repositories → `https://github.com/esper256/ha-pompey`.
3. If you added it **while the repo was still private**, remove that repository and add it again.
4. ⋮ → Check for updates. Custom repositories are often at the **bottom**.

Or copy this folder to `/addons/pompey` (USB / Samba / SSH), then Check for updates.

Then **Settings → System → Logs → Supervisor**. Look for `Can't read` / `pompey`. An invalid `config.yaml` is skipped with no store card (that is what `timeout: 1800` did — the schema maximum is 300). Pompey only lists on **aarch64** and **amd64**.

## VPN and Ingress

Start the app and open **Pompey** in the sidebar. Paste the Proton WireGuard `.conf`. Pompey reads PrivateKey, Address, peer PublicKey, and Endpoint from that file and adds keepalive if Proton omitted it. A valid paste starts the tunnel even if this Home Assistant host has no legacy iptables filter table (common on HAOS). The kill switch uses nft when it can; if no firewall table exists, the log says so and the tunnel still comes up.

The kill switch is **OUTPUT** (internet from this app). Home Assistant Ingress to port 8099 and LAN clients to published **5055** and **9696** are **INPUT** from Supervisor / Docker and are not blocked. If the tunnel cannot start, the wait screen stays up; the container is not halted.

Home Assistant’s option list is only Plex and source. It does not ask for WireGuard internals. Those are fixed: Proton DNS `10.2.0.1`, RFC1918 LAN plus Supervisor `172.30.32.0/23`, media at `/media`.

Ingress **8099** is always Pompey's wait/status UI. Search is Seerr on published host port **5055**. Sources are Prowlarr on published host port **9696** (Seerr cannot add indexers). Putting Seerr under Ingress breaks on the next minified Next.js chunk.

Plex in the options is a **shortcut**. Seerr’s web setup is the canonical Plex connection to **the Plex app you already run**. Pompey does not run Plex. After that wizard, Seerr already has Plex; Pompey does not copy the token back into Home Assistant options. Source URL/key are for Prowlarr.

## Plex in another Docker app

Pompey cannot see unpublished container-to-container DNS names. Proton’s DNS will not resolve `plex.local`. Use a **numeric IP** and a published port — table in the [README](../README.md#3-fill-the-four-options).

The default LAN list already includes `10.0.0.0/8`, `172.16.0.0/12`, and `192.168.0.0/16`. Supervisor’s `172.30.32.0/23` is always allowed.

Do not publish download peer ports on the Home Assistant host. Leave **5055** published for Seerr and **9696** for Prowlarr.

## Storage (inside the container)

```text
/media/Kid Friendly Movies
/media/Movies
/media/Kid Friendly TV
/media/TV
/media/downloads/incomplete
/media/downloads/complete
```

App config: `/addon_configs/<hash>_pompey/`. Engines: add-on **data** (`/data/engines` in the container). Restarting does not re-download engines that are already present.
