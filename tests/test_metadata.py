"""Checks that do not need Home Assistant installed.

The integration modules import homeassistant, which is not a test dependency
here, so these tests verify the parts that break silently or embarrass in
public: manifest and HACS metadata, translation parity, that every module
compiles, and that no Danish text has crept back in outside the Danish
translation.

The login path itself is covered by the acceptance test, see tests/README.md.
"""

from __future__ import annotations

import json
from pathlib import Path
import py_compile
import re
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "login_detail"
REPO_URL = "https://github.com/fuglphoenix/ha-login-detail"

# The entity IDs that installations from before 0.8.0 keep (see "Entities" in
# README.md), and the attribute keys before 0.8.0, which the README and the
# CHANGELOG list next to the new ones. They may appear anywhere; nothing else
# Danish may.
LEGACY_ENTITY_IDS = [
    "login_seneste_haendelse",
    "login_mislykkede_24t",
    "login_ip_adresser_24t",
    "login_historik",
    "login_aktive_sessioner",
]
LEGACY_ATTRIBUTE_KEYS = [
    "hændelser",
    "sessioner",
    "adresser",
    "ip_fordeling",
    "brugernavne",
    "brugere",
    "bruger",
    "seneste",
    "tidspunkt",
    "resultat",
    "vaert",
    "metode",
    "mislykkede",
    "sidst_brugt",
    "klient",
    "ukendt",
]
COMPAT_TERMS = LEGACY_ENTITY_IDS + LEGACY_ATTRIBUTE_KEYS

DANISH = re.compile(
    r"[æøåÆØÅ]"
    r"|\b(ikke|og|skal|eller|hvis|naar|foer|ogsaa|paa|bliver|kun|hvor|"
    r"der|det|til|som|med|af|fra|efter|indtil)\b",
    re.IGNORECASE,
)

TEXT_SUFFIXES = {".py", ".json", ".md", ".yaml", ".yml", ".toml", ""}


def load_json(path: Path) -> dict:
    """Read a JSON file from the repository."""
    return json.loads(path.read_text(encoding="utf-8"))


def tracked_files() -> list[Path]:
    """Files git tracks or would track, falling back to a directory walk."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        return [ROOT / line for line in out.splitlines() if (ROOT / line).is_file()]
    except (OSError, subprocess.CalledProcessError):
        return [
            p
            for p in ROOT.rglob("*")
            if p.is_file() and ".git" not in p.parts and ".venv" not in p.parts
        ]


def test_manifest_is_valid() -> None:
    """The manifest has what hassfest and HACS require of a custom component."""
    manifest = load_json(COMPONENT / "manifest.json")
    for key in ("domain", "name", "version", "documentation", "codeowners"):
        assert manifest[key], f"manifest is missing {key}"
    assert manifest["domain"] == "login_detail"
    assert manifest["config_flow"] is True
    assert manifest["single_config_entry"] is True
    assert manifest["documentation"] == REPO_URL
    assert manifest["issue_tracker"] == f"{REPO_URL}/issues"
    assert manifest["codeowners"] == ["@fuglphoenix"]


def test_manifest_keys_are_sorted() -> None:
    """Hassfest requires domain, name, then the remaining keys sorted."""
    keys = list(load_json(COMPONENT / "manifest.json"))
    assert keys[:2] == ["domain", "name"]
    assert keys[2:] == sorted(keys[2:])


def test_versions_agree() -> None:
    """The manifest version has a CHANGELOG entry."""
    version = load_json(COMPONENT / "manifest.json")["version"]
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{version}]" in changelog


def test_hacs_json() -> None:
    """hacs.json has the keys HACS needs for a custom repository."""
    hacs = load_json(ROOT / "hacs.json")
    assert hacs["name"] == "Login Detail"
    assert hacs["render_readme"] is True
    assert re.fullmatch(r"\d{4}\.\d{1,2}\.\d+", hacs["homeassistant"])


def test_every_module_compiles() -> None:
    """Catch syntax errors without importing Home Assistant."""
    for path in [*COMPONENT.rglob("*.py"), *(ROOT / "tests").rglob("*.py")]:
        py_compile.compile(str(path), doraise=True, cfile=str(path) + "c")
        Path(str(path) + "c").unlink(missing_ok=True)


def collect_keys(node: object, prefix: str = "") -> set[str]:
    """Flatten a translation dict into dotted keys."""
    keys: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            keys |= collect_keys(value, f"{prefix}.{key}" if prefix else key)
    else:
        keys.add(prefix)
    return keys


@pytest.mark.parametrize("language", ["en", "da"])
def test_translations_match_strings(language: str) -> None:
    """Every string has a translation in every shipped language."""
    strings = collect_keys(load_json(COMPONENT / "strings.json"))
    translation = collect_keys(
        load_json(COMPONENT / "translations" / f"{language}.json")
    )
    assert translation == strings


def test_english_translation_is_strings_json() -> None:
    """English is the source language: en.json mirrors strings.json."""
    assert load_json(COMPONENT / "translations" / "en.json") == load_json(
        COMPONENT / "strings.json"
    )


def test_no_danish_outside_danish_translation() -> None:
    """All text is English, except translations/da.json and compat names."""
    allowed = {COMPONENT / "translations" / "da.json", Path(__file__).resolve()}
    findings = []
    for path in tracked_files():
        if path.resolve() in allowed or path.suffix not in TEXT_SUFFIXES:
            continue
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            for term in COMPAT_TERMS:
                line = line.replace(term, "")
            if match := DANISH.search(line):
                rel = path.relative_to(ROOT)
                findings.append(f"{rel}:{number}: {match.group(0)!r}: {line.strip()}")
    assert not findings, "\n".join(findings)


def test_no_legacy_attribute_keys_in_code() -> None:
    """The attribute keys from before 0.8.0 are only named in the docs."""
    findings = []
    for path in COMPONENT.rglob("*"):
        if path.suffix not in {".py", ".js"}:
            continue
        text = path.read_text(encoding="utf-8")
        for key in LEGACY_ATTRIBUTE_KEYS:
            if re.search(rf"[\"']{re.escape(key)}[\"']", text):
                findings.append(f"{path.relative_to(ROOT)}: {key!r}")
    assert not findings, "\n".join(findings)
