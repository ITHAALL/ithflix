import os
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
from dotenv import load_dotenv
from bson.objectid import ObjectId
from datetime import datetime
from functools import wraps
import requests

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "ithub_secret_key_2026")

# ============================================================================
# CONNEXION MONGODB
# ============================================================================

client = MongoClient(os.getenv("MONGODB_URI"), server_api=ServerApi('1'))
db_chat  = client["ithchat"]
db_flix  = client["ithflix"]

accounts          = db_chat["accounts"]
movies_collection = db_flix["movies"]
animes_collection = db_flix["animes"]


# ============================================================================
# HELPERS
# ============================================================================

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        if not session.get("is_admin"):
            return redirect(url_for("flix_index"))
        return f(*args, **kwargs)
    return decorated


def send_discord_embed(webhook_url, title, description, color=0x007bff):
    if not webhook_url:
        return False
    payload = {
        "embeds": [{
            "title": title,
            "description": description,
            "color": color,
            "footer": {"text": "🔐 ITH-Hub Logs"},
            "timestamp": datetime.utcnow().isoformat()
        }]
    }
    try:
        response = requests.post(webhook_url, json=payload, timeout=5)
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"Webhook error: {e}")
        return False


# ============================================================================
# FILTRES JINJA
# ============================================================================

@app.template_filter("total_episodes")
def total_episodes_filter(seasons):
    return sum(s.get("total_episodes", 0) for s in seasons)


# ============================================================================
# AUTH
# ============================================================================

@app.route("/", methods=["GET", "POST"])
def login():
    if "user" in session:
        return redirect(url_for("flix_index"))

    if request.method == "POST":
        user_input = request.form.get("user")
        pass_input = request.form.get("pass")

        user_data = accounts.find_one({"user": user_input})
        if user_data and pass_input == user_data.get("password"):
            session["user"]     = user_data.get("user")
            session["is_admin"] = user_data.get("admin", False)
            send_discord_embed(
                os.getenv("WEBHOOK_LOGS"),
                "🔗 Nouvelle connexion",
                f"`{user_input}` s'est connecté à son compte ITH-Hub.",
                color=0x00c853
            )
            return redirect(url_for("flix_index"))

        return render_template("login.html", erreur="Identifiants incorrects.")

    return render_template("login.html")


@app.route("/logout")
def logout():
    if "user" in session:
        send_discord_embed(
            os.getenv("WEBHOOK_LOGS"),
            "⛔ Déconnexion",
            f"`{session['user']}` s'est déconnecté de son compte ITH-Hub.",
            color=0xe53935
        )
    session.clear()
    return redirect(url_for("login"))


# ============================================================================
# FILMS
# ============================================================================

@app.route("/films")
@login_required
def flix_index():
    all_movies = list(movies_collection.find({}).sort("_id", -1))
    return render_template("films/index.html", movies=all_movies)


@app.route("/films/watch/<movie_id>")
@login_required
def watch_movie(movie_id):
    movie = movies_collection.find_one({"_id": ObjectId(movie_id)})
    if not movie:
        return render_template("404.html"), 404
    return render_template("films/watch.html", movie=movie, video_url=movie.get("source"))


@app.route("/films/ajouter", methods=["GET", "POST"])
@admin_required
def add_movie():
    if request.method == "POST":
        movies_collection.insert_one({
            "title":  request.form.get("title"),
            "cover":  request.form.get("cover"),
            "source": request.form.get("source"),
        })
        return redirect(url_for("flix_index"))
    return render_template("films/add_movie.html")


@app.route("/films/modifier/<id>", methods=["GET", "POST"])
@admin_required
def edit_movie(id):
    if request.method == "POST":
        movies_collection.update_one(
            {"_id": ObjectId(id)},
            {"$set": {
                "title":  request.form.get("title"),
                "source": request.form.get("source"),
                "cover":  request.form.get("cover"),
            }}
        )
        return redirect(url_for("flix_index"))
    movie = movies_collection.find_one({"_id": ObjectId(id)})
    return render_template("films/edit_movie.html", movie=movie)


@app.route("/films/supprimer/<id>")
@admin_required
def delete_movie(id):
    movies_collection.delete_one({"_id": ObjectId(id)})
    return redirect(url_for("flix_index"))


@app.route("/films/recherche")
@login_required
def film_search():
    query = request.args.get("q", "")
    movies = []
    if query:
        movies = list(movies_collection.find(
            {"title": {"$regex": query, "$options": "i"}}
        ))
    return render_template("films/search.html", query=query, movies=movies)


@app.route("/api/suggestion", methods=["POST"])
@login_required
def send_suggestion():
    """Envoie une suggestion utilisateur vers le webhook Discord dédié."""
    data        = request.get_json(silent=True) or {}
    contenu     = data.get("contenu", "").strip()
    section     = data.get("section", "?")   # "film" ou "anime"
    titre_ref   = data.get("titre_ref", "")  # titre du film/anime en cours
    user        = session.get("user", "inconnu")

    if not contenu:
        return jsonify({"ok": False, "error": "Suggestion vide"}), 400

    webhook_url = os.getenv("WEBHOOK_SUGGESTIONS") or os.getenv("WEBHOOK_LOGS")
    if not webhook_url:
        return jsonify({"ok": False, "error": "Webhook non configuré"}), 500

    description = (
        f"**Utilisateur :** `{user}`\n"
        f"**Section :** {section}\n"
        + (f"**Titre en cours :** {titre_ref}\n" if titre_ref else "")
        + f"\n💬 {contenu}"
    )

    ok = send_discord_embed(
        webhook_url,
        title="💡 Nouvelle suggestion",
        description=description,
        color=0xffd700
    )
    return jsonify({"ok": ok})


# ============================================================================
# ANIMES
# ============================================================================

@app.route("/animes")
@login_required
def animes_index():
    animes = list(animes_collection.find(
        {},
        {"name": 1, "cover_url": 1, "genres": 1, "status": 1, "seasons": 1}
    ).sort("updated_date", -1))
    return render_template("animes/index.html", animes=animes)


@app.route("/animes/<anime_name>")
@login_required
def anime_detail(anime_name):
    anime = animes_collection.find_one({"name": anime_name})
    if not anime:
        return render_template("404.html"), 404
    return render_template("animes/detail.html", anime=anime)


@app.route("/animes/<anime_name>/s<int:season_num>/e<int:episode_num>")
@login_required
def watch_anime(anime_name, season_num, episode_num):
    anime = animes_collection.find_one({"name": anime_name})
    if not anime:
        return render_template("404.html"), 404

    season = next((s for s in anime.get("seasons", []) if s["season_number"] == season_num), None)
    if not season:
        return render_template("404.html"), 404

    episode = next((ep for ep in season.get("episodes", []) if ep["episode_number"] == episode_num), None)
    if not episode:
        return render_template("404.html"), 404

    anime_info = animes_collection.find_one(
        {"name": anime_name},
        {"name": 1, "cover_url": 1, "seasons": 1}
    )
    return render_template(
        "animes/watch.html",
        anime=anime_info,
        season_num=season_num,
        episode_num=episode_num,
        episode=episode,
        all_seasons=anime_info.get("seasons", [])
    )


@app.route("/animes/genre/<genre_name>")
@login_required
def anime_genre(genre_name):
    animes = list(animes_collection.find(
        {"genres": genre_name},
        {"name": 1, "cover_url": 1, "genres": 1, "status": 1}
    ))
    return render_template("animes/genre.html", genre=genre_name, animes=animes)


@app.route("/animes/recherche")
@login_required
def anime_search():
    query = request.args.get("q", "")
    animes = []
    if query:
        animes = list(animes_collection.find(
            {"name": {"$regex": query, "$options": "i"}},
            {"name": 1, "cover_url": 1, "genres": 1, "status": 1}
        ))
    return render_template("animes/search.html", query=query, animes=animes)


# ============================================================================
# API
# ============================================================================

@app.route("/api/animes")
@login_required
def api_animes():
    animes = list(animes_collection.find({}, {"name": 1, "cover_url": 1, "genres": 1}))
    for a in animes:
        a["_id"] = str(a["_id"])
    return jsonify(animes)


# ============================================================================
# ERROR HANDLERS
# ============================================================================

@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
