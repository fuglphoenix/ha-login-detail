#!/usr/bin/env python3
"""Cheap checks that run without Home Assistant installed.

The real test lives in tests/ and needs a running HA container. This one only
catches what plain Python can catch: syntax errors, invalid JSON, and
translations that have drifted from strings.json.

    python tools/check.py
"""

from __future__ import annotations

import json
import pathlib
import py_compile
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "custom_components" / "login_detail"

errors: list[str] = []


def fail(msg: str) -> None:
    """Record a failed check."""
    errors.append(msg)


def check_syntax() -> None:
    """Compile every Python file in the repository."""
    for path in sorted(ROOT.rglob("*.py")):
        if "__pycache__" in path.parts or ".venv" in path.parts:
            continue
        try:
            py_compile.compile(str(path), doraise=True, cfile=str(path) + "c")
        except py_compile.PyCompileError as err:
            fail(f"syntax error in {path.relative_to(ROOT)}: {err.msg.strip()}")
        finally:
            pathlib.Path(str(path) + "c").unlink(missing_ok=True)


def load_json(path: pathlib.Path) -> dict | None:
    """Read a JSON file, recording a failure if it is invalid."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        fail(f"invalid JSON in {path.relative_to(ROOT)}: {err}")
        return None


def check_manifest() -> None:
    """Check the keys and values the integration depends on."""
    manifest = load_json(COMPONENT / "manifest.json")
    if manifest is None:
        return
    for key in ("domain", "name", "version", "config_flow", "iot_class"):
        if key not in manifest:
            fail(f"manifest.json is missing the key {key!r}")
    if manifest.get("domain") != "login_detail":
        fail(
            f"manifest.json: domain is {manifest.get('domain')!r}, "
            "expected 'login_detail'"
        )
    if not manifest.get("single_config_entry"):
        fail("manifest.json: single_config_entry must be true")
    const = (COMPONENT / "const.py").read_text(encoding="utf-8")
    if f'DOMAIN = "{manifest.get("domain")}"' not in const:
        fail("DOMAIN in const.py does not match manifest.json")


def keys(obj: object, prefix: str = "") -> set[str]:
    """Flatten a translation dict into dotted keys."""
    if not isinstance(obj, dict):
        return {prefix}
    out: set[str] = set()
    for key, value in obj.items():
        out |= keys(value, f"{prefix}.{key}" if prefix else key)
    return out


def check_translations() -> None:
    """Every translation has exactly the keys in strings.json."""
    strings = load_json(COMPONENT / "strings.json")
    if strings is None:
        return
    expected = keys(strings)
    for path in sorted((COMPONENT / "translations").glob("*.json")):
        data = load_json(path)
        if data is None:
            continue
        missing = expected - keys(data)
        extra = keys(data) - expected
        name = path.relative_to(ROOT)
        if missing:
            fail(f"{name} is missing keys: {sorted(missing)}")
        if extra:
            fail(f"{name} has keys that are not in strings.json: {sorted(extra)}")


def main() -> int:
    """Run all checks and report."""
    check_syntax()
    check_manifest()
    check_translations()
    for path in sorted(ROOT.rglob("*.json")):
        if "__pycache__" not in path.parts and ".venv" not in path.parts:
            load_json(path)
    if errors:
        for err in errors:
            print(f"ERROR: {err}", file=sys.stderr)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
