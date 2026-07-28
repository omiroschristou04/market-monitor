# GitHub Secrets Setup

The daily briefing runs in GitHub Actions
(`.github/workflows/daily_briefing.yml`) at **05:30 UTC every weekday
(06:30 UK time)**. Your laptop does not need to be on.

Actions cannot read your local `.env` file — it never leaves your machine and
is git-ignored. Instead, the four settings it contains are stored as
**GitHub Secrets** and injected as environment variables when the workflow
runs. `run.py` reads them with `os.environ.get`, so the same code works both
locally (from `.env`) and in the cloud (from Secrets).

---

## 1. Where to add them

In your browser:

1. Go to your repository: `https://github.com/omiroschristou04/market-monitor`
2. Click **Settings** (the tab along the top of the repo, not your account
   settings).
3. In the left sidebar, open **Secrets and variables** → **Actions**.
4. Make sure you are on the **Secrets** tab (not *Variables*).
5. Click the green **New repository secret** button.
6. Enter the **Name** exactly as written below and paste the value into
   **Secret**, then click **Add secret**.
7. Repeat for each of the four secrets.

Secret values are write-only: once saved you can update or delete them, but
GitHub will never show them again. They are also masked in workflow logs.

---

## 2. The four secrets

| Name | Required | What to paste |
|---|---|---|
| `GMAIL_ADDRESS` | Yes | The Gmail address the briefing is sent **from**, e.g. `youraddress@gmail.com` |
| `GMAIL_APP_PASSWORD` | Yes | Your 16-character Gmail **App Password**, spaces removed |
| `EMAIL_RECIPIENT` | Optional | Who receives the briefing. Omit to send to yourself (`GMAIL_ADDRESS`) |
| `PAGES_URL` | Optional | Public report URL, e.g. `https://omiroschristou04.github.io/market-monitor/` |

If a required secret is missing the pipeline does **not** crash — it still
fetches data and publishes the report, and simply skips the email with a
warning in the logs.

### Where to find each value

**`GMAIL_ADDRESS`** — the Gmail account you want to send from.

**`GMAIL_APP_PASSWORD`** — Gmail blocks normal-password SMTP logins, so you
need an App Password:

1. Turn on 2-Step Verification: <https://myaccount.google.com/security>
2. Open <https://myaccount.google.com/apppasswords>
3. Choose app **Mail**, device **Other**, name it `Market Monitor`, then
   **Generate**.
4. Google shows a 16-character password like `abcd efgh ijkl mnop`.
   **Remove the spaces** and paste `abcdefghijklmnop` as the secret value.

You already have this in your local `.env` file — open it and copy the
`GMAIL_APP_PASSWORD` value straight across.

**`EMAIL_RECIPIENT`** — any address you want the briefing delivered to.

**`PAGES_URL`** — the address GitHub Pages serves your report at. Find
it under **Settings → Pages**; it is shown as *"Your site is live at …"*. It
follows the pattern `https://<your-username>.github.io/<repo-name>/`. This is
the link behind the **Open Full Report** button in the email, so without it
the button falls back to a local `file://` path that only works on your PC.

---

## 3. Enable GitHub Pages (one-time)

The workflow pushes the refreshed `docs/index.html`, but Pages has to be
switched on to serve it:

1. **Settings** → **Pages**
2. **Source**: *Deploy from a branch*
3. **Branch**: `main`, folder: `/docs` → **Save**

---

## 4. Let the workflow push its commit (one-time)

The workflow commits the updated `docs/` back to the repo. It authenticates
with the automatic `GITHUB_TOKEN` — there is nothing to configure and no
password to store — but the token needs write permission:

1. **Settings** → **Actions** → **General**
2. Scroll to **Workflow permissions**
3. Select **Read and write permissions** → **Save**

(The workflow also declares `permissions: contents: write` itself, which is
enough on most repositories; the setting above is the fallback if pushes are
rejected with a 403.)

---

## 5. Test it

Do not wait until tomorrow morning:

1. Go to the **Actions** tab.
2. Select **Daily Market Briefing** in the left sidebar.
3. Click **Run workflow** → **Run workflow**.
4. Open the run and expand **Run the pipeline** to watch the log.

What a healthy run looks like:

- `Loading configuration...` lists `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD:
  set (16 chars)` and so on — secrets are masked as `***` by GitHub.
- `[6/8] Copying report to Desktop...` prints
  `! Desktop not found ...; skipping copy.` — expected on a Linux runner.
- `[7/8] Publishing ...` prints `Running under GitHub Actions — the workflow
  will commit and push docs/.`
- `[8/8] Emailing report via Gmail SMTP...` prints `Email sent to ...`
- The final step commits `Daily briefing - YYYY-MM-DD` and pushes.

### If something goes wrong

| Symptom in the log | Fix |
|---|---|
| `! GMAIL_ADDRESS is not set` | Secret name is misspelled — it must match exactly, including case |
| `Gmail rejected the login` | The App Password is wrong or still has spaces in it; regenerate and re-save |
| `git push` fails with `403` | Workflow permissions are read-only — see step 4 above |
| `docs/ is unchanged` | The report was identical to the last run; nothing to publish, not an error |

---

## 6. Rotating or removing a secret

Same screen as step 1: **Settings** → **Secrets and variables** → **Actions**.
Click the pencil icon next to a secret to replace its value, or the bin icon to
delete it. If you ever revoke the Gmail App Password at
<https://myaccount.google.com/apppasswords>, generate a new one and update
`GMAIL_APP_PASSWORD` in both your local `.env` and the GitHub Secret.
