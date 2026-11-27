# Privacy

This integration processes personal data. Be aware of what it stores and
where:

- **Usernames, user IDs, IP addresses and user agents are stored in plain
  text** - in the events, in the Home Assistant log and in
  `.storage/login_detail_history`. The storage file is bounded by the
  retention options (default 7 days and 500 entries), is readable only by the
  user Home Assistant runs as (0.12.0), and is deleted with the integration;
  the log, the recorder and backups follow their own retention.
- **The sensors show none of it by default (0.12.0).** Every user of Home
  Assistant, administrator or not, can read every entity and its history, so
  the sensors only count. Turn on **Show usernames and IP addresses in the
  sensors** and usernames, IP addresses and hostnames of logins, and the
  users, IP addresses and client names of active sessions, are in the sensor
  attributes and therefore in the recorder database - readable by everyone
  who can log in. See [Entities](entities.md).
- **Since 0.10.0, the `Host` header and the raw `X-Forwarded-For` header**
  (`host`, `x_forwarded_for_untrusted`) and whether the request came through
  Nabu Casa (`ingress`) are stored too, on every event: in the events, the
  log, `.storage/login_detail_history`, the Sign-in log panel and its
  websocket command. They are not in the sensor attributes. An
  `X-Forwarded-For` header can hold several IP addresses - the client's and
  every proxy's - and **is set by the client: it can be forged** and must not
  be taken as the client's address. Both are cut to 200 characters.
- A rejected request names a user, an OAuth client ID and the name of a
  long-lived token only when the token's signature proves it. On
  `/auth/token` (0.10.0) the event also has the grant type and the client ID
  the request asked for (a public URL), and names the user only if the
  refresh token sent exists.
- **Tokens, refresh tokens, authorization codes, passwords, TOTP codes and
  request bodies are never stored or logged**, not even at debug level. Only
  what is derived from them is kept: the result of looking the token up
  (user, token type, the name of a long-lived token) and the client ID.
- The username of a failed login is only stored if it belongs to an existing
  user, in the form Home Assistant stores it. Anything else - including a
  password typed into the username field - is recorded as `null`. (Before
  0.7.0 it was stored as typed.)
- **Unless you turn on "Also store usernames that do not exist"** (0.9.0, off
  by default). Then a username no user has is stored as typed, cut to 64
  characters, with `known_user: false`. Since 0.12.0 it is kept only in
  `.storage/login_detail_history` and shown only in the admin-only Sign-in
  log panel: the event and the log line have `username: null` and
  `known_user: false`, and no sensor shows it, whatever the options. If
  someone types their password into the username field, the password is
  stored in plain text in that file and in every backup made while it is
  there. Turning the option off removes the stored names from the file at
  once; backups follow their own retention. Up to 0.11.2 the name was also
  in the events, the log, the sensor attributes and the recorder database,
  and those follow their own retention.
- The Sign-in log panel and its websocket command are for administrators
  only. Screenshots of the panel contain all of the above; anonymise them
  before sharing.
- Each IP address is looked up with **reverse DNS** using the DNS servers Home
  Assistant uses. That means a DNS query for the IP address of whoever tried
  to log in.
- Anyone who can see the log, automations triggered by the events, or a
  backup of Home Assistant can see this data - and, with **Show usernames and
  IP addresses in the sensors** on, anyone who can log in. Notifications sent
  by automations carry it to wherever they are delivered.

If you run Home Assistant for other people, tell them, and consider how long
you keep the recorder history.

To remove the stored data, see [Removal](../README.md#removal).
