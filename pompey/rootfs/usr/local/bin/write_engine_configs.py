#!/usr/bin/env python3
"""Generate managed settings before engine startup; preserve unrelated preferences."""

import configparser
import io
import json
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET
from pompey_common import qbit_seed_conf, simultaneous_downloads


def write(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(body)
    temp.chmod(0o600)
    temp.replace(path)


def arr_config(path, port, key, name, published=False):
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    root = (
        ET.fromstring(path.read_text(), parser=parser)
        if path.exists()
        else ET.Element("Config")
    )
    values = {
        "BindAddress": "*" if published else "127.0.0.1",
        "Port": port,
        "SslPort": port + 1000,
        "EnableSsl": "False",
        "ApiKey": key,
        "AuthenticationRequired": "DisabledForLocalAddresses",
        "LogLevel": os.environ.get("POMPEY_LOG_LEVEL", "warn"),
        "LaunchBrowser": "False",
        "Branch": "master",
        "UrlBase": "",
        "InstanceName": name,
        "UpdateAutomatically": "False",
        "UpdateMechanism": "Docker",
    }
    if not published and root.find("AuthenticationMethod") is None:
        values["AuthenticationMethod"] = "None"
    for name, value in values.items():
        tag = root.find(name)
        if tag is None:
            tag = ET.SubElement(root, name)
        tag.text = str(value)
    if path.parent.name == "prowlarr":
        from pompey_hosts import managed_hosts, merged_hosts
        hosts = managed_hosts()
        if hosts:
            allowed = root.find("AllowedHosts")
            if allowed is None:
                allowed = ET.SubElement(root, "AllowedHosts")
            allowed.text = merged_hosts(allowed.text, hosts)
    ET.indent(root)
    write(path, ET.tostring(root, encoding="unicode") + "\n")


def configure_engines(secrets_path, media_path):
    secrets = json.loads(Path(secrets_path).read_text())
    config = Path(os.environ.get("POMPEY_CONFIG", "/config"))
    for name, port in [("Radarr", 7878), ("Sonarr", 8989), ("Prowlarr", 9696)]:
        arr_config(
            config / name.lower() / "config.xml",
            port,
            secrets[name.lower() + "_api_key"],
            name,
            name == "Prowlarr",
        )
    path = config / "qBittorrent/config/qBittorrent.conf"
    parser = configparser.RawConfigParser(delimiters=("=",), strict=False)
    parser.optionxform = str
    if path.exists():
        parser.read(path)
    complete = media_path.rstrip("/") + "/downloads/complete"
    incomplete = media_path.rstrip("/") + "/downloads/incomplete"
    active = simultaneous_downloads()
    managed = {
        "Application": {
            "FileLogger\\Enabled": "true",
            "FileLogger\\Path": str(config / "qBittorrent/logs"),
        },
        "AutoRun": {"enabled": "false"},
        "BitTorrent": {
            # Native filename filtering is applied before libtorrent starts the
            # transfer, including when a magnet's metadata first arrives.
            "ExcludedFileNamesEnabled": "true",
            "Session\\ExcludedFileNames": "*.exe, *.com, *.scr, *.msi, *.bat, *.cmd, *.ps1, *.vbs, *.js, *.lnk",
            "Session\\DefaultSavePath": complete,
            "Session\\TempPath": incomplete,
            "Session\\TempPathEnabled": "true",
            "Session\\Interface": "wg0",
            "Session\\InterfaceName": "wg0",
            "Session\\InterfaceAddress": "",
            "Session\\Port": "6881",
            "Session\\UPnP": "false",
            "Session\\DisableAutoTMMByDefault": "true",
            "Session\\QueueingSystemEnabled": "true",
            "Session\\MaxActiveDownloads": str(active),
            "Session\\MaxActiveUploads": str(active),
            "Session\\MaxActiveTorrents": str(max(active + 16, 20)),
            "Session\\IgnoreSlowTorrentsForQueueing": "true",
            "Session\\SlowTorrentsDownloadRate": "2",
            "Session\\SlowTorrentsUploadRate": "2",
            "Session\\SlowTorrentsInactivityTimer": "180",
            **qbit_seed_conf(),
        },
        "LegalNotice": {"Accepted": "true"},
        "Network": {"PortForwardingEnabled": "false"},
        "Preferences": {
            "Connection\\PortRangeMin": "6881",
            "Connection\\UPnP": "false",
            "Downloads\\SavePath": complete,
            "Downloads\\TempPathEnabled": "true",
            "Downloads\\TempPath": incomplete,
            "WebUI\\Address": "127.0.0.1",
            "WebUI\\Port": "8080",
            "WebUI\\LocalHostAuth": "false",
            "WebUI\\Username": secrets["qbit_user"],
            "WebUI\\Password_PBKDF2": '"' + secrets["qbit_pbkdf2"] + '"',
            "WebUI\\CSRFProtection": "false",
            "WebUI\\HostHeaderValidation": "false",
            "WebUI\\ClickjackingProtection": "false",
        },
    }
    for section, values in managed.items():
        if not parser.has_section(section):
            parser.add_section(section)
        for key, value in values.items():
            parser.set(section, key, str(value))
    stream = io.StringIO()
    parser.write(stream, space_around_delimiters=False)
    write(path, stream.getvalue())


if __name__ == "__main__":
    configure_engines(*sys.argv[1:])
