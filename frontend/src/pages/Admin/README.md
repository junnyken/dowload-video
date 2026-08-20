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

| Panel                        | Backend still there            | In `src/admin/`? |
|------------------------------|--------------------------------|------------------|
| `PlaybooksPanel`             | `/intelligence/playbooks`      | no               |
| `AutomationHistoryPanel`     | `/intelligence/automation-history` | no           |
| `YouTubeGatePanel`           | YouTube gate admin routes      | no               |
| `QueueHealthPanel`           | queue/health routes            | no               |
| `AnomalyPanel`               | anomaly routes                 | partial — AdminHomePage |
| `OpsPanel`                   | `/ops-signals`                 | partial — platform badges |

So this is not just dead code: it is a list of admin capabilities that were lost
when the UI was swapped, and the fastest reference for porting them into
`src/admin/pages/`. Delete a file here once its capability has a home in the new shell.

Do not add anything new to this folder.
