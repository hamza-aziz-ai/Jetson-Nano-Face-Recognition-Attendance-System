# Remote access through a Cloudflare Tunnel

The website can be reached from the internet without opening any port on the router: `cloudflared` on the Nano connects out to Cloudflare, and Cloudflare serves the site over HTTPS on a hostname (D-035). The site's own sign-in page (D-036) is the only lock; Cloudflare Access was in front until 2026-11-07 and was removed at the owner's request so that people reach the site directly. Without these steps the site stays reachable only on the home network.

What is already on the device (`deploy/cloudflared-facepipe.service`):

- `cloudflared` 2026.10.7 from Cloudflare's official arm64 `.deb` (SHA-256 checked), started with `--no-autoupdate`; upgrades are deliberate.
- A systemd service that runs it as a throwaway user (`DynamicUser=yes`) and writes no logs (request URLs can carry registration invitation tokens).
- `/etc/cloudflared/facepipe-tunnel.env`, readable by root only, holding `TUNNEL_TOKEN=`. The token is never put in the repository, on a command line or in a chat.

## Setup (owner, in this order)

1. **Create the tunnel.** Cloudflare dashboard, Networking, Tunnels, Create a tunnel, type Cloudflared. On the install page, copy only the token (the long string after `--token`); do not run the install command shown there.
2. **Give the token to the Nano.** On the Nano: `sudo nano /etc/cloudflared/facepipe-tunnel.env`, write `TUNNEL_TOKEN=` followed by the token, save. Then `sudo systemctl enable --now cloudflared-facepipe`. The dashboard shows the tunnel as Healthy.
3. **Set a strong website password first.** The sign-in page faces the internet: `python3 web.py --set-password <user>` with a long password. After 10 failed attempts in 15 minutes a visitor is refused.
4. **Publish the hostname.** In the tunnel, Routes, Add route, Published application: `attendance` on `hamzaaziz.dev`, service URL `http://localhost:8000`.
5. **Maintenance page (optional).** Once: `npx wrangler login`. Then `sh deploy/maintenance/deploy.sh attendance.hamzaaziz.dev`. It publishes the Worker `facepipe-maintenance` on the page addresses (D-037).
6. **Check.** From a phone on mobile data: `https://attendance.hamzaaziz.dev` shows the sign-in page; an invitation link opens the registration form.

## Maintenance page (D-037)

When the Nano cannot answer (tunnel down: Cloudflare error 1033 / HTTP 530; cloudflared up but the website down: 502; connection errors 504 and 520-529), the Worker in `deploy/maintenance/worker.js` shows a "Down for maintenance" page in the site's look with HTTP 503 and `Retry-After: 30`, and the page reloads every 30 seconds until the site answers again. Any answer from the website itself, its own 503s included, passes through unchanged.

It runs only on the page addresses (`/`, `/live`, `/attendance*`, `/people*`, `/cameras*`, `/system*`, `/login*`, `/register*`), so the status polls, files and clips never count against the Workers free plan (100,000 requests a day). Remove it with `npx wrangler delete --name facepipe-maintenance`.

## How it fits the site

- The website sees every tunnel request coming from `127.0.0.1`. It takes the visitor's address from `CF-Connecting-IP` only on connections from this host, so the failed sign-in lockout and the registration rate limit count per visitor. A device on the home network cannot set that header to its advantage: its connections do not come from `127.0.0.1`.
- HTTPS: the session cookie gets `Secure`, and forms must come from `https://<hostname>`, only when `X-Forwarded-Proto: https` arrives from this host (cloudflared).
- Faces in the live view, snapshots and clips pass through Cloudflare (TLS ends at Cloudflare's edge) while they are viewed remotely; they are still stored only on the Nano.

## Turning it off

`sudo systemctl disable --now cloudflared-facepipe` closes remote access at once; the site keeps working at home. Deleting the public hostname or the tunnel in the dashboard does the same from anywhere.
