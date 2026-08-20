# `src/pages/Admin/` — orphaned panels

Nothing in this folder is rendered.

These panels were only ever mounted by `AdminDashboard.jsx`, which was reached
through `App.jsx`'s `view === 'admin'` branch. That branch had become
unreachable: `main.jsx` routes every `/vid-admin*` path to the control plane in
`src/admin/` before `App.jsx` runs, and the in-app "Admin" button that could
still have set the view was itself gated on an `adminAuth` flag only the old
login could set — a closed loop with no entry point. `AdminDashboard.jsx` and
`components/AdminLogin.jsx` have been deleted; these are what they left behind.

They are kept rather than deleted because four of them have **no equivalent in
the new admin shell**, while their backend endpoints still exist:

| Panel                        | Backend                            | Ported? |
|------------------------------|------------------------------------|---------|
| `PlaybooksPanel`             | `/intelligence/playbooks`          | **yes** — `admin/pages/PlaybooksPage.tsx` |
| `AutomationHistoryPanel`     | `/intelligence/automation-history` | **yes** — `admin/pages/AutomationHistoryPage.tsx` |
| `YouTubeGatePanel`           | `/admin/youtube/*`                 | **yes** — `admin/pages/YouTubeGatePage.tsx` |
| `QueueHealthPanel`           | `/intelligence/queue-health`, `/auto-tune` | **yes** — `admin/pages/QueueHealthPage.tsx` |
| `AnomalyPanel`               | anomaly routes                     | partial — surfaced on AdminHomePage |
| `OpsPanel`                   | `/ops-signals`                     | partial — platform badges |

The four that had no home now have one, so those originals are safe to delete
whenever someone is confident nothing was missed in the rewrite; they are kept
for now only as a diff reference. `AnomalyPanel` and `OpsPanel` are the ones
still worth reading before deleting — their coverage in the new shell is partial,
not equivalent.

Porting note: the old panels authenticated with `X-Admin-Token`, which the
current UI cannot produce. Anything ported must use the admin session token —
`adminFetch` for `/api/v1/admin/*`, `intelFetch` for `/api/v1/intelligence/*`.

Do not add anything new to this folder.
