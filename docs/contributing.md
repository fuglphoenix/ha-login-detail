# Contributing and issues

Bug reports and pull requests are welcome in the
[issue tracker](https://github.com/fuglphoenix/ha-login-detail/issues). Please
include your Home Assistant version and the relevant `login_detail` log lines -
with usernames and IP addresses removed.

Changes are recorded in [CHANGELOG.md](../CHANGELOG.md).

The probe matrix runs a real Home Assistant against the integration.
`tools/probe_venv.sh <version>` builds a virtualenv for one HA version and
`tools/run_probe.sh <letter> <probe domain> <report file> ban|noban` runs a
single probe in it. Both keep their workspace under `HA_PROBE_ROOT`, which must
be on disk: one virtualenv is over a gigabyte, and building a second one for a
version switch fills a small `/tmp` and breaks the running probe with `ENOSPC`.
Pick a free run letter and `HA_PROBE_PORT` when probes run side by side, and
point `HA_PROBE_VENV` at the version you mean instead of moving the shared
`$HA_PROBE_ROOT/venv` symlink under a matrix that is already running.

The icon is drawn by `tools/brand.py`, which writes `assets/icon.svg` and the
PNG files in `custom_components/login_detail/brand/` from the same shapes.
Change the shapes there and run `python tools/brand.py`; the tests fail if the
files are out of date.
