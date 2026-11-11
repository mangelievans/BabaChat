import os
import secrets
import hmac
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from urllib.parse import urlsplit
from dotenv import load_dotenv

from flask import (Flask, render_template, request, redirect, url_for, session,
                   flash, abort, send_from_directory, jsonify)
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO, join_room, emit
from sqlalchemy import or_, and_, func
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-change-this-secret-key")
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# Set DATABASE_URL to a PostgreSQL connection string to use PostgreSQL.
# SQLite remains a convenient local fallback until PostgreSQL is configured.
database_url = os.environ.get("DATABASE_URL", f"sqlite:///{(BASE_DIR / 'messenger.db').as_posix()}")
if database_url.startswith("postgres://"):
    database_url = "postgresql+psycopg://" + database_url[len("postgres://"):]
elif database_url.startswith("postgresql://"):
    database_url = "postgresql+psycopg://" + database_url[len("postgresql://"):]
app.config["SQLALCHEMY_DATABASE_URI"] = database_url

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins=None, async_mode="threading")

ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp", "mp4", "mov", "webm", "m4v"}
VIDEO_EXTENSIONS = {"mp4", "mov", "webm", "m4v"}
PROFILE_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
PROFILE_IMAGE_MAX_BYTES = 5 * 1024 * 1024
POST_MEDIA_MAX_BYTES = 25 * 1024 * 1024
STICKERS = {
    "heart": "❤️",
    "laugh": "😂",
    "love": "🥰",
    "cool": "😎",
    "party": "🎉",
    "thumbs-up": "👍",
}
REPLY_STICKER_MARKER = "\u2063sticker:"


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(
        db.String(24),
        unique=True,
        nullable=False,
        index=True
    )
    password_hash = db.Column(db.String(255), nullable=False)

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )

    # Whether this account has a verification badge.
    is_verified = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
        server_default=db.false()
    )

    # Whether this account can manage verification.
    is_admin = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
        server_default=db.false()
    )
    
    # Profile information
    display_name = db.Column(db.String(50), nullable=True)

    bio = db.Column(
        db.String(280),
        nullable=False,
        default="",
        server_default=""
    )

    location = db.Column(
        db.String(100),
        nullable=False,
        default="",
        server_default=""
    )

    website = db.Column(
        db.String(255),
        nullable=False,
        default="",
        server_default=""
    )

    profile_image = db.Column(db.String(255), nullable=True)
    banner_image = db.Column(db.String(255), nullable=True)
    joined_date = db.Column(db.Date, nullable=True)




class Message(db.Model):
    __tablename__ = "messages"
    id = db.Column(db.Integer, primary_key=True)
    sender_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    recipient_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    body = db.Column(db.Text, nullable=False, default="")
    media_name = db.Column(db.String(255))
    media_type = db.Column(db.String(16))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    sender = db.relationship("User", foreign_keys=[sender_id])
    recipient = db.relationship("User", foreign_keys=[recipient_id])

class Post(db.Model):
    __tablename__ = "posts"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    body = db.Column(db.Text, nullable=False)

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True
    )

    # Values: post, repost, quote
    post_type = db.Column(
        db.String(10),
        nullable=False,
        default="post",
        server_default="post"
    )

    # NULL for ordinary posts; original post ID for reposts and quotes.
    original_post_id = db.Column(
        db.Integer,
        db.ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=True,
        index=True
    )

    author = db.relationship("User", backref="public_posts")

    original_post = db.relationship(
        "Post",
        remote_side=[id],
        foreign_keys=[original_post_id],
        backref="derived_posts"
    )

    media = db.relationship(
        "PostMedia",
        back_populates="post",
        uselist=False,
        cascade="all, delete-orphan"
    )


class PostMedia(db.Model):
    __tablename__ = "post_media"

    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(
        db.Integer,
        db.ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=False,
        unique=True
    )
    media_name = db.Column(db.String(255), nullable=False, unique=True)
    media_type = db.Column(db.String(16), nullable=False)

    post = db.relationship("Post", back_populates="media")


class PostReply(db.Model):
    __tablename__ = "post_replies"

    id = db.Column(db.Integer, primary_key=True)
    post_id = db.Column(
        db.Integer,
        db.ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True
    )

    post = db.relationship(
        "Post",
        backref=db.backref(
            "replies",
            cascade="all, delete-orphan",
            order_by="PostReply.created_at"
        )
    )
    author = db.relationship("User")


class PostLike(db.Model):
    __tablename__ = "post_likes"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False
    )

    post_id = db.Column(
        db.Integer,
        db.ForeignKey("posts.id", ondelete="CASCADE"),
        nullable=False
    )

    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        db.UniqueConstraint(
            "user_id",
            "post_id",
            name="unique_user_post_like"
        ),
    )

    user = db.relationship("User")
    post = db.relationship("Post", backref="likes")


class UserFollow(db.Model):
    __tablename__ = "user_follows"

    id = db.Column(db.Integer, primary_key=True)
    follower_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    followed_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    __table_args__ = (
        db.UniqueConstraint("follower_id", "followed_id", name="unique_user_follow"),
        db.CheckConstraint("follower_id != followed_id", name="prevent_self_follow")
    )


# Create missing tables. This does not modify existing table columns.

with app.app_context():
    db.create_all()

@app.context_processor
def inject_admin_navigation():
    user_id = session.get("user_id")
    user = db.session.get(User, user_id) if user_id else None
    return {"current_user_is_admin": bool(user and user.is_admin)}

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in first.")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


@app.route("/complete-profile", methods=["GET", "POST"])
@login_required
def complete_profile():
    user = db.session.get(User, session["user_id"])

    if user is None:
        abort(404)

    if request.method == "POST":
        display_name = request.form.get("display_name", "").strip()

        if not display_name or len(display_name) > 50:
            flash("Your name is required and must be at most 50 characters.")
            return render_template(
                "complete_profile.html",
                display_name=display_name
            )

        user.display_name = display_name
        db.session.commit()

        flash("Your display name has been saved.")
        return redirect(url_for("post_feed"))

    return render_template(
        "complete_profile.html",
        display_name=user.display_name or ""
    )


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

def chat_room(user_a, user_b):
    low, high = sorted((int(user_a), int(user_b)))
    return f"chat_{low}_{high}"

def sticker_media_name(sticker_key):
    return f"sticker_{sticker_key}_{secrets.token_hex(12)}"

def sticker_emoji(media_name):
    if not media_name:
        return None
    parts = media_name.split("_", 2)
    if len(parts) == 3 and parts[0] == "sticker":
        return STICKERS.get(parts[1])
    return None

@app.template_filter("sticker_emoji")
def sticker_emoji_filter(media_name):
    return sticker_emoji(media_name)

def encode_reply_sticker(body, sticker_key):
    return f"{body}{REPLY_STICKER_MARKER}{sticker_key}\u2063"

def get_reply_sticker(body):
    if REPLY_STICKER_MARKER not in body:
        return None
    _, sticker_suffix = body.rsplit(REPLY_STICKER_MARKER, 1)
    if not sticker_suffix.endswith("\u2063"):
        return None
    sticker_key = sticker_suffix[:-1]
    return STICKERS.get(sticker_key)

@app.template_filter("reply_text")
def reply_text_filter(body):
    if get_reply_sticker(body) is None:
        return body
    return body.rsplit(REPLY_STICKER_MARKER, 1)[0]

@app.template_filter("reply_sticker")
def reply_sticker_filter(body):
    return get_reply_sticker(body)

def message_payload(message):
    return {
        "id": message.id,
        "sender_id": message.sender_id,
        "sender_name": message.sender.username,
        "recipient_id": message.recipient_id,
        "body": message.body,
        "media_name": message.media_name,
        "media_type": message.media_type,
        "media_url": url_for("uploaded_file", filename=message.media_name)
        if message.media_name and message.media_type != "sticker" else None,
        "sticker": sticker_emoji(message.media_name)
        if message.media_type == "sticker" else None,
        "created_at": message.created_at.strftime("%d %b %Y, %H:%M EAT"),
    }

@app.template_filter("pretty_time")
def pretty_time(value):
    if not value:
        return ""
    try:
        return value.strftime("%d %b %Y, %H:%M EAT")
    except Exception:
        return str(value)

@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("post_feed"))
    return render_template("index.html")



@app.route("/profile/<username>")
@login_required
def user_profile(username):
    profile_user = User.query.filter_by(username=username).first()

    if profile_user is None:
        abort(404)

    current_user_id = session["user_id"]
    is_own_profile = profile_user.id == current_user_id
    is_following = False
    if not is_own_profile:
        is_following = UserFollow.query.filter_by(
            follower_id=current_user_id,
            followed_id=profile_user.id
        ).first() is not None

    profile_posts = Post.query.filter_by(user_id=profile_user.id).order_by(
        Post.created_at.desc(),
        Post.id.desc()
    ).all()
    target_post_ids = {
        post.original_post_id if post.post_type == "repost" else post.id
        for post in profile_posts
        if post.post_type != "repost" or post.original_post_id is not None
    }
    post_replies, reply_counts = get_post_replies(target_post_ids)
    liked_post_ids = set()
    like_counts = {}
    repost_counts = {}
    reposted_post_ids = set()

    if target_post_ids:
        liked_post_ids = {
            post_id
            for (post_id,) in db.session.query(PostLike.post_id).filter(
                PostLike.user_id == current_user_id,
                PostLike.post_id.in_(target_post_ids)
            ).all()
        }
        like_counts = dict(
            db.session.query(PostLike.post_id, func.count(PostLike.id)).filter(
                PostLike.post_id.in_(target_post_ids)
            ).group_by(PostLike.post_id).all()
        )
        repost_counts = dict(
            db.session.query(Post.original_post_id, func.count(Post.id)).filter(
                Post.post_type == "repost",
                Post.original_post_id.in_(target_post_ids)
            ).group_by(Post.original_post_id).all()
        )
        reposted_post_ids = {
            original_id
            for (original_id,) in db.session.query(Post.original_post_id).filter(
                Post.user_id == current_user_id,
                Post.post_type == "repost",
                Post.original_post_id.in_(target_post_ids)
            ).all()
        }

    return render_template(
        "profile.html",
        profile_user=profile_user,
        is_own_profile=is_own_profile,
        is_following=is_following,
        follower_count=UserFollow.query.filter_by(followed_id=profile_user.id).count(),
        following_count=UserFollow.query.filter_by(follower_id=profile_user.id).count(),
        profile_posts=profile_posts,
        liked_post_ids=liked_post_ids,
        like_counts=like_counts,
        repost_counts=repost_counts,
        reposted_post_ids=reposted_post_ids,
        post_replies=post_replies,
        reply_counts=reply_counts,
        csrf_token=get_posts_csrf_token()
    )


@app.route("/profile/<username>/<list_type>")
@login_required
def user_follow_list(username, list_type):
    profile_user = User.query.filter_by(username=username).first()
    if profile_user is None:
        abort(404)

    if list_type == "followers":
        users = User.query.join(
            UserFollow, User.id == UserFollow.follower_id
        ).filter(
            UserFollow.followed_id == profile_user.id
        ).order_by(func.lower(User.username)).all()
        title = "Followers"
    elif list_type == "following":
        users = User.query.join(
            UserFollow, User.id == UserFollow.followed_id
        ).filter(
            UserFollow.follower_id == profile_user.id
        ).order_by(func.lower(User.username)).all()
        title = "Following"
    else:
        abort(404)

    return render_template(
        "follow_list.html",
        profile_user=profile_user,
        users=users,
        list_title=title
    )


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        display_name = request.form.get("display_name", "").strip()
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not display_name or len(display_name) > 50:
            flash("Display name is required and must be at most 50 characters.")
            return render_template("register.html")

        if len(username) < 3 or len(username) > 24 or not username.replace("_", "").isalnum():
            flash("Username must be 3–24 characters and use only letters, numbers, or underscores.")
            return render_template("register.html")

        if len(password) < 8:
            flash("Use a password with at least 8 characters.")
            return render_template("register.html")

        try:
            db.session.add(
                User(
                    display_name=display_name,
                    username=username,
                    password_hash=generate_password_hash(password)
                )
            )
            db.session.commit()
            flash("Registration successful. You can now log in.")
            return redirect(url_for("login"))

        except IntegrityError:
            db.session.rollback()
            flash("That username is already taken.")

    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter(func.lower(User.username) == username.lower()).first()
        if user and check_password_hash(user.password_hash, password):
            session.clear()
            session["user_id"] = user.id
            session["username"] = user.username
            return redirect(url_for("post_feed"))
        flash("Incorrect username or password.")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("You have logged out.")
    return redirect(url_for("index"))


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    user = db.session.get(User, session["user_id"])
    if user is None:
        session.clear()
        return redirect(url_for("login"))

    # Generate a CSRF token for the settings forms.
    if "_settings_csrf" not in session:
        session["_settings_csrf"] = secrets.token_urlsafe(32)

    if request.method == "POST":
        submitted_token = request.form.get("csrf_token", "")
        stored_token = session.get("_settings_csrf", "")

        if not stored_token or not hmac.compare_digest(
            submitted_token, stored_token
        ):
            abort(400)

        action = request.form.get("action", "")

        if action == "update_profile":
            display_name = request.form.get("display_name", "").strip()
            bio = request.form.get("bio", "").strip()
            location = request.form.get("location", "").strip()
            website = request.form.get("website", "").strip()
            image = request.files.get("profile_image")

            if len(display_name) > 50 or len(bio) > 280 or len(location) > 100 or len(website) > 255:
                flash("One or more profile fields exceed their character limit.")
                return redirect(url_for("settings", _anchor="profile"))

            if website:
                parsed_website = urlsplit(website)
                if parsed_website.scheme not in {"http", "https"} or not parsed_website.netloc:
                    flash("Website must be a valid http or https URL.")
                    return redirect(url_for("settings", _anchor="profile"))

            image_data = None
            new_image_path = None
            new_image_name = None
            if image and image.filename:
                safe_name = secure_filename(image.filename)
                extension = safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else ""
                if not safe_name or extension not in PROFILE_IMAGE_EXTENSIONS:
                    flash("Choose a PNG, JPG, GIF, or WEBP profile photo.")
                    return redirect(url_for("settings", _anchor="profile"))

                image_data = image.stream.read(PROFILE_IMAGE_MAX_BYTES + 1)
                if len(image_data) > PROFILE_IMAGE_MAX_BYTES:
                    flash("Profile photos must be 5 MB or smaller.")
                    return redirect(url_for("settings", _anchor="profile"))

                new_image_name = f"profile_{user.id}_{secrets.token_hex(12)}.{extension}"
                new_image_path = UPLOAD_DIR / new_image_name

            previous_image = user.profile_image
            user.display_name = display_name or None
            user.bio = bio
            user.location = location
            user.website = website
            if new_image_name:
                user.profile_image = new_image_name
            elif request.form.get("remove_profile_image") == "yes":
                user.profile_image = None

            try:
                if new_image_path is not None:
                    new_image_path.write_bytes(image_data)
                db.session.commit()
            except Exception:
                db.session.rollback()
                if new_image_path is not None:
                    new_image_path.unlink(missing_ok=True)
                app.logger.exception("Profile update failed for user %s", user.id)
                flash("Profile update failed. Please try again.")
                return redirect(url_for("settings", _anchor="profile"))

            if previous_image and previous_image != user.profile_image:
                if Path(previous_image).name == previous_image:
                    try:
                        (UPLOAD_DIR / previous_image).unlink(missing_ok=True)
                    except OSError:
                        app.logger.warning("Could not remove replaced profile photo: %s", previous_image)

            flash("Your profile has been updated.")
            return redirect(url_for("user_profile", username=user.username))

        elif action == "change_password":
            current_password = request.form.get("current_password", "")
            new_password = request.form.get("new_password", "")
            confirm_password = request.form.get("confirm_password", "")

            if not check_password_hash(user.password_hash, current_password):
                flash("Your current password is incorrect.")
            elif len(new_password) < 8:
                flash("Your new password must be at least 8 characters.")
            elif new_password != confirm_password:
                flash("The new passwords do not match.")
            elif check_password_hash(user.password_hash, new_password):
                flash("Choose a password different from your current one.")
            else:
                user.password_hash = generate_password_hash(new_password)
                db.session.commit()
                session.clear()
                flash("Password changed successfully. Please log in again.")
                return redirect(url_for("login"))

        elif action == "delete_account":
            current_password = request.form.get("delete_password", "")
            confirmation = request.form.get("delete_confirmation", "")

            if not check_password_hash(user.password_hash, current_password):
                flash("Your password is incorrect. Account not deleted.")
            elif confirmation != "Yes":
                flash('You must confirm account deletion to delete account.')
            else:
                messages = Message.query.filter(
                    or_(
                        Message.sender_id == user.id,
                        Message.recipient_id == user.id
                    )
                ).all()

                media_paths = [
                    UPLOAD_DIR / Path(message.media_name).name
                    for message in messages
                    if message.media_name
                    and Path(message.media_name).name == message.media_name
                ]
                media_paths.extend(
                    UPLOAD_DIR / filename
                    for filename in (user.profile_image, user.banner_image)
                    if filename and Path(filename).name == filename
                )
                post_media = PostMedia.query.join(Post).filter(Post.user_id == user.id).all()
                media_paths.extend(
                    UPLOAD_DIR / item.media_name
                    for item in post_media
                    if Path(item.media_name).name == item.media_name
                )

                try:
                    user_likes = PostLike.query.filter_by(user_id=user.id).all()
                    user_posts = Post.query.filter_by(user_id=user.id).all()

                    for like in user_likes:
                        db.session.delete(like)

                    for post in user_posts:
                        db.session.delete(post)

                    for message in messages:
                        db.session.delete(message)

                    db.session.delete(user)
                    db.session.commit()

                except Exception:
                    db.session.rollback()
                    app.logger.exception("Account deletion failed")
                    flash("Account deletion failed. Please try again.")
                else:
                    session.clear()

                    # Remove media associated with the deleted messages.
                    for path in media_paths:
                        try:
                            path.unlink(missing_ok=True)
                        except OSError:
                            app.logger.warning(
                                "Could not remove deleted account media: %s",
                                path.name
                            )

                    flash("Your account and associated messages have been deleted.")
                    return redirect(url_for("index"))

        return redirect(url_for("settings"))

    return render_template(
        "settings.html",
        csrf_token=session["_settings_csrf"],
        user=user
    )


@app.route("/inbox")
@login_required
def inbox():
    current_id = session["user_id"]
    conversation_user_ids = db.session.query(
        Message.sender_id.label("user_id")
    ).filter(
        Message.recipient_id == current_id
    ).union(
        db.session.query(Message.recipient_id.label("user_id")).filter(
            Message.sender_id == current_id
        )
    ).subquery()
    users = User.query.join(
        conversation_user_ids,
        User.id == conversation_user_ids.c.user_id
    ).order_by(func.lower(User.username)).all()
    previews = {}
    for user in users:
        last = Message.query.filter(or_(
            and_(Message.sender_id == current_id, Message.recipient_id == user.id),
            and_(Message.sender_id == user.id, Message.recipient_id == current_id)
        )).order_by(Message.id.desc()).first()
        previews[user.id] = last
    return render_template("inbox.html", users=users, previews=previews)

def find_users_by_username_prefix(prefix, current_user_id, limit):
    escaped_prefix = (
        prefix.lower()
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
    return User.query.filter(
        User.id != current_user_id,
        func.lower(User.username).like(f"{escaped_prefix}%", escape="\\")
    ).order_by(
        func.lower(User.username)
    ).limit(limit).all()


@app.route("/search")
@login_required
def user_search():
    query = request.args.get("q", "").strip()[:24]
    users = find_users_by_username_prefix(query, session["user_id"], 50) if query else []
    return render_template("search.html", query=query, users=users)


@app.route("/search/suggestions")
@login_required
def user_search_suggestions():
    query = request.args.get("q", "").strip()[:24]
    if not query:
        return jsonify(users=[])

    users = find_users_by_username_prefix(query, session["user_id"], 8)
    return jsonify(users=[
        {
            "username": user.username,
            "is_verified": user.is_verified,
            "url": url_for("user_profile", username=user.username)
        }
        for user in users
    ])


@app.route("/chat/<int:user_id>", methods=["GET", "POST"])
@login_required
def chat(user_id):
    current_id = session["user_id"]
    if user_id == current_id:
        abort(400)
    other = db.session.get(User, user_id)
    if not other:
        abort(404)

    if request.method == "POST":
        body = request.form.get("body", "").strip()
        media = request.files.get("media")
        sticker = request.form.get("sticker", "").strip()
        media_name = None
        media_type = None
        if sticker and sticker not in STICKERS:
            if request.accept_mimetypes.best == "application/json":
                return jsonify(ok=False, error="Choose a valid sticker."), 400
            flash("Choose a valid sticker.")
            return redirect(url_for("chat", user_id=user_id))
        if sticker and media and media.filename:
            if request.accept_mimetypes.best == "application/json":
                return jsonify(ok=False, error="Send a sticker or attach a file, not both."), 400
            flash("Send a sticker or attach a file, not both.")
            return redirect(url_for("chat", user_id=user_id))
        if sticker:
            media_name = sticker_media_name(sticker)
            media_type = "sticker"
        if media and media.filename:
            safe_name = secure_filename(media.filename)
            if not safe_name or not allowed_file(safe_name):
                if request.accept_mimetypes.best == "application/json":
                    return jsonify(ok=False, error="Unsupported file type."), 400
                flash("Unsupported file. Use PNG, JPG, GIF, WEBP, MP4, MOV, WEBM, or M4V.")
                return redirect(url_for("chat", user_id=user_id))
            ext = safe_name.rsplit(".", 1)[1].lower()
            media_name = f"{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}_{current_id}.{ext}"
            media_type = "video" if ext in VIDEO_EXTENSIONS else "image"
            media.save(UPLOAD_DIR / media_name)
        if not body and not media_name:
            if request.accept_mimetypes.best == "application/json":
                return jsonify(ok=False, error="Write a message or attach a file or sticker."), 400
            return redirect(url_for("chat", user_id=user_id))

        message = Message(sender_id=current_id, recipient_id=user_id, body=body,
                          media_name=media_name, media_type=media_type)
        db.session.add(message)
        db.session.commit()
        payload = message_payload(message)
        socketio.emit("new_message", payload, room=chat_room(current_id, user_id))
        if request.accept_mimetypes.best == "application/json":
            return jsonify(ok=True, message_id=message.id)
        return redirect(url_for("chat", user_id=user_id))

    messages = Message.query.filter(or_(
        and_(Message.sender_id == current_id, Message.recipient_id == user_id),
        and_(Message.sender_id == user_id, Message.recipient_id == current_id)
    )).order_by(Message.id.asc()).all()
    return render_template(
        "chat.html",
        other=other,
        messages=messages,
        delete_csrf_token=get_message_delete_csrf_token()
    )

def get_message_delete_csrf_token():
    if "_message_delete_csrf" not in session:
        session["_message_delete_csrf"] = secrets.token_urlsafe(32)
    return session["_message_delete_csrf"]

@app.route("/messages/<int:message_id>/delete", methods=["POST"])
@login_required
def delete_message(message_id):
    """Delete a sent message and notify participants in the open chat."""
    submitted_token = request.form.get("csrf_token", "")
    stored_token = session.get("_message_delete_csrf", "")
    if not stored_token or not hmac.compare_digest(submitted_token, stored_token):
        abort(400, description="Invalid or missing CSRF token.")

    message = db.session.get(Message, message_id)
    if message is None:
        abort(404)

    current_id = session["user_id"]
    if message.sender_id != current_id:
        abort(403)

    recipient_id = message.recipient_id
    media_path = None
    if (message.media_name and message.media_type != "sticker"
            and Path(message.media_name).name == message.media_name):
        media_path = UPLOAD_DIR / message.media_name

    db.session.delete(message)
    db.session.commit()

    if media_path:
        try:
            media_path.unlink(missing_ok=True)
        except OSError:
            app.logger.warning("Could not remove deleted message media: %s", media_path.name)

    socketio.emit(
        "message_deleted",
        {"message_id": message_id},
        room=chat_room(current_id, recipient_id)
    )

    if request.accept_mimetypes.best == "application/json":
        return jsonify(ok=True, message_id=message_id)
    return redirect(url_for("chat", user_id=recipient_id))

@socketio.on("join_chat")
def on_join_chat(data):
    if "user_id" not in session:
        return
    try:
        other_id = int((data or {}).get("other_user_id"))
    except (TypeError, ValueError):
        return
    current_id = int(session["user_id"])
    if other_id == current_id or db.session.get(User, other_id) is None:
        return
    join_room(chat_room(current_id, other_id))

@app.route("/uploads/<path:filename>")
@login_required
def uploaded_file(filename):
    # Authentication required; production should also verify conversation membership.
    return send_from_directory(UPLOAD_DIR, filename)


# ============================================================
# PUBLIC POSTS: FEED, CREATE, LIKE, REPOST AND QUOTE
# ============================================================

def get_posts_csrf_token():
    """Create or retrieve the CSRF token for post forms."""
    if "_posts_csrf" not in session:
        session["_posts_csrf"] = secrets.token_urlsafe(32)

    return session["_posts_csrf"]


def validate_posts_csrf():
    """Reject post form submissions with a missing or invalid token."""
    submitted = request.form.get("csrf_token", "")
    stored = session.get("_posts_csrf", "")

    if not stored or not hmac.compare_digest(submitted, stored):
        abort(400, description="Invalid or missing CSRF token.")


@app.route("/users/<int:user_id>/follow", methods=["POST"])
@login_required
def toggle_follow(user_id):
    validate_posts_csrf()

    current_user_id = session["user_id"]
    if current_user_id == user_id:
        abort(400, description="You cannot follow your own account.")

    followed_user = db.session.get(User, user_id)
    if followed_user is None:
        abort(404)

    follow = UserFollow.query.filter_by(
        follower_id=current_user_id,
        followed_id=user_id
    ).first()

    if follow:
        db.session.delete(follow)
        is_following = False
    else:
        db.session.add(UserFollow(follower_id=current_user_id, followed_id=user_id))
        is_following = True

    db.session.commit()
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify(
            success=True,
            is_following=is_following,
            follower_count=UserFollow.query.filter_by(followed_id=user_id).count()
        )

    flash(f"You are now following @{followed_user.username}." if is_following else f"You unfollowed @{followed_user.username}.")
    return redirect(url_for("user_profile", username=followed_user.username))


def get_original_post(post):
    """Return the original post when the supplied post is a repost."""
    if post.post_type == "repost":
        return post.original_post

    return post


def get_post_replies(post_ids):
    """Return replies grouped by post and their counts for a set of posts."""
    replies_by_post = {post_id: [] for post_id in post_ids}
    reply_counts = {}
    if post_ids:
        replies = PostReply.query.filter(
            PostReply.post_id.in_(post_ids)
        ).order_by(
            PostReply.created_at.asc(),
            PostReply.id.asc()
        ).all()
        for reply in replies:
            replies_by_post[reply.post_id].append(reply)
            reply_counts[reply.post_id] = reply_counts.get(reply.post_id, 0) + 1

    return replies_by_post, reply_counts


def post_action_redirect():
    """Return profile actions to the profile supplied by the form, if valid."""
    username = request.form.get("return_to_username", "").strip()
    if username:
        profile_user = User.query.filter_by(username=username).first()
        if profile_user is not None:
            return redirect(url_for("user_profile", username=profile_user.username))

    return redirect(url_for("post_feed"))


@app.route("/posts")
@login_required
def post_feed():
    """Display posts to authenticated users only."""
    current_user_id = session["user_id"]
    current_user = db.session.get(User, current_user_id)

    posts = Post.query.order_by(
        Post.created_at.desc(),
        Post.id.desc()
    ).all()

    # Find which posts the current user has liked.
    liked_post_ids = {
        post_id
        for (post_id,) in db.session.query(
            PostLike.post_id
        ).filter(
            PostLike.user_id == current_user_id
        ).all()
    }

    # Count likes for every post.
    like_counts = dict(
        db.session.query(
            PostLike.post_id,
            func.count(PostLike.id)
        ).group_by(
            PostLike.post_id
        ).all()
    )

    # Count reposts of each original post.
    repost_counts = dict(
        db.session.query(
            Post.original_post_id,
            func.count(Post.id)
        ).filter(
            Post.post_type == "repost",
            Post.original_post_id.isnot(None)
        ).group_by(
            Post.original_post_id
        ).all()
    )

    # Find posts that this user has already reposted.
    reposted_post_ids = {
        original_id
        for (original_id,) in db.session.query(
            Post.original_post_id
        ).filter(
            Post.user_id == current_user_id,
            Post.post_type == "repost",
            Post.original_post_id.isnot(None)
        ).all()
    }
    post_ids = {
        post.original_post_id if post.post_type == "repost" else post.id
        for post in posts
        if post.post_type != "repost" or post.original_post_id is not None
    }
    post_replies, reply_counts = get_post_replies(post_ids)

    return render_template(
        "post_feed.html",
        posts=posts,
        liked_post_ids=liked_post_ids,
        like_counts=like_counts,
        repost_counts=repost_counts,
        reposted_post_ids=reposted_post_ids,
        post_replies=post_replies,
        reply_counts=reply_counts,
        current_user=current_user,
        is_admin=bool(current_user and current_user.is_admin),
        csrf_token=get_posts_csrf_token()
    )


@app.route("/posts/create", methods=["POST"])
@login_required
def create_post():
    """Publish a text post or a post with one attachment or sticker."""
    validate_posts_csrf()

    body = request.form.get("body", "").strip()
    media = request.files.get("media")
    sticker = request.form.get("sticker", "").strip()
    media_data = None
    media_name = None
    media_type = None

    if sticker and sticker not in STICKERS:
        flash("Choose a valid sticker.")
        return redirect(url_for("post_feed"))
    if sticker and media and media.filename:
        flash("Choose a sticker or an image/video attachment, not both.")
        return redirect(url_for("post_feed"))
    if sticker:
        media_name = sticker_media_name(sticker)
        media_type = "sticker"

    if media and media.filename:
        safe_name = secure_filename(media.filename)
        extension = safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else ""
        if not safe_name or not allowed_file(safe_name):
            flash("Choose an image or video in a supported format.")
            return redirect(url_for("post_feed"))

        media_data = media.stream.read(POST_MEDIA_MAX_BYTES + 1)
        if len(media_data) > POST_MEDIA_MAX_BYTES:
            flash("Post attachments must be 25 MB or smaller.")
            return redirect(url_for("post_feed"))

        media_name = f"post_{session['user_id']}_{secrets.token_hex(12)}.{extension}"
        media_type = "video" if extension in VIDEO_EXTENSIONS else "image"

    if not body and not media_name:
        flash("Write a post or attach an image, video, or sticker.")
        return redirect(url_for("post_feed"))

    if len(body) > 5000:
        flash("Your post cannot exceed 5,000 characters.")
        return redirect(url_for("post_feed"))

    post = Post(user_id=session["user_id"], body=body, post_type="post")
    media_path = UPLOAD_DIR / media_name if media_name else None
    try:
        db.session.add(post)
        if media_name and media_type != "sticker":
            db.session.flush()
            media_path.write_bytes(media_data)
            post.media = PostMedia(
                media_name=media_name,
                media_type=media_type
            )
        elif media_name:
            post.media = PostMedia(
                media_name=media_name,
                media_type=media_type
            )
            db.session.add(post.media)
        db.session.commit()
    except Exception:
        db.session.rollback()
        if media_path:
            media_path.unlink(missing_ok=True)
        app.logger.exception("Post creation failed for user %s", session["user_id"])
        flash("Your post could not be published. Please try again.")
        return redirect(url_for("post_feed"))

    flash("Your post has been published.")
    return redirect(url_for("post_feed"))


@app.route("/posts/<int:post_id>/like", methods=["POST"])
@login_required
def toggle_like(post_id):
    """Like or unlike a post."""
    validate_posts_csrf()

    post = db.session.get(Post, post_id)

    if post is None:
        abort(404)

    target = get_original_post(post)

    if target is None:
        abort(404)

    existing_like = PostLike.query.filter_by(
        user_id=session["user_id"],
        post_id=target.id
    ).first()

    liked = False

    if existing_like:
        db.session.delete(existing_like)
        flash("Like removed.")
    else:
        db.session.add(
            PostLike(
                user_id=session["user_id"],
                post_id=target.id
            )
        )
        liked = True
        flash("Post liked.")

    db.session.commit()

    like_count = PostLike.query.filter_by(post_id=target.id).count()

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({
            "success": True,
            "liked": liked,
            "like_count": like_count,
            "post_id": target.id
        })

    return post_action_redirect()


@app.route("/posts/<int:post_id>/replies", methods=["POST"])
@login_required
def create_post_reply(post_id):
    """Reply to a post as the authenticated user."""
    validate_posts_csrf()

    post = db.session.get(Post, post_id)
    if post is None:
        abort(404)

    target = get_original_post(post)
    if target is None:
        abort(404)

    body = request.form.get("body", "").strip()
    sticker = request.form.get("sticker", "").strip()
    if sticker and sticker not in STICKERS:
        flash("Choose a valid sticker.")
        return post_action_redirect()
    if not body and not sticker:
        flash("Write a reply or choose a sticker before publishing it.")
        return post_action_redirect()

    if len(body) > 5000:
        flash("Your reply cannot exceed 5,000 characters.")
        return post_action_redirect()

    if sticker:
        body = encode_reply_sticker(body, sticker)

    db.session.add(PostReply(
        post_id=target.id,
        user_id=session["user_id"],
        body=body
    ))
    db.session.commit()

    flash("Your reply has been published.")
    return post_action_redirect()


@app.route("/posts/<int:post_id>/repost", methods=["POST"])
@login_required
def repost_post(post_id):
    """Repost an existing post without adding commentary."""
    validate_posts_csrf()

    post = db.session.get(Post, post_id)

    if post is None:
        abort(404)

    original = get_original_post(post)

    if original is None:
        abort(404)

    current_user_id = session["user_id"]

    existing_repost = Post.query.filter_by(
        user_id=current_user_id,
        original_post_id=original.id,
        post_type="repost"
    ).first()

    if existing_repost:
        flash("You have already reposted this post.")
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({
                "success": False,
                "message": "You have already reposted this post.",
                "reposted": True,
                "repost_count": Post.query.filter_by(
                    original_post_id=original.id,
                    post_type="repost"
                ).count()
            })
        return post_action_redirect()

    repost = Post(
        user_id=current_user_id,
        body="",
        post_type="repost",
        original_post_id=original.id
    )

    db.session.add(repost)
    db.session.commit()

    flash("Post reposted successfully.")

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({
            "success": True,
            "message": "Post reposted successfully.",
            "reposted": True,
            "repost_count": Post.query.filter_by(
                original_post_id=original.id,
                post_type="repost"
            ).count(),
            "post": {
                "id": repost.id,
                "user_id": current_user_id,
                "author_username": User.query.get(current_user_id).username,
                "author_display_name": repost.author.display_name,
                "author_profile_image": url_for("uploaded_file", filename=repost.author.profile_image)
                if repost.author.profile_image else None,
                "author_verified": repost.author.is_verified,
                "body": repost.body,
                "post_type": repost.post_type,
                "created_at": repost.created_at.isoformat(),
                "original_post": {
                    "id": original.id,
                    "body": original.body,
                    "author_username": original.author.username,
                    "author_display_name": original.author.display_name,
                    "author_verified": original.author.is_verified,
                    "media_url": url_for("uploaded_file", filename=original.media.media_name)
                    if original.media and original.media.media_type != "sticker" else None,
                    "media_type": original.media.media_type if original.media else None,
                    "sticker": sticker_emoji(original.media.media_name)
                    if original.media and original.media.media_type == "sticker" else None,
                    "created_at": original.created_at.isoformat()
                }
            }
        })

    return post_action_redirect()


@app.route("/posts/<int:post_id>/quote", methods=["POST"])
@login_required
def quote_post(post_id):
    """Publish a quote post with commentary and an original post."""
    validate_posts_csrf()

    post = db.session.get(Post, post_id)

    if post is None:
        abort(404)

    original = get_original_post(post)

    if original is None:
        abort(404)

    body = request.form.get("body", "").strip()
    sticker = request.form.get("sticker", "").strip()
    if sticker and sticker not in STICKERS:
        flash("Choose a valid sticker.")
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "message": "Choose a valid sticker."}), 400
        return post_action_redirect()

    if not body and not sticker:
        flash("Add your thoughts or choose a sticker before publishing a quote.")
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "message": "Add your thoughts or choose a sticker before publishing a quote."}), 400
        return post_action_redirect()

    if len(body) > 5000:
        flash("Your commentary cannot exceed 5,000 characters.")
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "message": "Your commentary cannot exceed 5,000 characters."}), 400
        return post_action_redirect()

    media = None
    if sticker:
        media = PostMedia(
            media_name=sticker_media_name(sticker),
            media_type="sticker"
        )

    quote = Post(
        user_id=session["user_id"],
        body=body,
        post_type="quote",
        original_post_id=original.id
    )

    db.session.add(quote)
    if media:
        quote.media = media
    db.session.commit()

    flash("Your quote has been published.")

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({
            "success": True,
            "message": "Your quote has been published.",
            "post": {
                "id": quote.id,
                "user_id": session["user_id"],
                "author_username": User.query.get(session["user_id"]).username,
                "author_display_name": quote.author.display_name,
                "author_profile_image": url_for("uploaded_file", filename=quote.author.profile_image)
                if quote.author.profile_image else None,
                "author_verified": quote.author.is_verified,
                "body": quote.body,
                "post_type": quote.post_type,
                "media_type": quote.media.media_type if quote.media else None,
                "sticker": sticker_emoji(quote.media.media_name)
                if quote.media and quote.media.media_type == "sticker" else None,
                "created_at": quote.created_at.isoformat(),
                "original_post": {
                    "id": original.id,
                    "body": original.body,
                    "author_username": original.author.username,
                    "author_display_name": original.author.display_name,
                    "author_verified": original.author.is_verified,
                    "media_url": url_for("uploaded_file", filename=original.media.media_name)
                    if original.media and original.media.media_type != "sticker" else None,
                    "media_type": original.media.media_type if original.media else None,
                    "sticker": sticker_emoji(original.media.media_name)
                    if original.media and original.media.media_type == "sticker" else None,
                    "created_at": original.created_at.isoformat()
                }
            }
        })

    return post_action_redirect()


@app.route("/posts/<int:post_id>/delete", methods=["POST"])
@login_required
def delete_post(post_id):
    """Delete a post if it belongs to the logged-in user."""
    validate_posts_csrf()

    post = db.session.get(Post, post_id)

    if post is None:
        abort(404)

    current_user = db.session.get(User, session["user_id"])
    if post.user_id != session["user_id"] and not (current_user and current_user.is_admin):
        abort(403)

    media_path = None
    if (post.media and post.media.media_type != "sticker"
            and Path(post.media.media_name).name == post.media.media_name):
        media_path = UPLOAD_DIR / post.media.media_name

    try:
        db.session.delete(post)
        db.session.commit()
        flash("Post deleted.")
    except Exception:
        db.session.rollback()
        app.logger.exception("Failed to delete post %s", post_id)
        flash("We could not delete that post. Please try again.")
    else:
        if media_path:
            try:
                media_path.unlink(missing_ok=True)
            except OSError:
                app.logger.warning("Could not remove deleted post media: %s", media_path.name)

    return redirect(url_for("post_feed"))


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        user = db.session.get(User, session["user_id"])

        if user is None or not user.is_admin:
            abort(403)

        return view(*args, **kwargs)

    return wrapped


@app.route("/admin/users")
@admin_required
def admin_users():
    if "_admin_csrf" not in session:
        session["_admin_csrf"] = secrets.token_urlsafe(32)

    users = User.query.order_by(
        User.username.asc()
    ).all()

    return render_template(
        "admin_users.html",
        users=users,
        csrf_token=session["_admin_csrf"]
    )


@app.route(
    "/admin/users/<int:user_id>/verification",
    methods=["POST"]
)
@admin_required
def toggle_verification(user_id):
    submitted = request.form.get("csrf_token", "")
    stored = session.get("_admin_csrf", "")

    if not stored or not hmac.compare_digest(submitted, stored):
        abort(400, description="Invalid or missing CSRF token.")

    user = db.session.get(User, user_id)

    if user is None:
        abort(404)

    user.is_verified = not user.is_verified
    db.session.commit()

    if user.is_verified:
        flash(f"@{user.username} is now verified.")
    else:
        flash(f"Verification removed from @{user.username}.")

    return redirect(url_for("admin_users"))


@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def admin_delete_user(user_id):
    submitted = request.form.get("csrf_token", "")
    stored = session.get("_admin_csrf", "")
    if not stored or not hmac.compare_digest(submitted, stored):
        abort(400, description="Invalid or missing CSRF token.")

    user = db.session.get(User, user_id)
    if user is None:
        abort(404)

    username = user.username
    deleting_self = user.id == session["user_id"]
    messages = Message.query.filter(
        or_(Message.sender_id == user.id, Message.recipient_id == user.id)
    ).all()
    media_paths = [
        UPLOAD_DIR / Path(message.media_name).name
        for message in messages
        if message.media_name and Path(message.media_name).name == message.media_name
    ]
    media_paths.extend(
        UPLOAD_DIR / filename
        for filename in (user.profile_image, user.banner_image)
        if filename and Path(filename).name == filename
    )

    try:
        user_posts = Post.query.filter_by(user_id=user.id).all()
        post_ids = [post.id for post in user_posts]
        post_media = PostMedia.query.filter(PostMedia.post_id.in_(post_ids)).all()
        media_paths.extend(
            UPLOAD_DIR / item.media_name
            for item in post_media
            if Path(item.media_name).name == item.media_name
        )
        user_likes = PostLike.query.filter(
            or_(
                PostLike.user_id == user.id,
                PostLike.post_id.in_(post_ids)
            )
        ).all()

        for like in user_likes:
            db.session.delete(like)
        for post in user_posts:
            db.session.delete(post)
        for message in messages:
            db.session.delete(message)
        db.session.delete(user)
        db.session.commit()
    except Exception:
        db.session.rollback()
        app.logger.exception("Admin account deletion failed for user %s", user_id)
        flash("Account removal failed. Please try again.")
        return redirect(url_for("admin_users"))

    for path in media_paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            app.logger.warning("Could not remove deleted account media: %s", path.name)

    if deleting_self:
        session.clear()
        flash("Your administrator account and associated data were removed.")
        return redirect(url_for("index"))

    flash(f"@{username} and associated data were removed.")
    return redirect(url_for("admin_users"))



@app.errorhandler(413)
def too_large(_error):
    if request.accept_mimetypes.best == "application/json":
        return jsonify(ok=False, error="Upload too large. Maximum request size is 100 MB."), 413
    flash("That upload is too large. The maximum request size is 100 MB.")
    return redirect(request.referrer or url_for("inbox"))

if __name__ == "__main__":
    # Local development only. Do not expose the debug server to the public internet.
    socketio.run(app, debug=False, allow_unsafe_werkzeug=True)
