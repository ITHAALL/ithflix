from flask import Flask, render_template, request, session, redirect, url_for, make_response
from datetime import datetime
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
from dotenv import load_dotenv
import os, requests

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "ithflix_ultra_secret_99")

client = MongoClient(os.getenv("MONGODB_URI"), server_api=ServerApi('1'))
db_chat = client["ithchat"]
db_flix = client["ithflix"]

accounts = db_chat["accounts"]
movies = db_flix["movies"]
movies_names = db_flix["movies_names"]

def send_discord_embed(title, description, color=0x007bff):
    """ Envoie des logs de connexion sur Discord """
    webhook_url = os.getenv("WEBHOOK_LOGS")
    if not webhook_url: return
    payload = {
        "embeds": [{
            "title": title, "description": description, "color": color,
            "footer": {"text": "📁 ITHFlix Logs"},
            "timestamp": datetime.utcnow().isoformat()
        }]
    }
    try: requests.post(webhook_url, json=payload)
    except: pass

@app.before_request
def block_non_brave():
    """ Sécurité : Force l'utilisation de Brave pour bloquer les pubs nativement """
    if request.endpoint in ['brave_required', 'bypass_brave', 'static'] or request.cookies.get('bypass_brave') == 'true':
        return

    ua_full = request.headers.get('Sec-Ch-Ua', '')
    user_agent = request.headers.get('User-Agent', '')

    if 'Brave' not in ua_full and 'Brave' not in user_agent:
        return redirect(url_for('brave_required'))
    
def login_required(f):
    """ Vérifie si l'utilisateur est connecté """
    def wrapper(*args, **kwargs):
        if 'user' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    wrapper.__name__ = f.__name__
    return wrapper

# --- ROUTES ---

@app.route('/', methods=['GET', 'POST'])
def login():
    if 'user' in session: return redirect(url_for('flix_index'))
    if request.method == 'POST':
        u, p = request.form.get('user'), request.form.get('pass')
        user_data = accounts.find_one({"user": u})
        if user_data and p == user_data.get("password"):
            session['user'] = user_data.get('user')
            session['is_admin'] = user_data.get('admin', False)
            send_discord_embed("🔗 Connexion ITHFlix", f"`{u}` est maintenant en ligne.")
            return redirect(url_for('flix_index'))
        return render_template('login.html', erreur="Identifiants incorrects.")
    return render_template('login.html')

@app.route('/logout')
def logout():
    if 'user' in session:
        send_discord_embed("⛓️‍💥 Déconnexion ITHFlix", f"`{session['user']}` s'est déconnecté.")
    session.clear()
    return redirect(url_for('login'))

@app.route('/flix')
@login_required
def flix_index():
    page = request.args.get('page', 1, type=int)
    sort_type = request.args.get('sort', 'date')
    per_page = 24
    skip = (page - 1) * per_page

    pipeline = [
        {
            "$unionWith": {
                "coll": "series_names",
                "pipeline": [{"$addFields": {"media_type": "series"}}]
            }
        },
        {
            "$addFields": {
                "media_type": {"$ifNull": ["$media_type", "movie"]},
                "sort_priority": {
                    "$cond": {
                        "if": {"$or": [
                            {"$eq": ["$title", "Inconnue"]},
                            {"$eq": ["$release_date", "Inconnue"]}
                        ]},
                        "then": 1, "else": 0
                    }
                }
            }
        }
    ]

    if sort_type == 'alpha':
        pipeline.append({"$sort": {"sort_priority": 1, "title": 1}})
    else:
        pipeline.append({"$sort": {"sort_priority": 1, "release_date": -1}})

    pipeline_data = pipeline + [{"$skip": skip}, {"$limit": per_page}]
    
    catalog = list(db_flix["movies_names"].aggregate(pipeline_data, allowDiskUse=True))
    
    total_count = db_flix["movies_names"].count_documents({}) + db_flix["series_names"].count_documents({})
    total_pages = (total_count // per_page) + (1 if total_count % per_page > 0 else 0)

    if total_pages > 1000:
        total_pages = 1000

    stats = {
        "movies_count": db_flix["movies_names"].count_documents({}),
        "series_count": db_flix["series_names"].count_documents({})
    }

    return render_template('index.html',
        catalog=catalog,
        stats=stats, 
        current_page=page,
        total_pages=total_pages,
        sort_type=sort_type
    )

@app.route('/watch')
@login_required
def watch():
    m_type = request.args.get('type', 'movie')
    imdb_id = request.args.get('imdb', '').strip()
    season = request.args.get('season', '1')
    episode = request.args.get('episode', '1')
    
    col = db_flix["movies_names"] if m_type == "movie" else db_flix["series_names"]
    movie_in_db = col.find_one({"imdb_id": imdb_id})
    search_title = movie_in_db.get('title') if movie_in_db else imdb_id
    
    api_key = os.getenv("OMBD_API_KEY")
    movie_info = {}
    try:
        res = requests.get(f"https://www.omdbapi.com/?t={search_title}&apikey={api_key}").json()
        if res.get("Response") == "True": 
            movie_info = res
    except: pass

    if m_type == "series":
        embed = f"https://vidsrcme.ru/embed/tv?imdb={imdb_id}&season={season}&episode={episode}"
    else:
        embed = f"https://vidsrcme.ru/embed/movie?imdb={imdb_id}"
    
    return render_template('watch.html', 
        embed_url=embed, 
        movie=movie_info, 
        movie_title=search_title, 
        m_type=m_type
    )

@app.route('/brave-required')
def brave_required():
    return render_template('brave_required.html')

@app.route('/bypass-brave')
def bypass_brave():
    resp = make_response(redirect(url_for('login')))
    resp.set_cookie('bypass_brave', 'true', max_age=60*60*24)
    return resp

@app.route('/api/search')
@login_required
def api_search():
    query = request.args.get('q', '')
    if len(query) < 2: return {"results": []}

    regex = {"$regex": query, "$options": "i"}
    
    m_results = list(db_flix["movies_names"].find({"title": regex}).limit(5))
    for r in m_results: r['media_type'] = 'movie'
    
    s_results = list(db_flix["series_names"].find({"title": regex}).limit(5))
    for r in s_results: r['media_type'] = 'series'

    combined = m_results + s_results
    output = []
    for item in combined:
        output.append({
            "title": item.get('title'),
            "imdb_id": item.get('imdb_id'),
            "release_date": item.get('release_date'),
            "media_type": item.get('media_type')
        })
    return {"results": output}

if __name__ == '__main__':
    app.run(debug=True)
