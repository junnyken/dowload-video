# bgutil-pot

PO token provider for yt-dlp, deployed as its own Vibe Host project.

## Why it is separate

`docker-compose.yml` runs this as a service named `bgutil-pot`, and the backend
defaults `BGUTIL_POT_URL` to `http://bgutil-pot:4416`. Production does not run
that compose file — backend and frontend are separate Vibe Host projects — so
that hostname never resolved and no PO token was ever issued. YouTube
extraction still worked, but every download of the resulting URL was refused
with 403.

## Wiring

Deploy this directory as its own project, then set on the **backend**:

    BGUTIL_POT_URL=https://<this-project>.<host>

and redeploy the backend so the plugin picks it up.

## Access

The provider has no authentication of its own. It is only useful to something
that can already extract from YouTube, but it does spend this server's
resources, so restrict it to the backend's egress address rather than leaving
it open to the internet.
