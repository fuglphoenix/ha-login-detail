# Contributing and issues

Bug reports and pull requests are welcome in the
[issue tracker](https://github.com/fuglphoenix/ha-login-detail/issues). Please
include your Home Assistant version and the relevant `login_detail` log lines -
with usernames and IP addresses removed.

Changes are recorded in [CHANGELOG.md](../CHANGELOG.md).

The icon is drawn by `tools/brand.py`, which writes `assets/icon.svg` and the
PNG files in `custom_components/login_detail/brand/` from the same shapes.
Change the shapes there and run `python tools/brand.py`; the tests fail if the
files are out of date.
