# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.5.0] - 2026-10-14

No change in behaviour. Events, event fields, entity IDs and `unique_id`s are
unchanged.

### Changed
- All documentation, code comments and log messages are now in English. If you
  filter the log on the old message texts, update the filter; the events are
  unchanged.
- `manifest.json` now points at this repository for documentation and issues,
  and lists a code owner.
- The acceptance test probes are renamed to `login_detail_probe` and
  `login_detail_probe2`.

### Added
- MIT licence.
- `hacs.json`, so the integration can be installed as a HACS custom repository.
- hassfest and HACS validation in CI.
- Static checks with `pytest` and `ruff` in CI.

## [0.4.0] - 2026-10-12

### Added
- Setup from the UI: **Settings → Devices & services → Add integration →
  Login Detail**. `config_flow.py`, `strings.json` and Danish and English
  translations.
- `single_config_entry: true` in the manifest, plus a check in
  `async_step_user`.

### Changed
- `login_detail:` in `configuration.yaml` is no longer needed. An existing
  line is imported into a config entry automatically (`async_step_import`).
- `unique_id`s are unchanged, so the entity registry adopts the existing
  entities. Entity IDs, names and recorder history carry over.

### Fixed
- Removing or reloading the integration from the UI now takes the monkeypatch
  of core's three login methods off again, without a restart. In 0.3.0 it
  stayed in place until the next restart.
- The history could lose its newest entries. `Store.async_delay_save` waits
  10 seconds, and on a reload nothing forced the last write through - Home
  Assistant only does that on a restart. The bug also existed in 0.2.0 and
  0.3.0, but only became visible once the integration could be reloaded
  without a restart. Fixed with `async_flush()` in `async_unload_entry`.

## [0.3.0] - 2026-10-12

### Security
- `ok` was reported before 2FA had been entered. 0.2.0 hooked
  `LoginFlow.async_finish`, which core already calls once the **password** has
  been accepted; the interruption for 2FA happens one layer up, in
  `AuthManagerFlowManager.async_finish_flow`. As a result:
  1. One login was counted as two.
  2. **An attempt with the correct password where 2FA was never entered was
     recorded as `ok`.** Someone with the right password who did not get past
     2FA showed up as a completed login.

  Success is now reported from `async_finish_flow`, the only place where the
  final outcome is known. The intermediate state is reported as `mfa_pending`
  and replaced by `ok` when the same flow completes. If 2FA is never
  submitted, `mfa_pending` remains - and that is exactly the attempt worth
  seeing.

  The bug only affected users with 2FA enabled. Running the 0.3.0 acceptance
  test against 0.2.0 fails on exactly the five checks the bug is about.

## [0.2.0] - 2026-10-12

### Added
- Sensors (`sensor.py`, `const.py`): six entities.
- `login_detail_succeeded` event. 0.1.0 only saw failures and could not answer
  "who actually got in".
- History in `.storage/login_detail_history`, so it survives a restart.
- Reverse DNS lookup for each entry, in a background task.

### Known issues
- Reports `ok` before 2FA has been entered. Fixed in 0.3.0.

## [0.1.0] - 2026-10-12

### Added
- First version. Monkeypatches `HassLoginFlow.async_step_init` and
  `LoginFlow.async_step_mfa` and fires `login_detail_failed` with `username`,
  `ip_address`, `reason` and `flow_id`. Set up with `login_detail:` in
  `configuration.yaml`.
- Closes the gap that Home Assistant does not log *which* username was used in
  a failed login attempt
  ([home-assistant/core#148110](https://github.com/home-assistant/core/issues/148110)).

[Unreleased]: https://github.com/fuglphoenix/ha-login-detail/compare/v0.5.0...HEAD
[0.5.0]: https://github.com/fuglphoenix/ha-login-detail/releases/tag/v0.5.0
