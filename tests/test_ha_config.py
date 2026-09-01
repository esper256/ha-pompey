#!/usr/bin/env python3
"""Validate Home Assistant app YAML the way Supervisor does.

If SCHEMA_APP_CONFIG raises, Supervisor logs ``Can't read <path>: ...`` and
skips the app. It never appears under Settings → Apps → Install app.

Schema: home-assistant/supervisor ``supervisor/apps/validate.py``
(``SCHEMA_APP_CONFIG`` / ``_SCHEMA_APP_CONFIG``), same ``timeout`` range in
5e3f4e8f (legacy ``addons/validate.py``) and current ``apps/validate.py``.
"""
from __future__ import annotations

import copy
import re
import unittest
from pathlib import Path
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parents[1]

# supervisor/apps/const.py + supervisor/const.py + supervisor/docker/const.py
RE_SLUG = re.compile(r"^[-_.A-Za-z0-9]+$")
RE_SCHEMA_ELEMENT = re.compile(
    r"^(?:"
    r"|bool"
    r"|email"
    r"|url"
    r"|port"
    r"|device(?:\((?P<filter>subsystem=[a-z]+)\))?"
    r"|str(?:\((?P<s_min>\d+)?,(?P<s_max>\d+)?\))?"
    r"|password(?:\((?P<p_min>\d+)?,(?P<p_max>\d+)?\))?"
    r"|int(?:\((?P<i_min>-?\d+)?,(?P<i_max>-?\d+)?\))?"
    r"|float(?:\((?P<f_min>-?\d*\.?\d+)?,(?P<f_max>-?\d*\.?\d+)?\))?"
    r"|match\((?P<match>.*)\)"
    r"|list\((?P<list>.+)\)"
    r")\??$"
)
RE_VOLUME = re.compile(
    r"^(data|config|ssl|local_apps|addons|backup|share|media|"
    r"homeassistant_config|all_app_configs|all_addon_configs|"
    r"app_config|addon_config)(?::(rw|ro))?$"
)
ARCH_ALL_COMPAT = {"aarch64", "amd64", "armhf", "armv7", "i386"}
STARTUP = {"initialize", "system", "services", "application", "once"}
BOOT = {"auto", "manual", "manual_only"}
STAGE = {"stable", "experimental", "deprecated"}
PRIVILEGED = {
    "BPF",
    "CHECKPOINT_RESTORE",
    "DAC_READ_SEARCH",
    "IPC_LOCK",
    "NET_ADMIN",
    "NET_RAW",
    "PERFMON",
    "SYS_ADMIN",
    "SYS_MODULE",
    "SYS_NICE",
    "SYS_PTRACE",
    "SYS_RAWIO",
    "SYS_RESOURCE",
    "SYS_TIME",
}
MAP_TYPES = {
    "data",
    "config",
    "ssl",
    "local_apps",
    "addons",
    "backup",
    "share",
    "media",
    "homeassistant_config",
    "all_app_configs",
    "all_addon_configs",
    "app_config",
    "addon_config",
}
FILE_SUFFIX_CONFIGURATION = {".yaml", ".yml", ".json"}
TIMEOUT_MIN = 10
TIMEOUT_MAX = 300
REQUIRED = ("name", "version", "slug", "description", "arch")


class ConfigInvalid(Exception):
    """Stand-in for voluptuous.Invalid — Supervisor skips the app."""


def load_yaml(path: Path):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        raise ConfigInvalid(f"{path} is empty")
    return data


def _require_dict(data, where: str) -> dict:
    if not isinstance(data, dict):
        raise ConfigInvalid(f"{where} must be a dictionary")
    return data


def _require_str(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigInvalid(f"{field} must be a non-empty string")
    return value


def _require_bool(value, field: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigInvalid(f"{field} must be a boolean")
    return value


def _valid_url(value: str, field: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigInvalid(f"{field} is not a valid URL: {value!r}")


def _migrate_map(config: dict) -> list[dict]:
    volumes: list[dict] = []
    for entry in config.get("map", []):
        if isinstance(entry, dict):
            if "type" not in entry:
                continue
            volumes.append(dict(entry))
        elif isinstance(entry, str):
            match = RE_VOLUME.match(entry)
            if not match:
                continue
            volumes.append(
                {"type": match.group(1), "read_only": match.group(2) != "rw"}
            )
        else:
            raise ConfigInvalid(f"map entry is neither a string nor a mapping: {entry!r}")
    return volumes


def _validate_schema_element(value, path: str) -> None:
    if isinstance(value, str):
        if not RE_SCHEMA_ELEMENT.match(value):
            raise ConfigInvalid(f"schema {path}: unknown type {value!r}")
        return
    if isinstance(value, list):
        for i, item in enumerate(value):
            if isinstance(item, list):
                raise ConfigInvalid(f"schema {path}: a list may not directly contain another list")
            _validate_schema_element(item, f"{path}[{i}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ConfigInvalid(f"schema {path}: keys must be strings")
            _validate_schema_element(item, f"{path}.{key}")
        return
    raise ConfigInvalid(f"schema {path}: invalid element {value!r}")


def validate_app_config(config) -> dict:
    """Faithful subset of SCHEMA_APP_CONFIG. Raises ConfigInvalid to skip."""
    config = _require_dict(config, "app config")
    if not config.get("name"):
        raise ConfigInvalid("Invalid app config!")

    out = dict(config)
    out["map"] = _migrate_map(out)

    for key in REQUIRED:
        if key not in out:
            raise ConfigInvalid(f"required key not provided @ [{key!r}]")

    _require_str(out["name"], "name")
    _require_str(str(out["version"]) if out["version"] is not None else "", "version")
    slug = _require_str(out["slug"], "slug")
    if not RE_SLUG.match(slug):
        raise ConfigInvalid(f"slug {slug!r} does not match {RE_SLUG.pattern}")
    _require_str(out["description"], "description")

    arch = out["arch"]
    if not isinstance(arch, list) or not arch:
        raise ConfigInvalid("arch must be a non-empty list")
    for item in arch:
        if item not in ARCH_ALL_COMPAT:
            raise ConfigInvalid(f"arch value {item!r} is not in {sorted(ARCH_ALL_COMPAT)}")

    if "url" in out:
        _valid_url(_require_str(out["url"], "url"), "url")

    if "startup" in out and out["startup"] not in STARTUP:
        raise ConfigInvalid(f"startup {out['startup']!r} is not one of {sorted(STARTUP)}")
    if "boot" in out and out["boot"] not in BOOT:
        raise ConfigInvalid(f"boot {out['boot']!r} is not one of {sorted(BOOT)}")
    if "stage" in out and out["stage"] not in STAGE:
        raise ConfigInvalid(f"stage {out['stage']!r} is not one of {sorted(STAGE)}")

    for flag in (
        "init",
        "ingress",
        "ingress_stream",
        "panel_admin",
        "apparmor",
        "host_network",
        "host_pid",
        "host_ipc",
        "host_uts",
        "host_dbus",
    ):
        if flag in out:
            _require_bool(out[flag], flag)

    if "ingress_port" in out:
        port = out["ingress_port"]
        if not isinstance(port, int) or not (0 <= port <= 65535):
            raise ConfigInvalid(f"ingress_port {port!r} must be 0..65535")

    if "timeout" in out:
        try:
            timeout = int(out["timeout"])
        except (TypeError, ValueError) as exc:
            raise ConfigInvalid(f"timeout {out['timeout']!r} is not an integer") from exc
        if timeout < TIMEOUT_MIN or timeout > TIMEOUT_MAX:
            raise ConfigInvalid(
                f"timeout {timeout} is outside Supervisor Range(min={TIMEOUT_MIN}, "
                f"max={TIMEOUT_MAX}). This skips the app in the store."
            )

    if "privileged" in out:
        if not isinstance(out["privileged"], list):
            raise ConfigInvalid("privileged must be a list")
        for cap in out["privileged"]:
            if cap not in PRIVILEGED:
                raise ConfigInvalid(f"privileged {cap!r} is not allowed")

    if "devices" in out:
        if not isinstance(out["devices"], list) or not all(
            isinstance(item, str) and item.startswith("/") for item in out["devices"]
        ):
            raise ConfigInvalid("devices must be a list of host paths")

    if "panel_icon" in out:
        _require_str(out["panel_icon"], "panel_icon")
    if "panel_title" in out:
        _require_str(out["panel_title"], "panel_title")

    for volume in out["map"]:
        volume_type = volume.get("type")
        if volume_type not in MAP_TYPES:
            raise ConfigInvalid(f"map type {volume_type!r} is not a MappingType")
        if "read_only" in volume and not isinstance(volume["read_only"], bool):
            raise ConfigInvalid(f"map read_only for {volume_type} must be a boolean")
        if "path" in volume:
            path = volume["path"]
            if not isinstance(path, str) or not path or path == "/":
                raise ConfigInvalid(f"map path for {volume_type} is invalid")

    if "options" in out and not isinstance(out["options"], dict):
        raise ConfigInvalid("options must be a dictionary")
    if "schema" in out:
        schema = out["schema"]
        if schema is not False:
            schema = _require_dict(schema, "schema")
            for key, value in schema.items():
                _validate_schema_element(value, key)

    return out


def validate_repository_config(data) -> dict:
    data = _require_dict(data, "repository.yaml")
    _require_str(data.get("name"), "name")
    if "url" in data:
        _valid_url(_require_str(data["url"], "url"), "url")
    if "maintainer" in data:
        _require_str(data["maintainer"], "maintainer")
    return data


def validate_translations(data, schema_keys: set[str]) -> dict:
    data = _require_dict(data, "translations")
    configuration = data.get("configuration", {})
    if not isinstance(configuration, dict):
        raise ConfigInvalid("translations configuration must be a dictionary")
    for key, entry in configuration.items():
        entry = _require_dict(entry, f"translations.configuration.{key}")
        if "name" not in entry or not isinstance(entry["name"], str):
            raise ConfigInvalid(f"translations.configuration.{key} needs name")
        if key not in schema_keys:
            raise ConfigInvalid(
                f"translations.configuration.{key} has no matching schema key"
            )
    return data


def find_app_configs(root: Path) -> list[Path]:
    """Same glob rules as Supervisor StoreData._find_app_configs."""
    found: list[Path] = []
    for path in root.glob("**/config.*"):
        if path.suffix not in FILE_SUFFIX_CONFIGURATION:
            continue
        parts = path.relative_to(root).parts
        if any(part.startswith(".") or part == "rootfs" for part in parts):
            continue
        found.append(path)
    return sorted(found)


class SupervisorConfigSchema(unittest.TestCase):
    def setUp(self):
        self.config_path = ROOT / "pompey/config.yaml"
        self.raw = load_yaml(self.config_path)

    def test_household_options_only(self):
        self.assertEqual(
            set(self.raw["options"]),
            {
                "media_folder",
                "movies_folder",
                "movies_kid_folder",
                "tv_folder",
                "tv_kid_folder",
                "after_download",
            },
        )
        self.assertEqual(set(self.raw["schema"]), set(self.raw["options"]))
        self.assertEqual(self.raw["options"]["after_download"], "stop_sharing")
        self.assertEqual(
            self.raw["schema"]["after_download"],
            "list(stop_sharing|share_to_ratio|share_one_day)",
        )
        self.assertNotIn("preferred_language", self.raw["options"])
        self.assertNotIn("anime_audio", self.raw["options"])
        self.assertNotIn("subtitles", self.raw["options"])
        for leaked in (
            "wireguard_config",
            "wireguard_private_key",
            "wireguard_address",
            "wireguard_peer_public_key",
            "wireguard_endpoint",
            "wireguard_dns",
            "lan_networks",
            "port_forwarding",
            "media_root",
            "log_level",
            "plex_url",
            "plex_token",
            "source_url",
            "source_key",
        ):
            self.assertNotIn(leaked, self.raw["schema"], leaked)
            self.assertNotIn(leaked, self.raw["options"], leaked)
        validated = validate_app_config(self.raw)
        self.assertEqual(validated["slug"], "pompey")
        self.assertIn("aarch64", validated["arch"])
        self.assertIn("amd64", validated["arch"])

    def test_timeout_1800_is_why_the_store_skips_the_app(self):
        broken = copy.deepcopy(self.raw)
        broken["timeout"] = 1800
        with self.assertRaisesRegex(ConfigInvalid, r"timeout 1800.*max=300"):
            validate_app_config(broken)

    def test_timeout_is_within_supervisor_range(self):
        timeout = int(self.raw["timeout"])
        self.assertGreaterEqual(timeout, TIMEOUT_MIN)
        self.assertLessEqual(timeout, TIMEOUT_MAX)

    def test_timeout_below_minimum_is_rejected(self):
        broken = copy.deepcopy(self.raw)
        broken["timeout"] = 9
        with self.assertRaisesRegex(ConfigInvalid, r"timeout 9"):
            validate_app_config(broken)

    def test_invalid_slug_is_rejected(self):
        broken = copy.deepcopy(self.raw)
        broken["slug"] = "Pompey App"
        with self.assertRaisesRegex(ConfigInvalid, "slug"):
            validate_app_config(broken)

    def test_unknown_schema_type_is_rejected(self):
        broken = copy.deepcopy(self.raw)
        broken["schema"] = dict(broken.get("schema") or {})
        broken["schema"]["wireguard_config"] = "string"
        with self.assertRaisesRegex(ConfigInvalid, "unknown type"):
            validate_app_config(broken)

    def test_unknown_map_type_is_rejected(self):
        broken = copy.deepcopy(self.raw)
        broken["map"] = [{"type": "downloads", "read_only": False}]
        with self.assertRaisesRegex(ConfigInvalid, "map type"):
            validate_app_config(broken)

    def test_missing_name_is_rejected(self):
        broken = copy.deepcopy(self.raw)
        del broken["name"]
        with self.assertRaises(ConfigInvalid):
            validate_app_config(broken)

    def test_only_one_app_config_file(self):
        found = find_app_configs(ROOT)
        self.assertEqual(found, [self.config_path])

    def test_repository_yaml(self):
        repo = load_yaml(ROOT / "repository.yaml")
        validate_repository_config(repo)
        self.assertEqual(repo["url"], "https://github.com/esper256/ha-pompey")

    def test_translations_match_schema(self):
        trans = load_yaml(ROOT / "pompey/translations/en.yaml")
        validate_translations(trans, set(self.raw["schema"]))
        self.assertEqual(set(trans["configuration"]), set(self.raw["schema"]))

    def test_schema_types_match_supervisor_regex(self):
        for key, value in self.raw["schema"].items():
            self.assertIsInstance(value, str, key)
            self.assertRegex(value, RE_SCHEMA_ELEMENT, key)

    def test_options_keys_are_in_schema(self):
        extra = set(self.raw["options"]) - set(self.raw["schema"])
        self.assertFalse(extra, f"options keys missing from schema: {extra}")


if __name__ == "__main__":
    unittest.main()
