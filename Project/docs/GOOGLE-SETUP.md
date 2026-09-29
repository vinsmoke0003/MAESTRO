# Connecting MAESTRO to Gmail, Google Drive and Google Calendar

About 10 minutes, once. Free: the Gmail and Drive APIs cost nothing for personal
use, and no billing account is needed.

You do steps 1–5 in your browser. MAESTRO never sees your Google password;
Google's own sign-in page asks you to approve the permissions, and MAESTRO
receives a token that stays on your computer.

## 1. Create a Google Cloud project

1. Open <https://console.cloud.google.com/> and sign in with the Gmail account
   you want MAESTRO to use.
2. Click the project picker at the top, then **New project**. Name it `MAESTRO`
   and click **Create**. Make sure it is selected afterwards.

## 2. Turn on the three APIs

1. Go to **APIs & Services → Library**.
2. Search **Gmail API**, open it, click **Enable**.
3. Search **Google Drive API**, open it, click **Enable**.
4. Search **Google Calendar API**, open it, click **Enable**.

## 3. Set up the consent screen (your app, in Testing mode)

1. Go to **APIs & Services → OAuth consent screen** (on newer consoles this is
   **Google Auth Platform**, sections *Branding* and *Audience*).
2. App name `MAESTRO`, support email = your address, developer contact = your
   address. Save.
3. **Audience / User type: External**, and leave the publishing status as
   **Testing**.
4. Under **Test users**, click **Add users** and add your own Gmail address.
   Only accounts on this list can sign in — which is exactly what you want.

You do not need to add scopes here; MAESTRO requests them when you connect.

## 4. Create the OAuth client

1. Go to **APIs & Services → Credentials** (or **Google Auth Platform → Clients**).
2. **Create credentials → OAuth client ID**.
3. Application type: **Desktop app**. Name: `MAESTRO desktop`. Click **Create**.
4. Click **Download JSON**.

## 5. Put the file where MAESTRO looks for it

```bash
mkdir -p ~/.maestro/google
```

```bash
mv ~/Downloads/client_secret_*.json ~/.maestro/google/client_secret.json
```

`~/.maestro` is on MAESTRO's own denylist: no plan can read, move or upload
anything in it, including this file and the token.

## 6. Install and connect

From the `Project` folder, with the venv active:

```bash
pip install -e ".[google]"
```

```bash
maestro google connect
```

Your browser opens. Choose your account. Google shows **"Google hasn't
verified this app"** — expected, because it is your own app in Testing mode:
click **Continue**. Tick the permissions and click **Continue**. The page says
MAESTRO is connected; close it.

```bash
maestro google status
```

## 7. Try it

```bash
maestro ask "check my inbox"
```

```bash
maestro ask "any unread emails from Priya this week"
```

```bash
maestro ask "find my DSA notes in google drive"
```

```bash
maestro ask "check my last 15 emails for tests or tickets and add reminders to my calendar"
```

Or by voice: *"Maestro, check my inbox."*

## What MAESTRO can and cannot do

| Request | Risk | Approval |
|---|---|---|
| search / list emails, read an email | R0 | never |
| draft a Gmail (saved to Drafts, **not sent**) | R2 | spoken "yes" |
| search Drive, download files into your workspace | R0 / R1 | never |
| upload files to your Drive | R2 | spoken "yes" |
| find tests, tickets, deadlines in your **last 15** emails and add calendar reminders (1 day + 1 hour before) | R2 | spoken "yes", after seeing every event |
| **send** an email | — | always refused: you press Send in Gmail |
| **share** or **delete** a Drive file | — | always refused (hard-blocked) |

Email bodies and Drive file names are written by other people, so MAESTRO
treats them as untrusted: it shows them to you, and nothing in them can become
an instruction.

## Good to know

- **Weekly re-connect.** While the app is in Testing mode, Google expires the
  sign-in after about 7 days. When a Gmail or Drive command says to, run
  `maestro google connect` again.
- **Disconnect any time:** `maestro google disconnect` revokes the token at
  Google and deletes it here. You can also remove access at
  <https://myaccount.google.com/permissions>.
- **Never commit** `client_secret.json` or `token.json`. Both live outside the
  repository, and `.gitignore` excludes them anyway.
- **Calendar reminders are yours alone.** Events go on your own calendar with no
  guests, so no invitation is ever emailed. Each one says which email it came
  from — check the original before relying on a date. Running the request again
  does not add duplicates, and a failed run removes what it added.
- **Why "gmail.compose"?** It is the narrowest Google scope that allows
  creating drafts. Google bundles "send" into it, but MAESTRO has no code that
  sends: `email.send` is hard-blocked in the verb registry and has no executor.
