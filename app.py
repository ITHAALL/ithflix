from flask import Flask, render_template, request, session, redirect, url_for, flash, make_response
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

def get_imdb_id(query):
    """ Convertit un titre en ID IMDb via OMDB """
    omd_api_key = os.getenv("OMBD_API_KEY")
    url = f"https://www.omdbapi.com/?t={query}&apikey={omd_api_key}"
    try:
        res = requests.get(url).json()
        if res.get('Response') == 'False' and "API key" in res.get('Error', ''):
            return "ERROR_API"
        return res.get('imdbID') if res.get('Response') == 'True' else None
    except: return "ERROR_API"

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
    return render_template('index.html')

@app.route('/watch')
@login_required
def watch():
    m_type = request.args.get('type', 'movie')
    search = request.args.get('imdb', '').strip()
    s, e = request.args.get('season', '1'), request.args.get('episode', '1')

    if not search.startswith('tt'):
        res = get_imdb_id(search)
        if res == "ERROR_API":
            flash("Erreur : Clé API invalide. Contactez un admin.")
            return redirect(url_for('flix_index'))
        if not res:
            flash(f"'{search}' introuvable. Vérifiez l'anglais/IMDb.")
            return redirect(url_for('flix_index'))
        search = res

    embed = f"https://vidsrcme.ru/embed/{'movie' if m_type=='movie' else 'tv'}?imdb={search}"
    if m_type in ['series', 'tv']: 
        embed += f"&season={s}&episode={e}"
    
    return render_template('watch.html', embed_url=embed, m_type=m_type)

@app.route('/brave-required')
def brave_required():
    return render_template('brave_required.html')

@app.route('/bypass-brave')
def bypass_brave():
    resp = make_response(redirect(url_for('login')))
    resp.set_cookie('bypass_brave', 'true', max_age=60*60*24)
    return resp

if __name__ == '__main__':
    app.run(debug=True, port=5000)
