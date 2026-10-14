# Privacy

This integration processes personal data. Be aware of what it stores and
where:

- **Usernames, user IDs, IP addresses and user agents are stored in plain
  text** - in the events, in the Home Assistant log, in
  `.storage/login_detail_history`, in the sensor attributes and therefore in
  the recorder database. The storage file is bounded by the retention options
  (default 7 days and 500 entries); the log, the recorder and backups follow
  their own retention.
- A rejected request names a user, an OAuth client ID and the name of a
  long-lived token only when the token's signature proves it. Tokens
  themselves are never stored.
- The username of a failed login is only stored if it belongs to an existing
  user, in the form Home Assistant stores it. Anything else - including a
  password typed into the username field - is recorded as `null`. (Before
  0.7.0 it was stored as typed.)
- The Sign-in log panel and its websocket command are for administrators
  only. Screenshots of the panel contain all of the above; anonymise them
  before sharing.
- Each IP address is looked up with **reverse DNS** using the DNS servers Home
  Assistant uses. That means a DNS query for the IP address of whoever tried
  to log in.
- Anyone who can see the entities, the log, automations triggered by the
  events, or a backup of Home Assistant can see this data. Notifications sent
  by automations carry it to wherever they are delivered.

If you run Home Assistant for other people, tell them, and consider how long
you keep the recorder history.

To remove the stored data, see [Removal](../README.md#removal).
