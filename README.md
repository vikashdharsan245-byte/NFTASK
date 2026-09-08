
# NITT Team Inductions — Flask + Vercel + PostgreSQL

This is the complete backend integration for the uploaded Team Inductions frontend.

## Stack

Frontend:
- Existing HTML/CSS design
- Vanilla JavaScript

Backend:
- Flask
- Flask-SQLAlchemy
- Vercel Python serverless function

Database:
- SQLite for local development
- PostgreSQL for Vercel production

Authentication:
- Google OAuth 2.0 / OpenID Connect "Sign in with Google"
- Restricted to `@nitt.edu`
- Server verifies the Google ID token
- OTP is then sent to the same verified NITT email
- Student session is created only after OTP verification

Email:
- Resend

## Why PostgreSQL on Vercel

The app can use SQLite locally, but do not use a local `.db` file as the production database on Vercel. Serverless instances have ephemeral filesystems.

Set:

    DATABASE_URL=postgresql+psycopg://...

in Vercel for production.

## 1. Install

Python 3.11+ is recommended.

    python -m venv .venv
    source .venv/bin/activate

Windows:

    .venv\Scripts\activate

Then:

    pip install -r requirements.txt

## 2. Local environment

Copy `.env.example` to `.env`.

For local SQLite:

    DATABASE_URL=sqlite:///local.db

Set these:

    FLASK_SECRET_KEY=...
    OTP_SECRET=...
    GOOGLE_CLIENT_ID=...
    RESEND_API_KEY=...
    OTP_FROM=...

## 3. Google OAuth setup

This implementation uses Google Sign-In/OIDC.

Create a Google Cloud project and a Web OAuth client / Google Identity credential.

Authorized JavaScript origin locally:

    http://127.0.0.1:5000

or:

    http://localhost:5000

Production:

    https://YOUR-VERCEL-DOMAIN.vercel.app

The frontend requests the NITT hosted domain with:

    data-hd="nitt.edu"

IMPORTANT:
`hd` is only an account-selection hint. The Flask backend also validates:
- ID-token signature
- audience
- issuer/expiry through google-auth
- `email_verified == true`
- `hd == nitt.edu`
- email ends with `@nitt.edu`

So a user cannot simply edit the browser's `data-hd` attribute to bypass the restriction.

Google's documentation says `hd` can restrict account selection to a Workspace domain and recommends server-side verification of the ID token. See:
https://developers.google.com/identity/gsi/web/reference/html-reference

## 4. OTP flow

The normal flow is:

1. Student opens website.
2. Student clicks "Sign in with Google".
3. Google authenticates the user.
4. Flask verifies the Google ID token.
5. Flask checks the NITT domain and verified email.
6. Flask sends an OTP to that exact NITT email using Resend.
7. Student enters OTP.
8. Flask creates the application session.
9. Student selects 3 preferences.
10. Flask locks the preferences in the database.
11. Flask loads questionnaires using those domains.
12. Student answers each domain's questionnaire.
13. Flask validates and stores each answer.
14. Final completion is recorded with `submitted_at`.

There is also a direct NITT-email OTP fallback in the UI. For maximum assurance, you can remove that fallback and require Google + OTP only.

## 5. Run locally

    python run.py

Open:

    http://127.0.0.1:5000

The database and tables are created automatically.

Sample teams/questions are seeded automatically when `SEED_ON_START=1`.

## 6. Database schema

Users:
- email
- google_sub
- name
- roll_no
- department
- year

Teams:
- slug
- name
- description
- active
- sort_order

Questions:
- public_id
- team_id
- title
- question_type
- options_json
- required
- active
- sort_order

Applications:
- user_id
- preferences_json
- locked
- submitted_at

Answers:
- application_id
- team_id
- question_id
- answer_text
- updated_at

OTP challenges:
- email
- OTP hash
- expires_at
- sent_at
- attempts

The OTP itself is never stored in plaintext.

## 7. Vercel deployment

1. Push this project to GitHub.
2. Import it into Vercel.
3. Vercel should detect `vercel.json`.
4. Set environment variables in Vercel.
5. Set `DATABASE_URL` to a managed PostgreSQL database.
6. Set `COOKIE_SECURE=1`.
7. Set:
       SEED_ON_START=1
   for the first deployment, or use a separate database initialization step.
8. Deploy.

Set your Google OAuth authorized JavaScript origin to:

    https://YOUR-VERCEL-DOMAIN.vercel.app

You do not need a DAuth callback URL because this project does NOT use DAuth.

## 8. Resend

Create a Resend API key and verify the domain used by:

    OTP_FROM

Example:

    OTP_FROM=Team Inductions <no-reply@inductions.example>

Put the key only in Vercel/server environment variables.

## 9. Security notes

- Never place GOOGLE client secrets/API keys in frontend JavaScript.
- The Google client ID may be public; token verification happens on the Flask backend.
- Keep FLASK_SECRET_KEY and OTP_SECRET private.
- Use HTTPS in production.
- Use PostgreSQL in production.
- Add rate limiting at the Vercel/edge layer for public auth endpoints.
- Consider adding Redis for rate-limit counters if traffic is significant.
- Add an admin authentication layer before exposing question/domain editing.
- The `/api/admin/seed` endpoint is disabled unless `ALLOW_SEED_ENDPOINT=1`; do not enable it publicly.

## 10. Adding domain-specific questions

Each question is linked to a Team row.

Example:

    Team = webops

    Question:
      public_id = webops_why
      title = Why do you want to join WebOps?
      team_id = webops

The browser never receives questions for domains the user did not lock.

## 11. Production architecture

    Browser
       |
       v
    Vercel
       |
       +---- Flask API
       |
       +---- PostgreSQL
       |
       +---- Resend
       |
       +---- Google OAuth/OIDC

No MongoDB.
No DAuth.
No separate Express server.


## IMPORTANT VERCEL FIX

Do not deploy this application with:

    DATABASE_URL=sqlite:///local.db

Vercel's serverless runtime does not provide a durable writable application
filesystem. A file-backed SQLite database can therefore cause startup errors
such as:

    FUNCTION_INVOCATION_FAILED
    OSError: [Errno 30] Read-only file system: '/var/task/instance'

For Vercel, set DATABASE_URL to a managed PostgreSQL database instead.

This fixed build uses a root-level `app.py`, matching Vercel's current
zero-configuration Flask deployment model.

Example:

    DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/DBNAME?sslmode=require

Also set:

    COOKIE_SECURE=1



## FINAL VERCEL CHECK

Before redeploying, go to:

Vercel → Project → Settings → Environment Variables

Delete any old variable such as:

    DATABASE_URL=sqlite:///local.db

Replace it with a remote PostgreSQL connection:

    DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/DBNAME?sslmode=require

The application now explicitly crashes with a clear configuration message if
Vercel is given SQLite, instead of trying to write `/var/task/instance/...`.

Also redeploy after changing environment variables. Vercel environment
variables are attached to deployments; changing them without a new deployment
does not fix the already-created deployment.

After deployment, test:

    https://YOUR-DOMAIN.vercel.app/api/health

Expected:

    {"ok": true, "service": "nitt-team-inductions"}

Then open the home page.

\n## IMPORTANT: Google-only build
This build removes the Resend import and all OTP code. It uses Google Sign-In only.
If Vercel logs mention `from resend import Resend`, Vercel is deploying an old
commit. Commit/push this build and create a new deployment.
