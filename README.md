# embytulli

A lightweight, self-hosted notifier that watches your Emby server and sends
rich Discord notifications when playback starts/stops, new media is added,
and more -- basically the Discord-notification half of Tautulli, built for
Emby instead of Plex.

Every event is also logged to a local SQLite database (`embytulli.db`), so
if you later want a Tautulli-style analytics dashboard (top users, most
watched titles, watch-time graphs), the history is already there.

## How it works

1. Emby has a built-in **Webhooks** feature. You point it at this app.
2. When something happens (playback starts, stops, etc.), Emby POSTs a JSON
   payload to this app.
3. This app parses that payload, builds a Discord embed (poster thumbnail,
   user, player/device, video/audio quality, progress bar, summary), and
   posts it to a Discord webhook.
4. The event is logged to SQLite either way.

```
Emby (LXC)  --webhook POST-->  embytulli (LXC/VM)  --webhook POST-->  Discord
```

## 1. Prerequisites

- A Proxmox LXC (or VM) separate from Emby, or the Emby LXC itself if you'd
  rather not spin up a new container. Debian/Ubuntu assumed below.
- Python 3.10+
- Network access from that container to your Emby server, and outbound
  internet access to `discord.com`.
- An Emby API key: Emby dashboard -> **Advanced** -> **API Keys** -> **New API Key**.
- A Discord webhook URL: in Discord, go to the target channel -> **Edit
  Channel** -> **Integrations** -> **Webhooks** -> **New Webhook** -> **Copy
  Webhook URL**.

## 2. Install

If you're putting this in its own LXC, create an unprivileged Debian 12 LXC
in Proxmox first, then inside it:

```bash
apt update && apt install -y python3 python3-venv python3-pip git

useradd -r -s /usr/sbin/nologin -d /opt/embytulli embytulli
mkdir -p /opt/embytulli
# copy this project's files into /opt/embytulli (scp, git clone, rsync, etc.)

cd /opt/embytulli
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

## 3. Configure

```bash
cp config.example.yaml config.yaml
nano config.yaml
```

Fill in at minimum:

- `emby.base_url` -- Emby's LAN address, e.g. `http://192.168.1.50:8096`
- `emby.api_key` -- the API key you created above
- `discord.webhook_url` -- the Discord webhook URL you copied

Everything else in `config.yaml` has sensible defaults and is documented
inline (which events to notify on, poster/summary/progress-bar toggles,
users/libraries to ignore).

Validate the config parses correctly:

```bash
./venv/bin/python config.py
```

## 4. Run it

### Quick test (foreground)

```bash
./venv/bin/python app.py
```

### Production (systemd)

```bash
chown -R embytulli:embytulli /opt/embytulli
cp embytulli.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now embytulli
systemctl status embytulli
```

By default it listens on `0.0.0.0:8087`. Adjust `server.port` in
`config.yaml` (and the `ExecStart` line in `embytulli.service`) if that
port's taken.

## 5. Point Emby at it

In Emby's dashboard: **Notifications** -> **Webhooks** (this is a built-in
Emby Premiere feature; if you don't see it, it may need to be enabled
under Plugins).

1. Click **Add Webhook**.
2. **Webhook Url**: `http://<embytulli-ip>:8087/webhook/emby`
   - If you set `server.shared_secret` in `config.yaml`, append
     `?secret=YOUR_SECRET` to this URL.
3. **Request Content Type**: `application/json`.
4. Under **Events**, check the ones you want: at minimum *Playback Start*
   and *Playback Stop*. Check *New Media Added* too if you want library
   notifications.
5. Save.

Emby's webhook payload format has varied slightly across versions/plugin
builds. `event_parser.py` in this app is written defensively -- it checks
several possible field names/locations for each piece of data -- so it
should handle the common shapes without configuration. If you find a field
showing up blank in Discord, enable `DEBUG` logging (`logging.level: DEBUG`
in `config.yaml`) and check `journalctl -u embytulli -f` while triggering
the event; the raw payload will help pinpoint what to add to
`event_parser.py`.

## 6. Test without waiting for real playback

With the app running:

```bash
curl -X POST http://localhost:8087/test
```

This fires a synthetic "now playing" event through the full pipeline (embed
building + Discord send) so you can confirm formatting looks right.

## Notification appearance

Each event gets a color-coded embed:

| Event | Color | Shown fields |
|---|---|---|
| Playback start | green | User, Player, Quality, Runtime, Library, Summary |
| Playback pause | orange | User, Player, Quality, Progress |
| Playback resume | blue | User, Player, Quality |
| Playback stop | red | User, Player, Quality, Progress |
| New media added | purple | Quality, Runtime, Library, Summary |
| Marked played/unplayed | grey | User |

Posters are fetched from Emby's API and attached directly to the Discord
message (not hot-linked), so they show up even if Emby isn't reachable
from the internet.

## What's next

This first version focuses on notifications, matching Tautulli's most-used
feature. The event log in `embytulli.db` (table `events`) is designed so a
follow-up phase can add:

- A web dashboard (watch history, top users/titles, concurrent streams)
- Graphs (plays per day, bandwidth/quality breakdown)
- A "currently watching" live view

Ask for that phase whenever you're ready -- the data collection is already
running.

## Troubleshooting

- **No message arrives in Discord**: check `journalctl -u embytulli -f`
  for errors; confirm the webhook URL in `config.yaml` is correct and the
  container has outbound internet access.
- **Emby can't reach the app**: confirm the LXC's firewall/Proxmox
  firewall allows the port, and that `server.host` is `0.0.0.0` (not
  `127.0.0.1`) if Emby is in a different container.
- **Poster missing**: confirm `emby.api_key` is valid and `emby.base_url`
  is reachable from the embytulli container (not just from your browser).
