# Changelog

## 0.1.1 — 2026-08-17

### Changed

- Drop Gluetun. The container already shares one network namespace, so a sidecar VPN client adds no routing capability.
- Bring up Proton WireGuard with `wg-quick` (`wg0`), an iptables OUTPUT kill switch, and `natpmpc` for port forwarding.
- Replace country/private-key Gluetun options with a Proton `wg0.conf` file (or the equivalent fields).

## 0.1.0 — 2026-08-17

### Added

- Experimental skeleton with an Ingress launcher and HA options schema.
