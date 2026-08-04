# Deployment

Two paths. **Fly is the supported one**; `ec2/` is the original hand-provisioned
setup, kept for reference.

## Fly (recommended)

Config lives in `../fly.toml` and `../Dockerfile`. Fly builds the image remotely,
so Docker isn't needed locally.

```sh
brew install flyctl
fly auth login

cd backend
fly launch --no-deploy          # claims the app name, writes nothing we rely on
fly postgres create             # then: fly postgres attach <db-name>
./deploy/fly-secrets.sh <app>   # generates prod JWT/Fernet keys, copies the rest from .env
fly deploy
```

Notes that matter:

- **`auto_stop_machines = false` is deliberate.** APScheduler runs inside the web
  process (`app/main.py`), so a machine that stops when idle silently stops the
  30-minute sync and the daily brief, with nothing to indicate why.
- **One worker, on purpose.** A second worker would run a second scheduler and
  duplicate every sync and brief. Scaling out means moving the scheduler into its
  own process first.
- **Migrations run as `release_command`**, in a one-off machine before the new
  version takes traffic. A failed migration aborts the deploy rather than leaving
  the app pointed at a schema it doesn't match.
- **`PUBLIC_BASE_URL` must match the deployed hostname.** The OAuth callback
  derives from it, and providers compare redirect URIs as exact strings.
- **`FERNET_KEY` encrypts stored provider tokens.** Lose it and every user has to
  reconnect every account. It is not recoverable from the database.

## EC2 (`ec2/`)

The original setup: Ubuntu, system Postgres, systemd, nginx, certbot. `install.sh`
provisions it. More moving parts and a manual TLS story — only worth it if you
specifically want a box you control.
