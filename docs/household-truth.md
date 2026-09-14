# Household behavior and limits

The current behavior is documented in the [household guide](../README.md). This file records the limits that must remain visible during development:

- Pompey runs on Home Assistant OS under Supervisor. Local HTTP fixtures, namespaces and Docker smoke tests do not reproduce HAOS.
- Search is Seerr on port 5055; sources are Prowlarr on 9696. Ingress remains Pompey status and setup. Plex runs separately.
- A compatible full-tunnel WireGuard configuration is required. NAT-PMP forwarding is optional and provider-dependent.
- Recyclarr must configure real profiles before requests can be wired. Placeholder profiles are not a successful setup.
- Arr owns ordinary completed downloads and upgrades. Continued sharing may need a second copy on filesystems without hardlinks.
- Unknown ratings go to general. Unmatched manual downloads and extras remain for review. Pompey does not invent TV episode identities.
- Closing a previously observed request unmonitors it; this does not remove library files or force-delete downloads. Declined requests stay declined.
- Engine releases are pinned and verified. Upstream candidate discovery never silently promotes a release.
- Final acceptance requires the [manual HAOS checklist](testing.md), including actual storage, Ingress, VPN reconnection and Plex discovery.
