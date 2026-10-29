# NHL Daily cloud delivery

GitHub-hosted workflow runs daily at 6:00 a.m. America/Los_Angeles, with a 6:20 a.m. recovery attempt. DST is automatic. GitHub can delay scheduled jobs; exact delivery time is not guaranteed.

## One-time private setup

In this repository, Settings → Secrets and variables → Actions → New repository secret:

- `SMTP_USERNAME`: your Gmail sending address.
- `EMAIL_TO`: your destination address.
- `GMAIL_APP_PASSWORD`: a Google app password created for this workflow. Never put it in a source file, issue, chat, or public report. Google requires 2-Step Verification. A Google app password grants substantial mail-account access; use a dedicated sending account if preferred. Revoke it when no longer needed.

The workflow reads Sent via IMAP to prevent duplicates and verify delivery; it sends through Gmail's encrypted SMTP. It does not reuse the ChatGPT Gmail connection. No paid service is configured.

Run Actions → NHL Daily Email → Run workflow with Preview checked to test research/layout. After secrets are saved, uncheck Preview for one authorized delivery test. Confirm the message in Gmail Sent and your inbox. Confirm an unattended morning run before relying on daily delivery.

Enable GitHub Actions failure notifications for this repository in your GitHub notification settings. A failure leaves the next run enabled. Public-repository schedules can be disabled after 60 days without repository activity; report commits normally provide activity, but review this after an offseason or extended failures.

## Behavior

Email and website are attempted independently. Sent-mail lookup fails closed; it never blindly retries an uncertain SMTP submission. Runs are serialized. Every later attempt searches Sent again. A successful send is not repeated at 6:20. Website publishes through the workflow's short-lived GITHUB_TOKEN, requiring no personal access token. No email address or credential is stored in public report HTML. The previous ChatGPT task should remain paused to avoid competing senders.

Sources: NHL public endpoints first for records and scoring; ESPN live endpoints as fallback for standings, scoring, schedule, goalie designations and single-book full-game moneylines. Missing optional information is labeled unavailable. Schedule failure stops delivery rather than claiming no games. Source URLs and retrieval times are in the run artifact, outside the report. No AI/API key is required.

History uses verified exact-date entries in scripts/history.json; dates without a maintained entry use a rotating verified trivia pool, explicitly labeled trivia. This is a limitation compared with daily free-form historical research. Add verified dated entries as coverage expands.

Local checks (Python 3.9+): `python -m unittest discover -s tests -v`; `python scripts/daily.py --preview`. The preview never sends or publishes.
