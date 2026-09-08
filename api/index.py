
import os
from dotenv import load_dotenv
load_dotenv()
import secrets
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Flask, jsonify, render_template, request, session, redirect
from flask_sqlalchemy import SQLAlchemy
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from resend import Resend

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY", "dev-change-this-secret")

database_url = os.environ.get("DATABASE_URL")
if database_url:
    # Render/provider sometimes gives postgres://; SQLAlchemy expects postgresql://
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = database_url
else:
    app.config["SQLALCHEMY_DATABASE_URI"] = (
        "sqlite:///" + os.path.join(os.path.dirname(__file__), "local.db")
    )

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE", "0") == "1"

db = SQLAlchemy(app)

OTP_TTL_MINUTES = int(os.environ.get("OTP_TTL_MINUTES", "10"))
OTP_RESEND_SECONDS = int(os.environ.get("OTP_RESEND_SECONDS", "60"))
OTP_MAX_ATTEMPTS = int(os.environ.get("OTP_MAX_ATTEMPTS", "5"))
ALLOWED_DOMAIN = os.environ.get("ALLOWED_EMAIL_DOMAIN", "nitt.edu").lower()
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")


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


class OtpChallenge(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), nullable=False, index=True)
    otp_hash = db.Column(db.String(64), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    sent_at = db.Column(db.DateTime, nullable=False)
    attempts = db.Column(db.Integer, default=0)


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



@app.get("/api/health")
def health():
    return jsonify(ok=True, service="nitt-team-inductions")

@app.get("/")
def index():
    return render_template(
        "index.html",
        google_client_id=GOOGLE_CLIENT_ID,
    )


@app.get("/api/auth/me")
def auth_me():
    user = get_current_user()
    if not user:
        return jsonify(message="Authentication required."), 401
    return jsonify(user=user_json(user))


@app.post("/api/auth/logout")
def auth_logout():
    session.clear()
    return jsonify(message="Logged out.")


@app.post("/api/auth/google")
def auth_google():
    if not GOOGLE_CLIENT_ID:
        return jsonify(message="GOOGLE_CLIENT_ID is not configured."), 500

    data = request.get_json(silent=True) or {}
    credential = data.get("credential")

    if not credential:
        return jsonify(message="Google credential is missing."), 400

    try:
        # Verifies signature, issuer, audience and token expiry.
        info = id_token.verify_oauth2_token(
            credential,
            google_requests.Request(),
            GOOGLE_CLIENT_ID,
        )
    except Exception:
        return jsonify(message="Invalid Google identity token."), 401

    email = normalize_email(info.get("email"))
    hosted_domain = normalize_email(info.get("hd"))
    email_verified = bool(info.get("email_verified"))

    # hd is not merely trusted from the UI: enforce it on the server.
    if (
        not valid_nitt_email(email)
        or hosted_domain != ALLOWED_DOMAIN
        or not email_verified
    ):
        return jsonify(
            message=f"Only verified {ALLOWED_DOMAIN} Google Workspace accounts are allowed."
        ), 403

    session["pending_google"] = {
        "email": email,
        "google_sub": info.get("sub"),
        "name": info.get("name", ""),
    }

    # Send OTP to the exact verified Google account email.
    result = issue_otp(email)
    if result[1] is not None:
        return result

    return jsonify(
        message="Google identity verified. An OTP was sent to your NITT email.",
        email=email,
    )


@app.post("/api/auth/request-otp")
def request_otp():
    data = request.get_json(silent=True) or {}
    email = normalize_email(data.get("email"))

    if not valid_nitt_email(email):
        return jsonify(message=f"Use your @{ALLOWED_DOMAIN} email address."), 400

    result = issue_otp(email)
    if result[1] is not None:
        return result

    return jsonify(
        message="OTP sent to your NITT email address.",
        email=email,
    )


def issue_otp(email):
    existing = OtpChallenge.query.filter_by(email=email).order_by(
        OtpChallenge.id.desc()
    ).first()

    current = now()
    if existing and (current - existing.sent_at).total_seconds() < OTP_RESEND_SECONDS:
        return jsonify(
            message=f"Please wait {OTP_RESEND_SECONDS} seconds before requesting another OTP."
        ), 429

    otp = make_otp()
    record = OtpChallenge(
        email=email,
        otp_hash=otp_digest(otp),
        sent_at=current,
        expires_at=current + timedelta(minutes=OTP_TTL_MINUTES),
        attempts=0,
    )
    db.session.add(record)
    db.session.commit()

    api_key = os.environ.get("RESEND_API_KEY")
    from_email = os.environ.get("OTP_FROM")

    if not api_key or not from_email:
        return jsonify(
            message="Email service is not configured. Set RESEND_API_KEY and OTP_FROM."
        ), 500

    try:
        resend = Resend(api_key)
        resend.emails.send({
            "from": from_email,
            "to": [email],
            "subject": "NITT Team Inductions OTP",
            "text": (
                f"Your Team Inductions OTP is {otp}. "
                f"It expires in {OTP_TTL_MINUTES} minutes."
            ),
        })
    except Exception as exc:
        app.logger.exception("OTP email failed: %s", exc)
        db.session.delete(record)
        db.session.commit()
        return jsonify(message="Failed to send OTP email."), 500

    return jsonify(message="OTP sent.", email=email), 200


@app.post("/api/auth/verify-otp")
def verify_otp():
    data = request.get_json(silent=True) or {}
    email = normalize_email(data.get("email"))
    otp = str(data.get("otp") or "")

    if not valid_nitt_email(email):
        return jsonify(message="Only NITT email addresses are allowed."), 400

    record = OtpChallenge.query.filter_by(email=email).order_by(
        OtpChallenge.id.desc()
    ).first()

    if not record:
        return jsonify(message="No active OTP challenge."), 400

    if record.expires_at < now():
        return jsonify(message="OTP expired. Request a new OTP."), 400

    if record.attempts >= OTP_MAX_ATTEMPTS:
        return jsonify(message="Too many attempts. Request a new OTP."), 429

    record.attempts += 1

    if not hmac.compare_digest(record.otp_hash, otp_digest(otp)):
        db.session.commit()
        return jsonify(message="Incorrect OTP."), 400

    # If Google login started the flow, the OTP email must match the
    # Google-verified identity. This prevents using Google for one account
    # and OTP for another.
    pending_google = session.get("pending_google")
    if pending_google and normalize_email(pending_google.get("email")) != email:
        return jsonify(message="OTP email does not match the Google account."), 403

    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(
            email=email,
            name=(pending_google or {}).get("name", ""),
            google_sub=(pending_google or {}).get("google_sub"),
            roll_no=email.split("@")[0],
        )
        db.session.add(user)
    else:
        if pending_google:
            user.google_sub = pending_google.get("google_sub") or user.google_sub
            user.name = pending_google.get("name") or user.name

    db.session.commit()
    get_application(user)

    session.clear()
    session["user_id"] = user.id

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


with app.app_context():
    db.create_all()
    if os.environ.get("SEED_ON_START", "1") == "1":
        seed_data()


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000")),
        debug=os.environ.get("FLASK_DEBUG", "0") == "1",
    )
