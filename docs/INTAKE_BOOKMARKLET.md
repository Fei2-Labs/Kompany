# Browser Intake Bookmarklet

Send anything you see while browsing straight into Kompany (issue #23).
The bookmarklet POSTs `{url, selection, note, kind}` to the local server's
`POST /intake` endpoint:

- `kind=ops` (or `auto`, the default): the CEO classifies and dispatches it
  like any directive.
- `kind=dev`: appended to `~/.kompany/dev-queue.md` and filed as a GitHub
  issue via `gh` (best-effort), never sent to the CEO.

## Honest limits

- **Localhost only.** The server binds 127.0.0.1; the bookmarklet only works
  on the machine running Kompany. It is not a remote/mobile capture tool.
- Token-guarded: requests need `Authorization: Bearer <INTAKE_TOKEN>`
  (falls back to `MOBILE_REMOTE_TOKEN` if `INTAKE_TOKEN` is unset).
- Pages with a strict Content-Security-Policy may block `fetch` to
  localhost; use the curl example as fallback.

## Setup

1. Set a token in `~/.kompany/.env` (or your env): `INTAKE_TOKEN=<random>`,
   then restart the server.
2. Find your port: read `~/.kompany/server.json` (`"port": ...`) — the
   running server publishes it there. The daemon uses a stable port, so you
   only edit the bookmarklet once; re-edit only if the port changes.
3. Edit `PORT` and `TOKEN` in the bookmarklet below before saving it.
   (No `prompt()` — Kompany's WebView and many pages disable native dialogs;
   hardcode-edit the two constants instead.)

## Bookmarklet

Create a bookmark and paste this as the URL, after replacing `8765` and
`YOUR_TOKEN`:

```
javascript:(function(){var PORT=8765,TOKEN='YOUR_TOKEN';var n=window.getSelection?String(window.getSelection()):'';fetch('http://127.0.0.1:'+PORT+'/intake',{method:'POST',headers:{'Content-Type':'application/json','Authorization':'Bearer '+TOKEN},body:JSON.stringify({url:location.href,selection:n.slice(0,2000),note:document.title,kind:'auto'})}).then(function(r){return r.json()}).then(function(b){console.log('kompany intake:',b)}).catch(function(e){console.error('kompany intake failed:',e)});})();
```

Select text on a page first to include it as `selection`; the page title
becomes the `note`.

## curl example

```bash
curl -sS -X POST "http://127.0.0.1:$(python3 -c 'import json;print(json.load(open("'"$HOME"'/.kompany/server.json"))["port"])')/intake" \
  -H "Authorization: Bearer $INTAKE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com/post","note":"draft a reply to this","kind":"ops"}'
```

For dev work (`"kind":"dev"`) the response includes the queue path and the
GitHub issue result; `gh` absence or failure is reported in the response,
never an error.
