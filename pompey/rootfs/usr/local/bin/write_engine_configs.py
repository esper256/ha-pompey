#!/usr/bin/env python3
"""Write persistent engine configuration before services start."""
import configparser
import io
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from pompey_common import qbit_seed_conf, simultaneous_downloads


def _xml_prologue(body: str) -> str:
    m = re.search(r"<(?![?!])([A-Za-z0-9_]+)(?:\s|/|>)", body)
    if m and m.start() > 0:
        return body[: m.start()]
    return ""


def set_tag(body: str, tag: str, value: str) -> str:
    """Set or add a top-level child element in an XML config document."""
    prefix = _xml_prologue(body)
    try:
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
        root = ET.fromstring(body, parser=parser)
        elem = root.find(tag)
        if elem is not None:
            elem.text = str(value)
        else:
            new_elem = ET.SubElement(root, tag)
            new_elem.text = str(value)
        ET.indent(root, space="  ")
        out = ET.tostring(root, encoding="unicode")
        if not out.endswith("\n"):
            out += "\n"
        return (prefix + out) if prefix else out
    except Exception:
        pat = rf"(<{tag}>)[^<]*(</{tag}>)"
        if re.search(pat, body):
            return re.sub(pat, rf"\g<1>{value}\g<2>", body, count=1)
        if "</Config>" in body:
            return body.replace("</Config>", f"  <{tag}>{value}</{tag}>\n</Config>", 1)
        return body


def write_xml(path, port, api_key, instance, bind="127.0.0.1"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.isfile(path):
        return
    body = f"""<Config>
  <BindAddress>{bind}</BindAddress>
  <Port>{port}</Port>
  <SslPort>{port + 1000}</SslPort>
  <EnableSsl>False</EnableSsl>
  <ApiKey>{api_key}</ApiKey>
  <AuthenticationMethod>None</AuthenticationMethod>
  <AuthenticationRequired>DisabledForLocalAddresses</AuthenticationRequired>
  <LogLevel>warn</LogLevel>
  <LaunchBrowser>False</LaunchBrowser>
  <Branch>master</Branch>
  <UrlBase></UrlBase>
  <InstanceName>{instance}</InstanceName>
  <UpdateAutomatically>False</UpdateAutomatically>
  <UpdateMechanism>Docker</UpdateMechanism>
</Config>
"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)


def drop_auth_none(body: str) -> str:
    """Remove AuthenticationMethod if it is set to None.

    Missing AuthenticationMethod makes Prowlarr's first UI visit the login
    setup (Servarr wiki). Leave Forms / External alone.
    """
    prefix = _xml_prologue(body)
    try:
        parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
        root = ET.fromstring(body, parser=parser)
        removed = False
        for elem in list(root.findall("AuthenticationMethod")):
            if (elem.text or "").strip().lower() == "none":
                root.remove(elem)
                removed = True
        if removed:
            ET.indent(root, space="  ")
            out = ET.tostring(root, encoding="unicode")
            if not out.endswith("\n"):
                out += "\n"
            return (prefix + out) if prefix else out
        return body
    except Exception:
        return re.sub(
            r"\s*<AuthenticationMethod>None</AuthenticationMethod>",
            "",
            body,
            count=1,
            flags=re.I,
        )


def publish_prowlarr(path, api_key):
    """Listen on all interfaces. First UI visit sets Prowlarr's own login.

    Existing 0.2.16 files were localhost + AuthenticationMethod None. Patch
    BindAddress so a rebuild publishes the UI without wiping indexers.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.isfile(path):
        write_xml(path, 9696, api_key, "Prowlarr", bind="*")
    with open(path, "r", encoding="utf-8") as fh:
        body = fh.read()
    body = set_tag(body, "BindAddress", "*")
    body = set_tag(body, "LogLevel", "warn")
    body = drop_auth_none(body)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)


def pin_arr_log_level(path):
    """Warn, not info. Info is every indexer search; Prowlarr's UI already shows flakes."""
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        body = fh.read()
    new = set_tag(body, "LogLevel", "warn")
    if new != body:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new)


def pin_arr_docker_updates(path):
    """Arr must not self-replace inside this container. Re-stamp every boot.

    Prowlarr's UI can flip BuiltIn on; an Arr rewrite can drop our tags.
    Pompey is the updater (fetch-engines), same as Docker compose pull.
    """
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        body = fh.read()
    new = set_tag(body, "UpdateAutomatically", "False")
    new = set_tag(new, "UpdateMechanism", "Docker")
    if new != body:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new)


def qbit_queue_settings(active: int) -> dict:
    """Cap transferring torrents; do not let stalled ones fill those slots.

    qBittorrent's own default is 3 active downloads and it still counts
    stalled/seedless torrents against that number — that is what froze
    the household queue. IgnoreSlowTorrentsForQueueing is the built-in
    "still trying, not blocking" switch. MaxActiveTorrents must be
    higher than MaxActiveDownloads: slow torrents still count against
    the total, which is why ignore-slow looks broken at the stock 5.
    """
    return {
        r"Session\QueueingSystemEnabled": "true",
        r"Session\MaxActiveDownloads": str(active),
        r"Session\MaxActiveUploads": str(active),
        r"Session\MaxActiveTorrents": str(max(active + 16, 20)),
        r"Session\IgnoreSlowTorrentsForQueueing": "true",
        r"Session\SlowTorrentsDownloadRate": "2",
        r"Session\SlowTorrentsUploadRate": "2",
        r"Session\SlowTorrentsInactivityTimer": "180",
    }


# qBittorrent 4 stored share limits as Session\MaxRatio*. 5.2 ignores those
# and reads GlobalMaxRatio / GlobalMaxSeedingMinutes / ShareLimitAction.
OBSOLETE_QBIT_SEED_KEYS = (
    r"Session\MaxRatioEnabled",
    r"Session\MaxRatio",
    r"Session\MaxSeedingTimeEnabled",
    r"Session\MaxSeedingTime",
    r"Session\MaxRatioAct",
)


def load_qbit_parser(text: str) -> configparser.RawConfigParser:
    parser = configparser.RawConfigParser(
        delimiters=("=",),
        comment_prefixes=("#", ";"),
        strict=False,
        empty_lines_in_values=False,
    )
    parser.optionxform = str
    try:
        parser.read_string(text)
    except configparser.MissingSectionHeaderError:
        parser.read_string("[BitTorrent]\n" + text)
    return parser


def dump_qbit_parser(parser: configparser.RawConfigParser) -> str:
    out = io.StringIO()
    parser.write(out, space_around_delimiters=False)
    val = out.getvalue()
    return val.rstrip("\n") + "\n"


def set_qbit_option(
    parser: configparser.RawConfigParser,
    key: str,
    value: str,
    default_section: str = None,
) -> None:
    found = False
    for sec in parser.sections():
        if key in parser[sec]:
            parser[sec][key] = str(value)
            found = True
    if found:
        return
    if default_section is None:
        if key.startswith("Session\\"):
            default_section = "BitTorrent"
        elif any(
            key.startswith(p)
            for p in ("Connection\\", "Downloads\\", "WebUI\\", "General\\")
        ):
            default_section = "Preferences"
        elif key.startswith("FileLogger\\"):
            default_section = "Application"
        else:
            default_section = "BitTorrent"
    if not parser.has_section(default_section):
        parser.add_section(default_section)
    parser[default_section][key] = str(value)


def drop_obsolete_qbit_seed(text: str) -> str:
    try:
        parser = load_qbit_parser(text)
        for sec in parser.sections():
            for key in OBSOLETE_QBIT_SEED_KEYS:
                if key in parser[sec]:
                    del parser[sec][key]
        return dump_qbit_parser(parser)
    except Exception:
        for key in OBSOLETE_QBIT_SEED_KEYS:
            text = re.sub(rf"(?m)^{re.escape(key)}=.*\n?", "", text)
        return text


def patch_qbit_seed(text: str, settings: dict) -> str:
    try:
        parser = load_qbit_parser(text)
        for sec in parser.sections():
            for obs in OBSOLETE_QBIT_SEED_KEYS:
                if obs in parser[sec]:
                    del parser[sec][obs]
        for key, value in settings.items():
            set_qbit_option(parser, key, value, default_section="BitTorrent")
        return dump_qbit_parser(parser)
    except Exception:
        text = drop_obsolete_qbit_seed(text)
        for key, value in settings.items():
            pat = rf"(?m)^{re.escape(key)}=.*$"
            line = f"{key}={value}"
            if re.search(pat, text):
                text = re.sub(pat, lambda _m, ln=line: ln, text, count=1)
                continue
            if re.search(r"(?m)^Session\\DisableAutoTMMByDefault=.*$", text):
                text = re.sub(
                    r"(?m)^(Session\\DisableAutoTMMByDefault=.*)$",
                    lambda m, ln=line: f"{m.group(1)}\n{ln}",
                    text,
                    count=1,
                )
                continue
            if re.search(r"(?m)^\[BitTorrent\]\s*$", text):
                text = re.sub(
                    r"(?m)^(\[BitTorrent\])\s*$",
                    lambda m, ln=line: f"{m.group(1)}\n{ln}",
                    text,
                    count=1,
                )
                continue
            text += f"\n[BitTorrent]\n{line}\n"
        return text


def patch_qbit_paths(text: str, media: str = None, log_dir: str = None) -> str:
    if media is None:
        media = getattr(sys.modules.get(__name__), "_current_media", None) or "/share"
    if log_dir is None:
        cfg = os.environ.get("POMPEY_CONFIG", "/config")
        log_dir = (
            getattr(sys.modules.get(__name__), "_current_log_dir", None)
            or f"{cfg}/qBittorrent/logs"
        )
    complete = f"{media}/downloads/complete"
    incomplete = f"{media}/downloads/incomplete"

    try:
        parser = load_qbit_parser(text)
        keys_to_set = [
            ("BitTorrent", r"Session\DefaultSavePath", complete),
            ("BitTorrent", r"Session\TempPath", incomplete),
            ("Preferences", r"Downloads\SavePath", complete),
            ("Preferences", r"Downloads\TempPath", incomplete),
            ("Application", r"FileLogger\Path", log_dir),
            ("Application", r"FileLogger\Enabled", "true"),
            ("BitTorrent", r"Session\Interface", "wg0"),
            ("BitTorrent", r"Session\InterfaceName", "wg0"),
            ("Preferences", r"Connection\Interface", "wg0"),
            ("Preferences", r"Connection\InterfaceName", "wg0"),
        ]
        for default_sec, key, val in keys_to_set:
            set_qbit_option(parser, key, val, default_section=default_sec)
        return dump_qbit_parser(parser)
    except Exception:
        for key, value in (
            (r"Session\DefaultSavePath", complete),
            (r"Session\TempPath", incomplete),
            (r"Downloads\SavePath", complete),
            (r"Downloads\TempPath", incomplete),
            (r"FileLogger\Path", log_dir),
            (r"FileLogger\Enabled", "true"),
            (r"Session\Interface", "wg0"),
            (r"Session\InterfaceName", "wg0"),
            (r"Connection\Interface", "wg0"),
            (r"Connection\InterfaceName", "wg0"),
        ):
            pat = rf"(?m)^{re.escape(key)}=.*$"
            if re.search(pat, text):
                text = re.sub(pat, lambda _m, k=key, v=value: f"{k}={v}", text, count=1)
        for key, value, section in (
            (r"Session\Interface", "wg0", r"[BitTorrent]"),
            (r"Session\InterfaceName", "wg0", r"[BitTorrent]"),
            (r"Connection\Interface", "wg0", r"[Preferences]"),
            (r"Connection\InterfaceName", "wg0", r"[Preferences]"),
        ):
            if re.search(rf"(?m)^{re.escape(key)}=", text):
                continue
            heading = rf"(?m)^({re.escape(section)})\s*$"
            if re.search(heading, text):
                text = re.sub(
                    heading,
                    lambda m, k=key, v=value: f"{m.group(1)}\n{k}={v}",
                    text,
                    count=1,
                )
            else:
                text += f"\n{section}\n{key}={value}\n"
        if not re.search(rf"(?m)^{re.escape(r'FileLogger\Path')}=", text):
            if re.search(rf"(?m)^{re.escape(r'FileLogger\Enabled')}=", text):
                text = re.sub(
                    rf"(?m)^({re.escape(r'FileLogger\Enabled')}=.*)$",
                    lambda m: f"{m.group(1)}\nFileLogger\\Path={log_dir}",
                    text,
                    count=1,
                )
            elif re.search(r"(?m)^\[Application\]\s*$", text):
                text = re.sub(
                    r"(?m)^(\[Application\])\s*$",
                    lambda m: (
                        f"{m.group(1)}\nFileLogger\\Enabled=true\n"
                        f"FileLogger\\Path={log_dir}"
                    ),
                    text,
                    count=1,
                )
            else:
                text = (
                    "[Application]\n"
                    "FileLogger\\Enabled=true\n"
                    f"FileLogger\\Path={log_dir}\n\n"
                    + text
                )
        if not re.search(rf"(?m)^{re.escape(r'FileLogger\Enabled')}=", text):
            text = re.sub(
                rf"(?m)^({re.escape(r'FileLogger\Path')}=.*)$",
                lambda m: f"FileLogger\\Enabled=true\n{m.group(1)}",
                text,
                count=1,
            )
        return text


def configure_engines(secrets_path: str, media_path: str, config_dir: str = None) -> None:
    if config_dir is None:
        config_dir = os.environ.get("POMPEY_CONFIG", "/config")

    with open(secrets_path, "r", encoding="utf-8") as fh:
        secrets = json.load(fh)
    media = media_path.rstrip("/")

    mod = sys.modules.get(__name__)
    if mod:
        setattr(mod, "_current_media", media)
        setattr(mod, "_current_log_dir", f"{config_dir}/qBittorrent/logs")
        setattr(mod, "secrets", secrets)
        setattr(mod, "media", media)
        setattr(mod, "config", config_dir)

    write_xml(f"{config_dir}/sonarr/config.xml", 8989, secrets["sonarr_api_key"], "Sonarr")
    write_xml(f"{config_dir}/radarr/config.xml", 7878, secrets["radarr_api_key"], "Radarr")
    pin_arr_log_level(f"{config_dir}/sonarr/config.xml")
    pin_arr_log_level(f"{config_dir}/radarr/config.xml")
    publish_prowlarr(f"{config_dir}/prowlarr/config.xml", secrets["prowlarr_api_key"])
    pin_arr_docker_updates(f"{config_dir}/sonarr/config.xml")
    pin_arr_docker_updates(f"{config_dir}/radarr/config.xml")
    pin_arr_docker_updates(f"{config_dir}/prowlarr/config.xml")

    pbk = secrets["qbit_pbkdf2"]
    user = secrets["qbit_user"]
    incomplete = f"{media}/downloads/incomplete"
    complete = f"{media}/downloads/complete"

    seed = qbit_seed_conf()
    seed[r"Session\DisableAutoTMMByDefault"] = "true"
    queue = qbit_queue_settings(simultaneous_downloads())
    # Locals: f-string expressions cannot contain backslashes on older Python.
    max_ratio = seed[r"Session\GlobalMaxRatio"]
    seed_minutes = seed[r"Session\GlobalMaxSeedingMinutes"]
    inactive_minutes = seed[r"Session\GlobalMaxInactiveSeedingMinutes"]
    share_action = seed[r"Session\ShareLimitAction"]
    queue_on = queue[r"Session\QueueingSystemEnabled"]
    max_dl = queue[r"Session\MaxActiveDownloads"]
    max_ul = queue[r"Session\MaxActiveUploads"]
    max_all = queue[r"Session\MaxActiveTorrents"]
    ignore_slow = queue[r"Session\IgnoreSlowTorrentsForQueueing"]
    slow_dl = queue[r"Session\SlowTorrentsDownloadRate"]
    slow_ul = queue[r"Session\SlowTorrentsUploadRate"]
    slow_wait = queue[r"Session\SlowTorrentsInactivityTimer"]
    qbit_text = f"""[Application]
FileLogger\\Enabled=true
FileLogger\\Path={config_dir}/qBittorrent/logs

[AutoRun]
enabled=false

[BitTorrent]
Session\\DefaultSavePath={complete}
Session\\TempPath={incomplete}
Session\\TempPathEnabled=true
Session\\Interface=wg0
Session\\InterfaceName=wg0
Session\\InterfaceAddress=
Session\\Port=6881
Session\\UPnP=false
Session\\DisableAutoTMMByDefault=true
Session\\QueueingSystemEnabled={queue_on}
Session\\MaxActiveDownloads={max_dl}
Session\\MaxActiveUploads={max_ul}
Session\\MaxActiveTorrents={max_all}
Session\\IgnoreSlowTorrentsForQueueing={ignore_slow}
Session\\SlowTorrentsDownloadRate={slow_dl}
Session\\SlowTorrentsUploadRate={slow_ul}
Session\\SlowTorrentsInactivityTimer={slow_wait}
Session\\GlobalMaxRatio={max_ratio}
Session\\GlobalMaxSeedingMinutes={seed_minutes}
Session\\GlobalMaxInactiveSeedingMinutes={inactive_minutes}
Session\\ShareLimitAction={share_action}

[LegalNotice]
Accepted=true

[Network]
PortForwardingEnabled=false

[Preferences]
Connection\\PortRangeMin=6881
Connection\\UPnP=false
Connection\\Interface=wg0
Connection\\InterfaceName=wg0
Downloads\\SavePath={complete}
Downloads\\TempPathEnabled=true
Downloads\\TempPath={incomplete}
General\\Locale=en
WebUI\\Address=127.0.0.1
WebUI\\Port=8080
WebUI\\LocalHostAuth=false
WebUI\\AuthSubnetWhitelistEnabled=true
WebUI\\AuthSubnetWhitelist=127.0.0.1, ::1
WebUI\\Username={user}
WebUI\\Password_PBKDF2="{pbk}"
WebUI\\CSRFProtection=false
WebUI\\HostHeaderValidation=false
WebUI\\ClickjackingProtection=false
"""
    # v4: profile/qBittorrent/qBittorrent.conf
    # v5: profile/qBittorrent/config/qBittorrent.conf
    for qconf in (
        f"{config_dir}/qBittorrent/qBittorrent.conf",
        f"{config_dir}/qBittorrent/config/qBittorrent.conf",
    ):
        os.makedirs(os.path.dirname(qconf), exist_ok=True)
        if os.path.isfile(qconf):
            with open(qconf, "r", encoding="utf-8") as fh:
                existing = fh.read()
            body = patch_qbit_paths(existing, media=media, log_dir=f"{config_dir}/qBittorrent/logs")
            body = patch_qbit_seed(body, seed)
            body = patch_qbit_seed(body, queue)
            with open(qconf, "w", encoding="utf-8") as fh:
                fh.write(body)
            os.chmod(qconf, 0o600)
            continue
        with open(qconf, "w", encoding="utf-8") as fh:
            fh.write(qbit_text)
        os.chmod(qconf, 0o600)


def main(argv=None) -> int:
    if argv is None:
        argv = sys.argv
    if len(argv) < 3:
        sys.stderr.write(f"Usage: {argv[0]} <secrets.json> <media_dir>\n")
        return 1
    configure_engines(argv[1], argv[2])
    return 0


if __name__ == "__main__":
    sys.exit(main())
