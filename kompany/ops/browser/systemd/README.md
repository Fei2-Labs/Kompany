# Systemd unit templates

These are **verbatim copies** of the unit files actually installed at
`/etc/systemd/system/` on `kosonen-server` (confirmed via `systemctl cat`
at migration time, both `xvfb.service` and `kompany-browser@linkedin.service`
were `enabled`+`active`).

An earlier draft of these files (in the now-deprecated standalone
`Fei2-Labs/kompany-browser` repo) was *reconstructed* from indirect evidence
(script comments, sibling unit forensics) rather than copied from the live
server, and had real drift from what's actually running: missing the
`OnFailure=kompany-alert@browser-%i.service` alert hook, wrong
`Requires`/`After` ordering, wrong `RestartSec`, and an `ExecStop` /
`TimeoutStopSec` pair that isn't actually installed. These files replace
that draft with the verified live copies.

**Path is user/host-specific.** `ExecStart=/home/kosonen/kompany-browser/...`
is a literal absolute path (systemd units can't expand `~`/`$HOME`). When
installing on a different machine/user, substitute the actual home
directory before copying into `/etc/systemd/system/`.

## Install

```bash
sudo cp systemd/*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now xvfb.service
sudo systemctl enable --now kompany-browser@linkedin.service
# kompany-browser@x and kompany-browser@weibo stay disabled until those
# integrations go active (see config/x.env, config/weibo.env).
```

## Files

- `xvfb.service` — the shared `:99` X virtual framebuffer every headed
  browser instance draws to.
- `kompany-browser@.service` — one instance per integration (`%i` = the
  config name under `config/<name>.env`, e.g. `linkedin`, `x`, `weibo`).
  Requires `xvfb.service`; fires `kompany-alert@browser-%i.service` on
  failure.
- `kompany-alert@.service` — generic `OnFailure=` hook that POSTs a panel
  alert via `send-alert.sh` so a crashed integration surfaces in the
  Kompany board UI instead of silently dying.
