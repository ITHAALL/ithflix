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

accounts           = db_chat["accounts"]
movies_collection  = db_flix["movies"]
animes_collection  = db_flix["animes"]
series_collection  = db_flix["series"]


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


def format_title_with_flag(title):
    if not title:
        return title
    title = title.replace("[VF]", "🇫🇷").replace("[VOSTFR]", "🇺🇸")
    return title


# ============================================================================
# FILTRES JINJA
# ============================================================================

@app.template_filter("total_episodes")
def total_episodes_filter(seasons):
    return sum(s.get("total_episodes", 0) for s in seasons)


@app.template_filter("flag_title")
def flag_title_filter(title):
    return format_title_with_flag(title)


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


# ============================================================================
# SÉRIES  —  structure identique aux animes
#   doc: { title, cover, genres, status, description, seasons: [
#            { season_number, total_episodes, episodes: [
#                { episode_number, title, sources: [url] }
#            ]}
#         ]}
# ============================================================================

@app.route("/series")
@login_required
def series_index():
    all_series = list(series_collection.find(
        {},
        {"title": 1, "cover": 1, "genres": 1, "status": 1, "seasons": 1}
    ).sort("_id", -1))
    return render_template("series/index.html", series=all_series)


@app.route("/series/<serie_id>")
@login_required
def serie_detail(serie_id):
    serie = series_collection.find_one({"_id": ObjectId(serie_id)})
    if not serie:
        return render_template("404.html"), 404
    return render_template("series/detail.html", serie=serie)


@app.route("/series/<serie_id>/s<int:season_num>/e<int:episode_num>")
@login_required
def watch_serie(serie_id, season_num, episode_num):
    serie = series_collection.find_one({"_id": ObjectId(serie_id)})
    if not serie:
        return render_template("404.html"), 404

    season = next((s for s in serie.get("seasons", []) if s["season_number"] == season_num), None)
    if not season:
        return render_template("404.html"), 404

    episode = next((ep for ep in season.get("episodes", []) if ep["episode_number"] == episode_num), None)
    if not episode:
        return render_template("404.html"), 404

    return render_template(
        "series/watch.html",
        serie=serie,
        season_num=season_num,
        episode_num=episode_num,
        episode=episode,
        all_seasons=serie.get("seasons", [])
    )


@app.route("/series/ajouter", methods=["GET", "POST"])
@admin_required
def add_serie():
    if request.method == "POST":
        series_collection.insert_one({
            "title":        request.form.get("title"),
            "cover":        request.form.get("cover"),
            "genres":       [g.strip() for g in request.form.get("genres", "").split(",") if g.strip()],
            "status":       request.form.get("status", "En cours"),
            "description":  request.form.get("description", ""),
            "seasons":      [],
            "created_date": datetime.utcnow(),
            "updated_date": datetime.utcnow(),
        })
        return redirect(url_for("series_index"))
    return render_template("series/add_serie.html")


@app.route("/series/modifier/<id>", methods=["GET", "POST"])
@admin_required
def edit_serie(id):
    if request.method == "POST":
        series_collection.update_one(
            {"_id": ObjectId(id)},
            {"$set": {
                "title":  request.form.get("title"),
                "cover":  request.form.get("cover"),
            }}
        )
        return redirect(url_for("series_index"))
    serie = series_collection.find_one({"_id": ObjectId(id)})
    return render_template("series/edit_serie.html", serie=serie)


@app.route("/series/supprimer/<id>")
@admin_required
def delete_serie(id):
    series_collection.delete_one({"_id": ObjectId(id)})
    return redirect(url_for("series_index"))


@app.route("/series/recherche")
@login_required
def serie_search():
    query = request.args.get("q", "")
    series = []
    if query:
        series = list(series_collection.find(
            {"title": {"$regex": query, "$options": "i"}},
            {"title": 1, "cover": 1, "genres": 1, "status": 1, "seasons": 1}
        ))
    return render_template("series/search.html", query=query, series=series)


# ============================================================================
# SUGGESTION API
# ============================================================================

@app.route("/api/suggestion", methods=["POST"])
@login_required
def send_suggestion():
    data      = request.get_json(silent=True) or {}
    contenu   = data.get("contenu", "").strip()
    section   = data.get("section", "?")
    titre_ref = data.get("titre_ref", "")
    user      = session.get("user", "inconnu")

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
# AUTOCOMPLETE API
# ============================================================================

@app.route("/api/autocomplete")
@login_required
def autocomplete():
    q       = request.args.get("q", "").strip()
    section = request.args.get("section", "all")
    limit   = 8

    if len(q) < 1:
        return jsonify([])

    regex   = {"$regex": q, "$options": "i"}
    results = []

    if section in ("films", "all"):
        for m in movies_collection.find({"title": regex}, {"title": 1}).limit(limit):
            results.append({
                "label": format_title_with_flag(m["title"]),
                "url":   url_for("watch_movie", movie_id=str(m["_id"])),
                "type":  "Film",
                "icon":  "🎬"
            })

    if section in ("series", "all"):
        for s in series_collection.find({"title": regex}, {"title": 1}).limit(limit):
            results.append({
                "label": format_title_with_flag(s["title"]),
                "url":   url_for("serie_detail", serie_id=str(s["_id"])),
                "type":  "Série",
                "icon":  "📺"
            })

    if section in ("animes", "all"):
        for a in animes_collection.find({"name": regex}, {"name": 1}).limit(limit):
            results.append({
                "label": a["name"],
                "url":   url_for("anime_detail", anime_name=a["name"]),
                "type":  "Anime",
                "icon":  "⚡"
            })

    q_lower = q.lower()
    results.sort(key=lambda x: (0 if x["label"].lower().startswith(q_lower) else 1, x["label"]))
    return jsonify(results[:limit])


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
