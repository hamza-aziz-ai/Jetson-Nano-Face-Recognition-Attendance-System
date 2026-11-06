# Remote access through a Cloudflare Tunnel

The website can be reached from the internet without opening any port on the router: `cloudflared` on the Nano connects out to Cloudflare, Cloudflare serves the site over HTTPS on a hostname, and Cloudflare Access lets only the owner through before the site's own login (D-035). Without these steps the site stays reachable only on the home network.

What is already on the device (`deploy/cloudflared-facepipe.service`):

- `cloudflared` 2026.10.7 from Cloudflare's official arm64 `.deb` (SHA-256 checked), started with `--no-autoupdate`; upgrades are deliberate.
- A systemd service that runs it as a throwaway user (`DynamicUser=yes`) and writes no logs (request URLs can carry registration invitation tokens).
- `/etc/cloudflared/facepipe-tunnel.env`, readable by root only, holding `TUNNEL_TOKEN=`. The token is never put in the repository, on a command line or in a chat.

## Setup (owner, in this order)

1. **Create the tunnel.** Cloudflare dashboard, Zero Trust, Networks, Tunnels, Create a tunnel, type Cloudflared, name for example `jetson-nano-attendance`. On the install page, copy only the token (the long string after `--token`); do not run the install command shown there.
2. **Give the token to the Nano.** On the Nano: `sudo nano /etc/cloudflared/facepipe-tunnel.env`, write `TUNNEL_TOKEN=` followed by the token, save. Then `sudo systemctl enable --now cloudflared-facepipe`. The dashboard shows the tunnel as Healthy.
3. **Protect it with Cloudflare Access before it is public.** Zero Trust, Access, Applications, Add an application, Self-hosted:
   - Application `attendance.hamzaaziz.dev`, session duration 24 hours, policy **Allow** with Include, Emails: the owner's email (sign in with a one-time code by email, or add Google as a login method).
   - A second self-hosted application for the same hostname with paths `register*` and `static/*`, policy **Bypass**, Include Everyone. Invitation links must work for people without an account; `/register` still needs a valid single-use invitation, and `/static` holds only the stylesheet, script and logo.
4. **Publish the hostname.** In the tunnel, Public Hostname, add `attendance` on `hamzaaziz.dev`, service type HTTP, URL `localhost:8000`.
5. **Check.** From a phone on mobile data: `https://attendance.hamzaaziz.dev` asks for the Cloudflare Access login, then the site's login. An invitation link opens without the Access login.

## How it fits the site

- The website sees every tunnel request coming from `127.0.0.1`. It takes the visitor's address from `CF-Connecting-IP` only on connections from this host, so the failed-login lockout and the registration rate limit count per visitor. A device on the home network cannot set that header to its advantage: its connections do not come from `127.0.0.1`.
- Form protection works through the tunnel: the browser sends `Origin: https://attendance.hamzaaziz.dev`, and cloudflared passes the same `Host`.
- Faces in the live view, snapshots and clips pass through Cloudflare (TLS ends at Cloudflare's edge) while they are viewed remotely; they are still stored only on the Nano.

## Turning it off

`sudo systemctl disable --now cloudflared-facepipe` closes remote access at once; the site keeps working at home. Deleting the public hostname or the tunnel in the dashboard does the same from anywhere.
