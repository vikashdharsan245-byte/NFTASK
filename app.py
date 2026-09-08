
import os
from dotenv import load_dotenv
load_dotenv()
import secrets
import hashlib
import hmac
import requests
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Flask, jsonify, render_template, request, session, redirect
from flask_sqlalchemy import SQLAlchemy
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "dev-change-this-secret")

database_url = os.environ.get("DATABASE_URL")
is_vercel = bool(os.environ.get("VERCEL"))

# Vercel serverless functions must use a remote database.
# Never allow file-backed SQLite on Vercel.
if is_vercel:
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL is missing. Configure PostgreSQL in Vercel Project Settings."
        )

    normalized_db = database_url.lower()

    if normalized_db.startswith("sqlite:"):
        raise RuntimeError(
            "SQLite is not supported on Vercel. "
            "Set DATABASE_URL to a managed PostgreSQL connection string."
        )

if database_url:
    if database_url.startswith("postgres://"):
        database_url = database_url.replace(
            "postgres://", "postgresql+psycopg://", 1
        )
    elif database_url.startswith("postgresql://") and "+psycopg" not in database_url:
        database_url = database_url.replace(
            "postgresql://", "postgresql+psycopg://", 1
        )
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
else:
    # Local development only.
    local_db = os.path.join(os.path.dirname(__file__), "local.db")
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + local_db

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE", "0") == "1"

db = SQLAlchemy(app)

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.environ.get(
    "GOOGLE_REDIRECT_URI",
    "https://nftask.vercel.app/api/auth/google/callback",
)


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    google_sub = db.Column(db.String(255), nullable=True, unique=True)
    name = db.Column(db.String(255), nullable=True, default="")
    roll_no = db.Column(db.String(100), nullable=True, default="")
    department = db.Column(db.String(100), nullable=True, default="")
    year = db.Column(db.String(30), nullable=True, default="")
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class Team(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(100), unique=True, nullable=False, index=True)
    name = db.Column(db.String(150), nullable=False)
    description = db.Column(db.Text, default="")
    active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)


class Question(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(db.String(120), unique=True, nullable=False, index=True)
    team_id = db.Column(db.Integer, db.ForeignKey("team.id"), nullable=False, index=True)
    title = db.Column(db.Text, nullable=False)
    question_type = db.Column(db.String(30), default="textarea")
    options_json = db.Column(db.Text, default="[]")
    required = db.Column(db.Boolean, default=True)
    active = db.Column(db.Boolean, default=True)
    sort_order = db.Column(db.Integer, default=0)

    team = db.relationship("Team", backref=db.backref("questions", lazy=True))


class Application(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), unique=True, nullable=False)
    preferences_json = db.Column(db.Text, default="[]")
    locked = db.Column(db.Boolean, default=False)
    submitted_at = db.Column(db.DateTime, nullable=True)

    user = db.relationship("User", backref=db.backref("application", uselist=False))


class Answer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    application_id = db.Column(db.Integer, db.ForeignKey("application.id"), nullable=False, index=True)
    team_id = db.Column(db.Integer, db.ForeignKey("team.id"), nullable=False, index=True)
    question_id = db.Column(db.Integer, db.ForeignKey("question.id"), nullable=False, index=True)
    answer_text = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc),
                           onupdate=lambda: datetime.now(timezone.utc))

    question = db.relationship("Question")
    team = db.relationship("Team")




def now():
    return datetime.now(timezone.utc)


def normalize_email(email):
    return str(email or "").strip().lower()


def valid_nitt_email(email):
    email = normalize_email(email)
    return email.endswith("@" + ALLOWED_DOMAIN)


def make_otp():
    return f"{secrets.randbelow(1_000_000):06d}"


def otp_digest(otp):
    secret = os.environ.get("OTP_SECRET", app.config["SECRET_KEY"]).encode()
    return hmac.new(secret, str(otp).encode(), hashlib.sha256).hexdigest()


def get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return db.session.get(User, user_id)


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not get_current_user():
            return jsonify(message="Authentication required."), 401
        return fn(*args, **kwargs)
    return wrapper


def user_json(user):
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name or "",
        "rollNo": user.roll_no or "",
        "department": user.department or "",
        "year": user.year or "",
    }


def get_application(user):
    application = Application.query.filter_by(user_id=user.id).first()
    if not application:
        application = Application(
            user_id=user.id,
            preferences_json="[]",
            locked=False,
        )
        db.session.add(application)
        db.session.commit()
    return application


def get_preferences(application):
    import json
    try:
        value = json.loads(application.preferences_json or "[]")
        return value if isinstance(value, list) else []
    except Exception:
        return []


def save_preferences(application, preferences):
    import json
    application.preferences_json = json.dumps(preferences)


def application_json(application):
    import json
    preferences = get_preferences(application)
    answers = {}
    for answer in Answer.query.filter_by(application_id=application.id).all():
        team = answer.team.slug
        answers.setdefault(team, {})[answer.question.public_id] = answer.answer_text

    completed = {}
    for team_slug in preferences:
        team = Team.query.filter_by(slug=team_slug).first()
        if not team:
            continue
        required_ids = {q.id for q in Question.query.filter_by(
            team_id=team.id, active=True, required=True
        ).all()}
        answered_ids = {a.question_id for a in Answer.query.filter_by(
            application_id=application.id, team_id=team.id
        ).all()}
        if required_ids and required_ids.issubset(answered_ids):
            completed[team_slug] = True

    return {
        "preferences": preferences,
        "locked": bool(application.locked),
        "answers": answers,
        "completed": completed,
        "submitted": bool(application.submitted_at),
    }



_database_initialized = False

@app.before_request
def ensure_database():
    global _database_initialized
    if _database_initialized:
        return
    initialize_database()
    _database_initialized = True

@app.get("/api/health")
def health():
    return jsonify(ok=True, service="nitt-team-inductions")

@app.get("/")
def index():
    return render_template(
        "index.html",
        google_client_id=GOOGLE_CLIENT_ID,
    )



@app.get("/api/auth/google/start")
def google_auth_start():
    """Start Google OAuth authorization-code flow."""
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return jsonify(
            message="Google OAuth is not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET."
        ), 500

    state = secrets.token_urlsafe(32)
    session["oauth_state"] = state

    from urllib.parse import urlencode

    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": GOOGLE_REDIRECT_URI,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "online",
        "prompt": "select_account",
        "state": state,
    }

    return redirect(
        "https://accounts.google.com/o/oauth2/v2/auth?"
        + urlencode(params)
    )


@app.get("/api/auth/google/callback")
def google_auth_callback():
    """Handle Google's OAuth callback and create the Flask login session."""
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return "Google OAuth is not configured on the server.", 500

    code = request.args.get("code")
    state = request.args.get("state")
    expected_state = session.pop("oauth_state", None)

    if not code:
        error = request.args.get("error", "unknown_error")
        return f"Google sign-in was cancelled or failed: {error}", 400

    if not state or not expected_state or not secrets.compare_digest(state, expected_state):
        return "Invalid OAuth state. Please start the login again.", 400

    try:
        token_response = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": GOOGLE_REDIRECT_URI,
                "grant_type": "authorization_code",
            },
            timeout=15,
        )
        token_response.raise_for_status()
        token_data = token_response.json()

        raw_id_token = token_data.get("id_token")
        if not raw_id_token:
            return "Google did not return an ID token.", 400

        info = id_token.verify_oauth2_token(
            raw_id_token,
            google_requests.Request(),
            GOOGLE_CLIENT_ID,
        )

        email = normalize_email(info.get("email"))
        email_verified = bool(info.get("email_verified"))

        if not email:
            return "Google did not provide an email address.", 400

        if not email_verified:
            return "Your Google email is not verified.", 403

        user = User.query.filter_by(email=email).first()

        if not user:
            user = User(email=email)
            db.session.add(user)

        user.google_sub = info.get("sub")
        user.name = info.get("name") or user.name or ""

        db.session.commit()

        session.clear()
        session["user_id"] = user.id
        session.permanent = True

        return redirect("/")

    except requests.RequestException:
        return "Could not contact Google OAuth. Please try again.", 502
    except ValueError:
        return "Google returned an invalid ID token.", 400
    except Exception:
        app.logger.exception("Google OAuth callback failed")
        return "Google sign-in failed on the server.", 500


@app.get("/api/auth/me")
def auth_me():
    user = get_current_user()
    if not user:
        return jsonify(message="Authentication required."), 401
    return jsonify(user=user_json(user))
@app.get("/api/teams")
@login_required
def teams():
    rows = Team.query.filter_by(active=True).order_by(
        Team.sort_order.asc(), Team.name.asc()
    ).all()

    return jsonify(teams=[
        {
            "id": team.slug,
            "name": team.name,
            "desc": team.description or "",
        }
        for team in rows
    ])


@app.get("/api/application")
@login_required
def application_get():
    user = get_current_user()
    return jsonify(application_json(get_application(user)))


@app.put("/api/application/preferences")
@login_required
def application_preferences():
    import json
    user = get_current_user()
    application = get_application(user)

    if application.locked:
        return jsonify(message="Preferences are already locked."), 409

    data = request.get_json(silent=True) or {}
    preferences = data.get("preferences")

    if not isinstance(preferences, list) or len(preferences) != 3:
        return jsonify(message="Exactly 3 preferences are required."), 400

    if len(set(preferences)) != 3:
        return jsonify(message="Preferences must be different."), 400

    teams = Team.query.filter(
        Team.slug.in_(preferences),
        Team.active.is_(True),
    ).all()

    if len(teams) != 3:
        return jsonify(message="One or more selected domains are invalid."), 400

    # Preserve the exact preference order entered by the student.
    valid_slugs = {team.slug for team in teams}
    if any(item not in valid_slugs for item in preferences):
        return jsonify(message="Invalid domain selected."), 400

    save_preferences(application, preferences)
    application.locked = True
    db.session.commit()

    return jsonify(
        preferences=preferences,
        locked=True,
    )


@app.get("/api/questionnaires/<team_slug>")
@login_required
def questionnaire_get(team_slug):
    user = get_current_user()
    application = get_application(user)
    preferences = get_preferences(application)

    if not application.locked or team_slug not in preferences:
        return jsonify(
            message="This domain is not one of your locked preferences."
        ), 403

    team = Team.query.filter_by(slug=team_slug, active=True).first()
    if not team:
        return jsonify(message="Domain not found."), 404

    questions = Question.query.filter_by(
        team_id=team.id,
        active=True,
    ).order_by(Question.sort_order.asc(), Question.id.asc()).all()

    import json
    return jsonify(
        questions=[
            {
                "id": q.public_id,
                "title": q.title,
                "type": q.question_type,
                "options": json.loads(q.options_json or "[]"),
                "required": bool(q.required),
            }
            for q in questions
        ]
    )


@app.put("/api/questionnaires/<team_slug>/answers")
@login_required
def questionnaire_answers(team_slug):
    import json
    user = get_current_user()
    application = get_application(user)
    preferences = get_preferences(application)

    if not application.locked or team_slug not in preferences:
        return jsonify(
            message="This domain is not one of your locked preferences."
        ), 403

    team = Team.query.filter_by(slug=team_slug, active=True).first()
    if not team:
        return jsonify(message="Domain not found."), 404

    data = request.get_json(silent=True) or {}
    incoming = data.get("answers", {})

    if not isinstance(incoming, dict):
        return jsonify(message="Answers must be an object."), 400

    questions = Question.query.filter_by(
        team_id=team.id,
        active=True,
    ).order_by(Question.sort_order.asc()).all()

    for q in questions:
        answer = str(incoming.get(q.public_id, "")).strip()

        if q.required and not answer:
            return jsonify(message=f"Answer required for: {q.title}"), 400

        if q.question_type == "select":
            options = json.loads(q.options_json or "[]")
            if answer and answer not in options:
                return jsonify(message=f"Invalid option for: {q.title}"), 400

    # Replace this team's answers atomically.
    Answer.query.filter_by(
        application_id=application.id,
        team_id=team.id,
    ).delete()

    result_answers = {}

    for q in questions:
        answer = str(incoming.get(q.public_id, "")).strip()
        if not answer:
            continue

        item = Answer(
            application_id=application.id,
            team_id=team.id,
            question_id=q.id,
            answer_text=answer,
            updated_at=now(),
        )
        db.session.add(item)
        result_answers[q.public_id] = answer

    db.session.commit()

    # Check final completion.
    preferences = get_preferences(application)
    all_done = True

    for slug in preferences:
        t = Team.query.filter_by(slug=slug).first()
        if not t:
            all_done = False
            break

        required_questions = Question.query.filter_by(
            team_id=t.id,
            active=True,
            required=True,
        ).all()

        answered_ids = {
            a.question_id for a in Answer.query.filter_by(
                application_id=application.id,
                team_id=t.id,
            ).all()
        }

        required_ids = {q.id for q in required_questions}
        if not required_ids.issubset(answered_ids):
            all_done = False
            break

    if all_done:
        application.submitted_at = now()
        db.session.commit()

    return jsonify(
        team=team_slug,
        answers=result_answers,
        completed=True,
        applicationCompleted=all_done,
    )


@app.post("/api/admin/seed")
def seed_database():
    # Development convenience only. Never expose this publicly without
    # adding proper admin authentication.
    if os.environ.get("ALLOW_SEED_ENDPOINT", "0") != "1":
        return jsonify(message="Seed endpoint disabled."), 403

    seed_data()
    return jsonify(message="Database seeded.")


def seed_data():
    import json

    team_rows = [
        ("webops", "WebOps", "Web development and infrastructure"),
        ("design", "Design", "UI/UX and creative design"),
        ("marketing", "Marketing", "Promotion and outreach"),
        ("events", "Events", "Planning and execution"),
        ("oc", "Organising Committee", "Backend of every ops"),
        ("pain", "PAIN", "Team behind shows"),
    ]

    question_rows = [
        ("why", "Why do you want to join this team?"),
        ("exp", "What experience or skills do you have?"),
        ("story", "Tell us about a project or achievement."),
    ]

    for sort, (slug, name, desc) in enumerate(team_rows, start=1):
        team = Team.query.filter_by(slug=slug).first()
        if not team:
            team = Team(
                slug=slug,
                name=name,
                description=desc,
                active=True,
                sort_order=sort,
            )
            db.session.add(team)
            db.session.flush()
        else:
            team.name = name
            team.description = desc
            team.active = True
            team.sort_order = sort

        for qsort, (key, title) in enumerate(question_rows, start=1):
            public_id = f"{slug}_{key}"
            q = Question.query.filter_by(public_id=public_id).first()

            if not q:
                q = Question(
                    public_id=public_id,
                    team_id=team.id,
                    title=title,
                    question_type="textarea",
                    options_json="[]",
                    required=True,
                    active=True,
                    sort_order=qsort,
                )
                db.session.add(q)
            else:
                q.title = title
                q.team_id = team.id
                q.active = True
                q.sort_order = qsort

    db.session.commit()


def initialize_database():
    """
    Initialize schema explicitly.
    This is NOT executed at module import, because Vercel imports
    the Flask module while constructing serverless invocations.
    """
    with app.app_context():
        db.create_all()
        if os.environ.get("SEED_ON_START", "0") == "1":
            seed_data()



if __name__ == "__main__":
    initialize_database()
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG", "0") == "1",
    )
