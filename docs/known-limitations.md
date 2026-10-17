# Known limitations

- **Private core API.** See the warning in the [README](../README.md) and
  [After a Home Assistant upgrade](upgrading.md#after-a-home-assistant-upgrade).
  This is the main limitation of the integration.
- **`username` on `invalid_mfa_code` may be the display name.** After the
  password step the username is taken from the flow's credential. If core has
  not set one, the user's display name is used instead of the login name.
- **`ip_address` is only as accurate as Home Assistant's HTTP setup.** It is
  the address Home Assistant sees, as resolved by `use_x_forwarded_for` and
  `trusted_proxies`. Behind a reverse proxy that is not trusted, or behind
  source NAT (including hairpin NAT for local clients), several clients can
  show up with the same address.
- **`x_forwarded_for_untrusted` is whatever the client sent.** Anyone can
  put any address in an `X-Forwarded-For` header. Use it to debug a proxy,
  never as the client's address; `ip_address` is the one Home Assistant
  resolved. `host` is set by the client too.
- **`ingress: nabu_casa` is untested against a real Home Assistant Cloud
  connection.** It uses core's own `is_cloud_connection`, which reads the
  flag the Cloud integration sets for remote connections. The acceptance
  test runs without Cloud and covers `direct` only.
- **An authorization code grant on `/auth/token` has one reason for several
  causes.** Core uses the code up when it checks it, so afterwards an
  unknown, expired or reused code, a wrong PKCE verifier and a user who is not
  allowed all show as `invalid_authorization_code`.
- **Rejected requests on ordinary views need IP banning enabled.** A 401 on
  `/api/mcp`, `/api/states` and other REST views only reaches core's
  `process_wrong_login` through the IP ban middleware, which Home Assistant
  only installs when `ip_ban_enabled` is true. With IP banning disabled, core
  does not log these requests and `login_detail_request_rejected` is not fired
  for them. The login flow, `/auth/token` and `/api/websocket` call core
  directly and are reported either way.
- **Calls without a token count towards the IP ban.** Core counts every
  rejected request against the IP address - also an MCP client's first call
  without a token, its calls with an expired token, and `not_admin`. With
  `login_attempts_threshold` set, enough of them ban the client's IP address
  for good (`ip_bans.yaml`); the counter is only reset by a successful login
  or websocket authentication *from the same address*, which never happens
  for a cloud MCP client, whose calls come from the provider's
  servers. Behind a reverse proxy that is not trusted, the proxy's address
  is banned - and with it everyone. This is core's behaviour; Login Detail
  only reports it. Without `login_attempts_threshold` (the default), nothing
  is counted or banned.
- **`not_admin` on websocket commands is not reported.** A websocket command
  that requires admin answers with an error message and does not call core's
  `process_wrong_login`, so there is nothing to capture without patching core.
  `not_admin` covers REST views such as `/api/mcp`.
- **A strict `logger.default` silences the log lines.** With for example
  `logger: default: error`, nothing is written to the log - the events are
  still fired. To keep the log lines, add:

  ```yaml
  logger:
    default: error
    logs:
      custom_components.login_detail: info
  ```

  `info` rather than `warning`, because it also gives you the startup line
  the post-upgrade check relies on.
- **The attribute keys changed in 0.8.0** from Danish to English, with no
  compatibility aliases. Update templates and cards that read attributes, see
  [Attribute keys](upgrading.md#attribute-keys).
- **Installations from before 0.8.0 keep their Danish entity IDs** (for
  example `sensor.login_mislykkede_24t`), see
  [Which entity IDs you get](upgrading.md#which-entity-ids-you-get).
- **The unit of the IP address sensor changed in 0.8.0** from `adresser` to
  `addresses`, which needs a one-time fix under **Developer tools →
  Statistics** on an existing installation, see
  [Unit of the IP address sensor](upgrading.md#unit-of-the-ip-address-sensor).
- **The config flow imports `probatio`**, the schema library shipped with Home
  Assistant 2026.10. If your Home Assistant version ships it under another
  name, the import fails.
- **HACS shows a generic icon.** The icon ships in
  `custom_components/login_detail/brand/`, which Home Assistant (2026.3 and
  newer) shows under **Settings → Devices & services**. The HACS dashboard does
  not read that folder yet ([hacs/integration#5171]); the integration is not in
  `home-assistant/brands`.

[hacs/integration#5171]: https://github.com/hacs/integration/issues/5171
