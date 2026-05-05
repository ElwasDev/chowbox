import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import json
import os
import secrets
import urllib.parse
import urllib.request
try:
    import requests as _requests
except ImportError:
    _requests = None
from datetime import datetime, timedelta
from flask import Flask, send_from_directory, jsonify, request, redirect, session
import threading

EMOJI_MAPPING = {
    'chowbox':              'chowbox_',
    'chow_logo':            'chowbox_',
    'chowbox_confirmado':   'chowbox_confirmado',
    'chowbox_mono':         'chowbox_mono',
    'minecraft':            'minecraft',
    'flecha':               'flecha',
    'libroembrujado':       'libroembrujado',
    'cohete_chowbox':       'cohete_chowbox',
}

def get_emoji(guild, name):
    if not name or not guild:
        return ''
    for emoji in guild.emojis:
        if emoji.name == name:
            return str(emoji)
    return ''

app_web = Flask(__name__, static_folder='web')
app_web.secret_key = os.environ.get("FLASK_SECRET", secrets.token_hex(32))

DISCORD_CLIENT_ID     = os.environ.get("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "")
_raw_web_url          = os.environ.get("WEB_URL", "http://localhost:5000").rstrip("/")
WEB_URL               = _raw_web_url if _raw_web_url.startswith(("http://", "https://")) else f"https://{_raw_web_url}"
print(f"DEBUG CLIENT_ID={DISCORD_CLIENT_ID!r}")
print(f"DEBUG CLIENT_SECRET={DISCORD_CLIENT_SECRET[:4] if DISCORD_CLIENT_SECRET else 'VACIO'}...")

DISCORD_AUTH_URL  = "https://discord.com/api/oauth2/authorize"
DISCORD_TOKEN_URL = "https://discord.com/api/oauth2/token"
DISCORD_USER_URL  = "https://discord.com/api/users/@me"

IMG_PENDIENTE = "https://media.discordapp.net/attachments/1145130881124667422/1501350845281734787/pendientechowbox.png?ex=69fbc16f&is=69fa6fef&hm=51bef5b6cdf5aaada3efad8972f30485165de90af8a10f8b0be0ce991a8a4887&=&format=webp&quality=lossless&width=562&height=562"

def get_redirect_uri():
    return f"{WEB_URL}/callback"

ROL_STAFF_AUTORIZADO_ID = int(os.environ.get("ROL_STAFF_AUTORIZADO_ID", "1500704116924743730"))
ROL_NUEVO_STAFF_ID       = int(os.environ.get("ROL_NUEVO_STAFF_ID", "1500703939354431539"))
CANAL_BIENVENIDA_ID      = int(os.environ.get("CANAL_BIENVENIDA_ID", "1500700654971261054"))
GUILD_ID = int(os.environ.get("GUILD_ID", "1500700653444665416"))

postulaciones_web_pendientes = []
postulaciones_enviadas = set()
postulaciones_store = {}  # token -> data
bot_loop = None  # Se asigna cuando el bot está listo
estado_postulaciones = {"abierto": True}
dm_mensajes_postulacion = {}

@app_web.route('/')
def index():
    if not session.get("discord_user"):
        return send_from_directory('web', 'login.html')
    if not estado_postulaciones["abierto"]:
        return send_from_directory('web', 'cerrado.html')
    return send_from_directory('web', 'index.html')

@app_web.route('/login')
def login():
    params = urllib.parse.urlencode({
        "client_id":     DISCORD_CLIENT_ID,
        "redirect_uri":  get_redirect_uri(),
        "response_type": "code",
        "scope":         "identify",
    })
    return redirect(f"{DISCORD_AUTH_URL}?{params}")

@app_web.route('/callback')
def callback():
    code = request.args.get("code")
    if not code:
        return redirect("/?error=no_code")
    try:
        data = urllib.parse.urlencode({
            "client_id":     DISCORD_CLIENT_ID,
            "client_secret": DISCORD_CLIENT_SECRET,
            "grant_type":    "authorization_code",
            "code":          code,
            "redirect_uri":  get_redirect_uri(),
        }).encode()
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "DiscordBot (ChowBox, 1.0)"
        }
        if _requests:
            r = _requests.post(DISCORD_TOKEN_URL, data=data, headers=headers)
            token_data = r.json()
        else:
            req = urllib.request.Request(DISCORD_TOKEN_URL, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req) as resp:
                token_data = json.loads(resp.read())
        access_token = token_data.get("access_token")
        if not access_token:
            print(f"No access token, response: {token_data}")
            return redirect("/?error=no_token")
        if _requests:
            r2 = _requests.get(DISCORD_USER_URL, headers={"Authorization": f"Bearer {access_token}", "User-Agent": "DiscordBot (ChowBox, 1.0)"})
            user_data = r2.json()
        else:
            req2 = urllib.request.Request(DISCORD_USER_URL, headers={"Authorization": f"Bearer {access_token}"})
            with urllib.request.urlopen(req2) as resp2:
                user_data = json.loads(resp2.read())
        session["discord_user"] = {
            "id":          user_data.get("id"),
            "username":    user_data.get("username"),
            "global_name": user_data.get("global_name") or user_data.get("username"),
            "avatar":      user_data.get("avatar"),
        }
        return redirect("/")
    except Exception as e:
        import traceback
        print(f"OAuth error: {e}")
        print(f"OAuth error detail: {traceback.format_exc()}")
        return redirect("/?error=oauth_failed")

@app_web.route('/logout')
def logout():
    session.clear()
    return redirect("/")

@app_web.route('/me')
def me():
    user = session.get("discord_user")
    if user:
        return jsonify({"ok": True, "user": user})
    return jsonify({"ok": False}), 401

@app_web.route('/ya_postulo')
def ya_postulo():
    user = session.get("discord_user")
    if not user:
        return jsonify({"enviado": False})
    enviado = user.get("id") in postulaciones_enviadas
    return jsonify({"enviado": enviado})

@app_web.route('/enviar', methods=['POST'])
def recibir_postulacion():
    user = session.get("discord_user")
    if not user:
        return jsonify({"ok": False, "error": "No autenticado"}), 401
    if user.get("id") in postulaciones_enviadas:
        return jsonify({"ok": False, "error": "ya_postulo"}), 409
    data = None
    try:
        data = request.get_json(force=True, silent=True)
    except Exception:
        pass
    if not data:
        try:
            data = json.loads(request.data.decode('utf-8'))
        except Exception:
            pass
    if not data:
        return jsonify({"ok": False, "error": "Sin datos"}), 400
    data["discord"]      = user.get("username")
    data["discord_id"]   = user.get("id")
    data["discord_name"] = user.get("global_name")
    postulaciones_enviadas.add(user.get("id"))
    if bot_loop and not bot_loop.is_closed():
        asyncio.run_coroutine_threadsafe(enviar_al_canal_revision_web(data), bot_loop)
    else:
        postulaciones_web_pendientes.append(data)
    return jsonify({"ok": True})

@app_web.route('/ver/<token>')
def ver_postulacion(token):
    data = postulaciones_store.get(token)
    if not data:
        return "<h2 style='font-family:sans-serif;text-align:center;margin-top:80px'>Postulación no encontrada o expirada.</h2>", 404
    preguntas = preguntas_data.get("preguntas", [])
    avatar = data.get("avatar_url", "")
    nombre = data.get("discord_name") or data.get("discord", "?")
    tag    = data.get("discord", "?")
    uid    = data.get("discord_id", "")
    ya_procesada = data.get("_procesada", False)

    filas = ""
    for i, p in enumerate(preguntas):
        resp = data.get(f"p{i+1}", "").strip() or "<em style='color:#888'>Sin respuesta</em>"
        filas += f"""
        <div class="qa">
          <div class="q">P{i+1}. {p}</div>
          <div class="a">{resp}</div>
        </div>"""

    botones = ""
    if not ya_procesada:
        botones = f"""
        <div class="actions">
          <form method="POST" action="/aceptar/{token}" onsubmit="return confirm('¿Aceptar a {nombre}?')">
            <button class="btn-accept" type="submit">✅ Aceptar</button>
          </form>
          <form method="POST" action="/rechazar/{token}" onsubmit="return confirm('¿Rechazar a {nombre}?')">
            <button class="btn-reject" type="submit">❌ Rechazar</button>
          </form>
        </div>"""
    else:
        estado = data.get("_estado", "Procesada")
        if estado == "Aceptada":
            botones = '<div class="badge badge-aceptada">✅ Postulación Aceptada</div>'
        else:
            botones = '<div class="badge badge-rechazada">❌ Postulación Rechazada</div>'

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Postulación — {nombre}</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:url('https://media.discordapp.net/attachments/1145130881124667422/1501370161691889774/pajeros.gif?ex=69fbd36c&is=69fa81ec&hm=9c4aded1e34be7e1b8f2186a5cc6937be5e02ded7d53433df8dbd043a5c83911&=&width=450&height=252') center center / cover fixed;color:#e0e0f0;font-family:'Segoe UI',sans-serif;min-height:100vh;padding:32px 16px}}
  body::before{{content:'';position:fixed;inset:0;background:#0008;z-index:0}}
  .card{{position:relative;z-index:1}}
  .logo-container{{text-align:center;margin-bottom:24px;position:relative;z-index:1}}
  .logo{{width:120px;height:120px;object-fit:contain;border-radius:50%;animation:pulse 2s ease-in-out infinite;filter:drop-shadow(0 0 16px #7c3fc1)}}
  @keyframes pulse{{0%,100%{{transform:scale(1);filter:drop-shadow(0 0 16px #7c3fc1)}}50%{{transform:scale(1.08);filter:drop-shadow(0 0 28px #b36ef0)}}}}
  .card{{max-width:720px;margin:0 auto;background:#16162a;border-radius:18px;overflow:hidden;box-shadow:0 8px 40px #0008}}
  .header{{background:linear-gradient(135deg,#5b2d8e,#7c3fc1);padding:32px 28px;display:flex;align-items:center;gap:20px}}
  .avatar{{width:72px;height:72px;border-radius:50%;border:3px solid #fff4;object-fit:cover}}
  .avatar-placeholder{{width:72px;height:72px;border-radius:50%;background:#ffffff22;display:flex;align-items:center;justify-content:center;font-size:2rem}}
  .user-info h1{{font-size:1.4rem;font-weight:700}}
  .user-info p{{opacity:.75;font-size:.95rem;margin-top:4px}}
  .badge-tag{{display:inline-block;background:#ffffff18;border-radius:8px;padding:2px 10px;font-size:.8rem;margin-top:6px}}
  .body{{padding:28px}}
  .qa{{background:#1e1e35;border-radius:12px;padding:16px 18px;margin-bottom:14px;border-left:3px solid #7c3fc1}}
  .q{{font-weight:600;color:#b39ddb;font-size:.9rem;margin-bottom:8px}}
  .a{{color:#e0e0f0;line-height:1.6;font-size:.95rem;white-space:pre-wrap}}
  .actions{{display:flex;gap:14px;margin-top:24px;justify-content:center;flex-wrap:wrap}}
  .btn-accept{{background:#2d7d46;color:#fff;border:none;padding:12px 36px;border-radius:10px;font-size:1rem;cursor:pointer;font-weight:600;transition:.2s}}
  .btn-accept:hover{{background:#3a9e5a}}
  .btn-reject{{background:#8b2020;color:#fff;border:none;padding:12px 36px;border-radius:10px;font-size:1rem;cursor:pointer;font-weight:600;transition:.2s}}
  .btn-reject:hover{{background:#b03030}}
  .badge{{text-align:center;margin-top:24px;padding:14px;background:#1e1e35;border-radius:10px;font-weight:600;font-size:1rem;color:#b39ddb}}
  .badge-aceptada{{background:#1a3d2b;color:#4caf50;border:2px solid #4caf50}}
  .badge-rechazada{{background:#3d1a1a;color:#f44336;border:2px solid #f44336}}
  .footer{{text-align:center;opacity:.4;font-size:.8rem;padding:16px 0 24px}}
</style>
</head>
<body>
<div class="logo-container">
  <img class="logo" src="https://media.discordapp.net/attachments/1145130881124667422/1501370540819091557/postulciones_chowbox.png?ex=69fbd3c7&is=69fa8247&hm=3bffe0684e727214c5b895008f728e8480e470dc9e74b023831b22c3f068e74d&=&format=webp&quality=lossless&width=562&height=562" alt="Chowbox">
</div>
<div class="card">
  <div class="header">
    {'<img class="avatar" src="' + avatar + '">' if avatar else '<div class="avatar-placeholder">👤</div>'}
    <div class="user-info">
      <h1>{nombre}</h1>
      <p><span class="badge-tag">@{tag}</span></p>
      <p style="font-size:.8rem;margin-top:6px;opacity:.6">ID: {uid}</p>
    </div>
  </div>
  <div class="body">
    {filas}
    {botones}
  </div>
  <div class="footer">Chowbox Network · Sistema de Postulaciones</div>
</div>
</body>
</html>"""
    return html

@app_web.route('/aceptar/<token>', methods=['POST'])
def aceptar_postulacion_web(token):
    data = postulaciones_store.get(token)
    if not data or data.get("_procesada"):
        return redirect(f"/ver/{token}")
    data["_procesada"] = True
    data["_estado"] = "Aceptada"
    if bot_loop and not bot_loop.is_closed():
        asyncio.run_coroutine_threadsafe(accion_postulacion_web(token, "aceptar"), bot_loop)
    return redirect(f"/ver/{token}")

@app_web.route('/rechazar/<token>', methods=['POST'])
def rechazar_postulacion_web(token):
    data = postulaciones_store.get(token)
    if not data or data.get("_procesada"):
        return redirect(f"/ver/{token}")
    data["_procesada"] = True
    data["_estado"] = "Rechazada"
    if bot_loop and not bot_loop.is_closed():
        asyncio.run_coroutine_threadsafe(accion_postulacion_web(token, "rechazar"), bot_loop)
    return redirect(f"/ver/{token}")

def iniciar_servidor_web():
    port = int(os.environ.get('PORT', 5000))
    app_web.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

TOKEN = os.environ.get("TOKEN", "")
config = {
    "token": TOKEN,
    "categoria_postulaciones_id": int(os.environ.get("CATEGORIA_POSTULACIONES_ID", 0)) or None,
    "canal_revision_id":          int(os.environ.get("CANAL_REVISION_ID", 0)) or None,
    "canal_resultados_id":        int(os.environ.get("CANAL_RESULTADOS_ID", 0)) or None,
}

with open('preguntas.json', 'r', encoding='utf-8') as f:
    preguntas_data = json.load(f)

try:
    with open('imagenes.json', 'r', encoding='utf-8') as f:
        imagenes_config = json.load(f)
except:
    imagenes_config = {"imagen_aceptado": "", "imagen_rechazado": ""}

postulaciones_activas = {}

def guardar_config():
    pass

def generar_html_postulacion(discord_tag, discord_name, discord_id, preguntas, respuestas_dict):
    filas = ""
    for i, pregunta in enumerate(preguntas):
        respuesta = respuestas_dict.get(i, respuestas_dict.get(f"p{i+1}", "Sin respuesta"))
        if not respuesta:
            respuesta = "Sin respuesta"
        respuesta_html = str(respuesta).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")
        fila_class = "par" if i % 2 == 0 else "impar"
        filas += f"""
        <div class="pregunta {fila_class}">
            <div class="num">P{i+1}</div>
            <div class="contenido">
                <div class="texto-pregunta">{pregunta}</div>
                <div class="texto-respuesta">{respuesta_html}</div>
            </div>
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<title>Postulacion de {discord_name} — ChowBox Staff</title>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:'Segoe UI',Arial,sans-serif; background:#0d0d0d; color:#e0e0e0; min-height:100vh; }}
  header {{ background:linear-gradient(135deg,#7d3c98 0%,#5b2c6f 100%); padding:28px 32px; display:flex; align-items:center; gap:20px; box-shadow:0 4px 20px rgba(0,0,0,0.5); }}
  header .logo {{ font-size:2.2rem; }}
  header .info h1 {{ font-size:1.5rem; font-weight:700; color:#fff; }}
  header .info p {{ font-size:0.9rem; color:rgba(255,255,255,0.75); margin-top:4px; }}
  .badge {{ display:inline-block; background:rgba(255,255,255,0.15); border:1px solid rgba(255,255,255,0.3); border-radius:20px; padding:3px 12px; font-size:0.78rem; color:#fff; margin-top:6px; }}
  .meta {{ background:#1a1a1a; border-bottom:1px solid #2a2a2a; padding:16px 32px; display:flex; gap:32px; font-size:0.88rem; color:#aaa; }}
  .meta span b {{ color:#e0e0e0; }}
  .container {{ max-width:860px; margin:32px auto; padding:0 20px 48px; }}
  .titulo-seccion {{ font-size:0.75rem; text-transform:uppercase; letter-spacing:2px; color:#9b59b6; font-weight:700; margin-bottom:16px; padding-left:4px; }}
  .pregunta {{ display:flex; gap:16px; padding:18px 20px; border-radius:10px; margin-bottom:10px; border-left:3px solid #9b59b6; transition:transform 0.15s; }}
  .pregunta:hover {{ transform:translateX(3px); }}
  .par {{ background:#1c1c1c; }}
  .impar {{ background:#181818; }}
  .num {{ min-width:38px; height:38px; background:#9b59b6; color:#fff; border-radius:50%; display:flex; align-items:center; justify-content:center; font-size:0.78rem; font-weight:700; flex-shrink:0; margin-top:2px; }}
  .contenido {{ flex:1; }}
  .texto-pregunta {{ font-size:0.85rem; color:#aaa; margin-bottom:6px; font-weight:500; }}
  .texto-respuesta {{ font-size:1rem; color:#e8e8e8; line-height:1.5; }}
  footer {{ text-align:center; padding:24px; font-size:0.78rem; color:#444; border-top:1px solid #1e1e1e; }}
</style>
</head>
<body>
<header>
  <div class="logo">🌙</div>
  <div class="info">
    <h1>Postulacion de {discord_name}</h1>
    <p>Staff Team — ChowBox</p>
    <span class="badge">📋 {len(preguntas)} preguntas respondidas</span>
  </div>
</header>
<div class="meta">
  <span>🎮 <b>{discord_tag}</b></span>
  <span>🆔 <b>{discord_id}</b></span>
</div>
<div class="container">
  <div class="titulo-seccion">Respuestas del postulante</div>
  {filas}
</div>
<footer>ChowBox Staff · Sistema de Postulaciones · Documento generado automaticamente</footer>
</body>
</html>"""
    return html


async def procesar_postulaciones_web():
    # Loop de fallback - solo procesa si bot_loop no estaba listo cuando llego la postulacion
    await bot.wait_until_ready()
    while not bot.is_closed():
        if postulaciones_web_pendientes:
            data = postulaciones_web_pendientes.pop(0)
            try:
                print(f"[FALLBACK] Procesando postulacion pendiente de {data.get('discord', '?')}")
                await enviar_al_canal_revision_web(data)
            except Exception as e:
                import traceback
                print(f"Error procesando postulacion web: {e}")
                print(traceback.format_exc())
        await asyncio.sleep(3)

async def accion_postulacion_web(token, accion):
    data = postulaciones_store.get(token)
    if not data:
        return
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        return
    # Editar embed original con nuevo estado
    try:
        ch = guild.get_channel(data.get("_channel_id", 0))
        if ch and data.get("_message_id"):
            msg_orig = await ch.fetch_message(data["_message_id"])
            if msg_orig and msg_orig.embeds:
                emb = msg_orig.embeds[0]
                if accion == "aceptar":
                    emb.color = discord.Color.green()
                    new_desc = emb.description.replace("> 📋 **Estado:** `En Revision` 🔍", "> 📋 **Estado:** `Aceptado` ✅")
                else:
                    emb.color = discord.Color.red()
                    new_desc = emb.description.replace("> 📋 **Estado:** `En Revision` 🔍", "> 📋 **Estado:** `Rechazado` ❌")
                emb = emb.copy()
                emb.description = new_desc
                view_disabled = discord.ui.View(timeout=None)
                view_disabled.add_item(discord.ui.Button(label="Ver Postulacion", style=discord.ButtonStyle.link, url=f"{WEB_URL}/ver/{token}", emoji=get_emoji(guild, EMOJI_MAPPING["libroembrujado"]) or "📋"))
                await msg_orig.edit(embed=emb, view=view_disabled)
    except Exception as ex:
        print(f"[EMBED EDIT] Error: {ex}")
    discord_id  = data.get("discord_id", "")
    discord_tag = data.get("discord", "?")
    discord_name = data.get("discord_name", discord_tag)

    canal_res = guild.get_channel(config.get("canal_resultados_id") or 0)
    usuario = None
    if discord_id:
        try:
            usuario = guild.get_member(int(discord_id)) or await guild.fetch_member(int(discord_id))
        except:
            pass

    if accion == "aceptar":
        if canal_res:
            nombre = usuario.mention if usuario else f"**{discord_name}**"
            e = discord.Embed(
                title=f"[INGRESO] {discord_name} fue admitido en el Staff de ChowBox",
                description=(
                    f"{nombre} fue admitido en el Staff de ChowBox\n\n"
                    "Al igual que los demas postulantes y staff, esperamos que logre alcanzar sus metas, "
                    "y demostrar lo mucho que vale dentro de ChowBox.\n\n"
                    "> ➡ Recuerda que entrar al staff es solo el comienzo. Hay muchas etapas que aprobar una vez logres entrar.\n"
                    "> ¡Mantenerse y crecer es lo dificil!\n\n"
                    'Un dia un sabio dijo... \"*Las pequeñas cosas son las responsables de los **grandes cambios**\"'
                ),
                color=discord.Color.purple(),
                timestamp=datetime.now()
            )
            e.set_image(url="https://cdn.discordapp.com/attachments/1145130881124667422/1501351358727454781/nuevostaff_chowbox.png?ex=69fbc1e9&is=69fa7069&hm=0ad9be7fb226defbe0de6bf3d9ab306c7878e25f94f69c58430a63b8d8e4d5c0")
            await canal_res.send(embed=e)
        if usuario:
            try:
                rol_staff = guild.get_role(ROL_NUEVO_STAFF_ID)
                if rol_staff:
                    await usuario.add_roles(rol_staff, reason="Postulacion aceptada desde web")
            except Exception as ex:
                print(f"[ROL] Error otorgando rol: {ex}")
            try:
                e_dm = discord.Embed(
                    title="✅ ACTUALIZACION DE TU POSTULACION",
                    description=(
                        "¡Tu postulacion fue **aceptada**! ¡Bienvenido al equipo! 🎊\n\n"
                        "📋 **Actualizacion del estado**\n"
                        "> Estado actual: `Aceptado` ✅"
                    ),
                    color=discord.Color.green(),
                    timestamp=datetime.now()
                )
                e_dm.set_footer(text="ChowBox Staff · Sistema de postulaciones")
                await usuario.send(embed=e_dm)
            except:
                pass

    elif accion == "rechazar":
        if canal_res:
            nombre = usuario.mention if usuario else f"**{discord_name}**"
            e = discord.Embed(
                title=f"[RECHAZO] {discord_name} no fue admitido en el Staff",
                description=f"{nombre} no fue admitido en el Staff de ChowBox.",
                color=discord.Color.red(),
                timestamp=datetime.now()
            )
            e.set_image(url="https://media.discordapp.net/attachments/1145130881124667422/1501351894264840292/rechazadochowbox.png?ex=69fbc269&is=69fa70e9&hm=c2935dc196f84586d38ddb3dd64f77e9ac502452ce47c0e1a4a270ca08998bbf&=&format=webp&quality=lossless&width=562&height=562")
            await canal_res.send(embed=e)
        if usuario:
            try:
                e_dm = discord.Embed(
                    title="❌ ACTUALIZACION DE TU POSTULACION",
                    description=(
                        "Tu postulacion fue **rechazada**.\n\n"
                        "📋 **Actualizacion del estado**\n"
                        "> Estado actual: `Rechazado` ❌"
                    ),
                    color=discord.Color.red(),
                    timestamp=datetime.now()
                )
                e_dm.set_footer(text="ChowBox Staff · Sistema de postulaciones")
                await usuario.send(embed=e_dm)
            except:
                pass

async def enviar_al_canal_revision_web(data):
    guild = bot.get_guild(GUILD_ID)
    if not guild:
        print(f"[ENVIAR] ERROR: No se encontro el servidor con ID {GUILD_ID}")
        return

    canal_revision = None
    if config.get("canal_revision_id"):
        canal_revision = guild.get_channel(config["canal_revision_id"])
        if not canal_revision:
            try:
                canal_revision = await bot.fetch_channel(config["canal_revision_id"])
            except Exception as e:
                print(f"fetch_channel fallo: {e}")
    if not canal_revision:
        canal_revision = discord.utils.get(guild.text_channels, name="postulaciones-staff")
    if not canal_revision:
        try:
            canal_revision = await guild.create_text_channel(name="postulaciones-staff")
            config["canal_revision_id"] = canal_revision.id
        except Exception as e:
            print(f"No se pudo crear el canal: {e}")
            return

    discord_tag  = data.get('discord', 'No especificado')
    discord_name = data.get('discord_name', discord_tag)
    discord_id   = data.get('discord_id', '')

    # Guardar en store con token unico
    token = secrets.token_urlsafe(16)
    postulaciones_store[token] = data

    chowbox_e = get_emoji(guild, EMOJI_MAPPING['chowbox']) or '🌙'
    arrow_e   = get_emoji(guild, EMOJI_MAPPING['flecha']) or '➡️'

    # Obtener avatar del miembro si es posible
    try:
        miembro_tmp = guild.get_member(int(discord_id)) if discord_id else None
        if not miembro_tmp and discord_id:
            miembro_tmp = await guild.fetch_member(int(discord_id))
        if miembro_tmp:
            data["avatar_url"] = str(miembro_tmp.display_avatar.url)
    except:
        pass

    url_ver = f"{WEB_URL}/ver/{token}"
    embed_main = discord.Embed(
        title=f"{chowbox_e} Nueva Postulacion",
        description=(
            f"{arrow_e} **Usuario:** {discord_name}\n"
            f"{arrow_e} **Discord:** @{discord_tag}\n"
            f"{arrow_e} **ID:** `{discord_id}`\n\n"
            f"Haz clic en **Ver Postulacion** para revisar todas las respuestas y decidir.\n\n"
            f"> 📋 **Estado:** `En Revision` 🔍"
        ),
        color=discord.Color.purple(),
        timestamp=datetime.now()
    )
    embed_main.set_footer(text="Enviado desde la pagina web · Verificado con Discord OAuth2")
    if data.get("avatar_url"):
        embed_main.set_thumbnail(url=data["avatar_url"])

    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label="Ver Postulacion",
        style=discord.ButtonStyle.link,
        url=url_ver,
        emoji=get_emoji(guild, EMOJI_MAPPING["libroembrujado"]) or "📋"
    ))
    msg = await canal_revision.send(embed=embed_main, view=view)
    data["_message_id"] = msg.id
    data["_channel_id"] = canal_revision.id

    if discord_id:
        try:
            miembro = guild.get_member(int(discord_id))
            if not miembro:
                miembro = await guild.fetch_member(int(discord_id))
            if miembro:
                dm_embed = discord.Embed(
                    title="📬 HEMOS RECIBIDO TU POSTULACION",
                    description=(
                        "Esta notificacion aclara que la recibimos correctamente.\n\n"
                        "Hemos recibido tu `postulacion para formar parte del equipo staff de ChowBox` "
                        "y se encuentra pendiente de revision.\n"
                        "Desde ahora, hasta la resolucion de la postulacion, pueden pasar dias. "
                        "Por favor, ten paciencia.\n\n"
                        "> Te notificaremos por este medio en cuanto el equipo tome una decision.\n\n"
                        "📋 **Actualizacion del estado**\n"
                        "> Estado actual: `Pendiente`"
                    ),
                    color=discord.Color.purple(),
                    timestamp=datetime.now()
                )
                dm_embed.set_image(url=IMG_PENDIENTE)
                dm_embed.set_footer(text="ChowBox Staff · Sistema de postulaciones")
                dm_msg = await miembro.send(embed=dm_embed)
                dm_mensajes_postulacion[str(discord_id)] = dm_msg.id
        except Exception as e:
            print(f"No se pudo enviar DM al postulante: {e}")


class BotonPostular(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            label="Postularse (Web)",
            style=discord.ButtonStyle.link,
            url=os.environ.get("WEB_URL", "http://localhost:5000"),
            emoji="🌐"
        ))

    @discord.ui.button(label="Postularse (Chat)", style=discord.ButtonStyle.primary, custom_id="postular_button", emoji="⛏️")
    async def postular_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id in postulaciones_activas:
            await interaction.response.send_message("❌ Ya tienes una postulacion en proceso.", ephemeral=True)
            return
        guild = interaction.guild
        categoria = None
        if config.get("categoria_postulaciones_id"):
            categoria = discord.utils.get(guild.categories, id=config["categoria_postulaciones_id"])
        if not categoria:
            categoria = discord.utils.get(guild.categories, name="📝 Postulaciones")
            if not categoria:
                try:
                    categoria = await guild.create_category("📝 Postulaciones")
                    config["categoria_postulaciones_id"] = categoria.id
                except Exception as e:
                    await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)
                    return
        try:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False),
                interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
                guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
            }
            canal = await categoria.create_text_channel(
                name=f"🔨・postulacion-{interaction.user.name}",
                overwrites=overwrites
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Error al crear canal: {e}", ephemeral=True)
            return
        postulaciones_activas[interaction.user.id] = {
            "canal_id": canal.id,
            "respuestas": {},
            "pregunta_actual": 0,
            "inicio": datetime.now().isoformat(),
            "tiempo_limite": datetime.now() + timedelta(minutes=34)
        }
        await interaction.response.send_message(f"> <:si_mineback:1454893106179735642> Canal creado: {canal.mention}", ephemeral=True)
        await iniciar_postulacion(canal, interaction.user)
        asyncio.create_task(temporizador_postulacion(canal, interaction.user.id, 34))


async def temporizador_postulacion(canal, user_id, minutos):
    await asyncio.sleep(minutos * 60)
    if user_id in postulaciones_activas:
        postulacion = postulaciones_activas[user_id]
        if postulacion["canal_id"] == canal.id:
            try:
                await canal.send("⏰ **Tiempo agotado.** El canal se cerrara en 10 segundos.")
                await asyncio.sleep(10)
                await canal.delete()
                del postulaciones_activas[user_id]
            except:
                pass


async def iniciar_postulacion(canal, usuario):
    guild = canal.guild
    chowbox_e   = get_emoji(guild, EMOJI_MAPPING['chowbox'])  or '🌙'
    minecraft_e = get_emoji(guild, EMOJI_MAPPING['minecraft']) or '⛏️'
    embed = discord.Embed(
        title=f"{chowbox_e} Proceso de Postulacion — Staff ChowBox",
        description=f"¡Hola {usuario.mention}! Bienvenido a tu canal privado de postulacion.",
        color=discord.Color.purple()
    )
    embed.add_field(name=f"{minecraft_e} Instrucciones", value=(
        "**1.** Responde cada pregunta de forma clara y detallada.\n"
        "**2.** Revisa tus respuestas antes de enviar.\n"
        "**3.** Tienes **34 minutos** para completar el proceso."
    ), inline=False)
    await canal.send(embed=embed)
    await enviar_pregunta(canal, usuario.id, 0)


async def enviar_pregunta(canal, user_id, indice):
    preguntas = preguntas_data["preguntas"]
    if indice >= len(preguntas):
        await finalizar_postulacion(canal, user_id)
        return
    await canal.send(f"**💬 Pregunta {indice + 1} de {len(preguntas)}:** {preguntas[indice]}")


async def finalizar_postulacion(canal, user_id):
    postulacion = postulaciones_activas.get(user_id)
    if not postulacion:
        return
    embed = discord.Embed(title="📋 Resumen de tu postulacion", color=discord.Color.purple())
    for i, pregunta in enumerate(preguntas_data["preguntas"]):
        embed.add_field(name=f"P{i+1}: {pregunta}", value=postulacion["respuestas"].get(i, "Sin respuesta")[:1024], inline=False)
    await canal.send(embed=embed, view=ConfirmarPostulacion(user_id))


class BotonesRevision(discord.ui.View):
    def __init__(self, user_id, username):
        super().__init__(timeout=None)
        self.user_id  = user_id
        self.username = username

    async def _get_canal_resultados(self, guild):
        canal = guild.get_channel(config.get("canal_resultados_id")) if config.get("canal_resultados_id") else None
        if not canal:
            canal = discord.utils.get(guild.text_channels, name="resultados-postulaciones")
        return canal

    async def _editar_dm_estado(self, guild, nuevo_estado: str, color: discord.Color, emoji_estado: str):
        usuario = guild.get_member(self.user_id)
        if not usuario:
            return
        dm_msg_id = dm_mensajes_postulacion.get(str(self.user_id))
        if not dm_msg_id:
            return
        try:
            dm_channel = await usuario.create_dm()
            dm_msg = await dm_channel.fetch_message(dm_msg_id)
            embed = dm_msg.embeds[0] if dm_msg.embeds else None
            if embed:
                embed_dict = embed.to_dict()
                desc = embed_dict.get("description", "")
                import re
                desc = re.sub(
                    r"> Estado actual: `[^`]+`",
                    f"> Estado actual: `{nuevo_estado}` {emoji_estado}",
                    desc
                )
                embed_dict["description"] = desc
                embed_dict["color"] = color.value
                embed_dict.pop("image", None)
                new_embed = discord.Embed.from_dict(embed_dict)
                await dm_msg.edit(embed=new_embed)
        except Exception as e:
            print(f"No se pudo editar el DM: {e}")

    @discord.ui.button(label="Aceptar", style=discord.ButtonStyle.success, custom_id="aceptar_postulacion", emoji="✅")
    async def aceptar(self, interaction: discord.Interaction, button: discord.ui.Button):
        tiene_rol = any(role.id == ROL_STAFF_AUTORIZADO_ID for role in interaction.user.roles)
        if not tiene_rol:
            await interaction.response.send_message("❌ No tienes permiso para realizar esta accion.", ephemeral=True)
            return
        guild     = interaction.guild
        canal_res = await self._get_canal_resultados(guild)
        usuario   = guild.get_member(self.user_id)
        if canal_res:
            nombre = usuario.mention if usuario else f"**{self.username}**"
            e = discord.Embed(
                title=f"[INGRESO] El postulante {self.username} fue admitido en el Staff de ChowBox",
                description=(
                    f"{nombre} fue admitido en el Staff de ChowBox\n\n"
                    "Al igual que los demas postulantes y staff, esperamos que logre alcanzar sus metas, "
                    "y demostrar lo mucho que vale dentro de ChowBox.\n\n"
                    "> ➡ Recuerda que entrar al staff es solo el comienzo. Hay muchas etapas que aprobar una vez logres entrar.\n"
                    "> ¡Mantenerse y crecer es lo dificil!\n\n"
                    'Un dia un sabio dijo... "*Las pequeñas cosas son las responsables de los **grandes cambios**"'
                ),
                color=discord.Color.purple(),
                timestamp=datetime.now()
            )
            e.set_image(url="https://cdn.discordapp.com/attachments/1145130881124667422/1501351358727454781/nuevostaff_chowbox.png?ex=69fbc1e9&is=69fa7069&hm=0ad9be7fb226defbe0de6bf3d9ab306c7878e25f94f69c58430a63b8d8e4d5c0")
            await canal_res.send(embed=e)
        if usuario:
            try:
                rol_staff = guild.get_role(ROL_NUEVO_STAFF_ID)
                if rol_staff:
                    await usuario.add_roles(rol_staff, reason="Postulacion aceptada")
            except Exception as e:
                print(f"[ROL] No se pudo otorgar rol: {e}")
            try:
                e_dm = discord.Embed(
                    title="✅ ACTUALIZACION DE TU POSTULACION",
                    description=(
                        "¡Tu postulacion fue **aceptada**! ¡Bienvenido al equipo! 🎊\n\n"
                        "📋 **Actualizacion del estado**\n"
                        "> Estado actual: `Aceptado` ✅"
                    ),
                    color=discord.Color.green(),
                    timestamp=datetime.now()
                )
                e_dm.set_footer(text="ChowBox Staff · Sistema de postulaciones")
                await usuario.send(embed=e_dm)
            except:
                pass
        await self._editar_dm_estado(guild, "Aceptado", discord.Color.green(), "✅")
        embed = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed(color=discord.Color.green())
        embed.title = "✅ POSTULACION ACEPTADA"
        embed.color = discord.Color.green()
        for item in self.children: item.disabled = True
        await interaction.response.send_message(f"> ✅ Aceptada por {interaction.user.mention}", ephemeral=False)
        await interaction.message.edit(embed=embed, view=self)

    @discord.ui.button(label="Rechazar", style=discord.ButtonStyle.danger, custom_id="rechazar_postulacion", emoji="❌")
    async def rechazar(self, interaction: discord.Interaction, button: discord.ui.Button):
        tiene_rol = any(role.id == ROL_STAFF_AUTORIZADO_ID for role in interaction.user.roles)
        if not tiene_rol:
            await interaction.response.send_message("❌ No tienes permiso para realizar esta accion.", ephemeral=True)
            return
        guild     = interaction.guild
        canal_res = await self._get_canal_resultados(guild)
        usuario   = guild.get_member(self.user_id)
        if canal_res:
            nombre = usuario.mention if usuario else f"**{self.username}**"
            e = discord.Embed(
                title=f"[RESULTADO] La postulacion de {self.username} fue rechazada en el Staff de ChowBox",
                description=(
                    f"{nombre} tu postulacion para formar parte del Staff de ChowBox ha sido revisada, "
                    "y en esta ocasion no ha sido aprobada.\n\n"
                    "Agradecemos el tiempo, esfuerzo e interes que mostraste al querer formar parte del equipo de ChowBox.\n\n"
                    "> ➡ Recuerda: un rechazo no define tu capacidad. Siempre puedes mejorar, aprender y volver a intentarlo en el futuro.\n"
                    "> Cada experiencia es una oportunidad para crecer.\n\n"
                    'Un dia un sabio dijo... "Los grandes logros nacen despues de muchos intentos."'
                ),
                color=discord.Color.purple(),
                timestamp=datetime.now()
            )
            e.set_image(url="https://media.discordapp.net/attachments/1145130881124667422/1501351894264840292/rechazadochowbox.png?ex=69fbc269&is=69fa70e9&hm=c2935dc196f84586d38ddb3dd64f77e9ac502452ce47c0e1a4a270ca08998bbf&=&format=webp&quality=lossless&width=562&height=562")
            await canal_res.send(embed=e)
        if usuario:
            try:
                e_dm = discord.Embed(
                    title="❌ ACTUALIZACION DE TU POSTULACION",
                    description=(
                        "Tu postulacion fue **rechazada**. Puedes reintentar en 14 dias. 💪\n\n"
                        "📋 **Actualizacion del estado**\n"
                        "> Estado actual: `Rechazado` ❌"
                    ),
                    color=discord.Color.purple(),
                    timestamp=datetime.now()
                )
                e_dm.set_footer(text="ChowBox Staff · Sistema de postulaciones")
                await usuario.send(embed=e_dm)
            except:
                pass
        await self._editar_dm_estado(guild, "Rechazado", discord.Color.purple(), "❌")
        embed = interaction.message.embeds[0] if interaction.message.embeds else discord.Embed(color=discord.Color.purple())
        embed.title = "❌ POSTULACION RECHAZADA"
        embed.color = discord.Color.purple()
        for item in self.children: item.disabled = True
        await interaction.response.send_message(f"> ❌ Rechazada por {interaction.user.mention}", ephemeral=False)
        await interaction.message.edit(embed=embed, view=self)


class ConfirmarPostulacion(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=None)
        self.user_id = user_id

    @discord.ui.button(label="Enviar postulacion", style=discord.ButtonStyle.success, emoji="✅")
    async def enviar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Esta no es tu postulacion.", ephemeral=True)
            return
        postulacion = postulaciones_activas.get(self.user_id)
        if not postulacion:
            await interaction.response.send_message("❌ Error al encontrar tu postulacion.", ephemeral=True)
            return
        guild = interaction.guild
        canal_revision = guild.get_channel(config.get("canal_revision_id")) if config.get("canal_revision_id") else None
        if not canal_revision:
            canal_revision = discord.utils.get(guild.text_channels, name="postulaciones-staff")
            if not canal_revision:
                try:
                    canal_revision = await guild.create_text_channel(name="postulaciones-staff")
                    config["canal_revision_id"] = canal_revision.id
                except: pass
        if canal_revision:
            chowbox_e  = get_emoji(interaction.guild, EMOJI_MAPPING['chowbox']) or '🌙'
            arrow_e    = get_emoji(interaction.guild, EMOJI_MAPPING['flecha']) or '➡️'
            preguntas_lista = preguntas_data["preguntas"]
            embed_main = discord.Embed(
                description=(
                    f"{chowbox_e} **Postulacion De {interaction.user.display_name}**\n"
                    f"{arrow_e} **Discord:** {interaction.user}\n"
                    f"{arrow_e} **ID:** `{interaction.user.id}`"
                ),
                color=discord.Color.purple(),
                timestamp=datetime.now()
            )
            embed_main.set_thumbnail(url=interaction.user.display_avatar.url)
            embed_main.set_footer(text=f"Postulacion de {interaction.user.name}")
            CHUNK = 12
            embeds_preguntas = []
            for chunk_start in range(0, len(preguntas_lista), CHUNK):
                chunk = preguntas_lista[chunk_start:chunk_start + CHUNK]
                e = discord.Embed(color=discord.Color.purple())
                for i, pregunta in enumerate(chunk):
                    idx = chunk_start + i
                    respuesta = postulacion["respuestas"].get(idx, "Sin respuesta")
                    e.add_field(name=f"{arrow_e} P{idx+1}: {pregunta[:100]}", value=f"> {str(respuesta)[:1000]}", inline=False)
                embeds_preguntas.append(e)
            view = BotonesRevision(interaction.user.id, interaction.user.name)
            await canal_revision.send(embed=embed_main, view=view)
            for e in embeds_preguntas:
                await canal_revision.send(embed=e)
        await interaction.response.send_message("✅ **¡Postulacion enviada!** Este canal se cerrara en 5 segundos.")
        try:
            dm_embed = discord.Embed(
                title="📬 HEMOS RECIBIDO TU POSTULACION",
                description=(
                    "Esta notificacion aclara que la recibimos correctamente.\n\n"
                    "Hemos recibido tu `postulacion para formar parte del equipo staff de ChowBox` "
                    "y se encuentra pendiente de revision.\n"
                    "Desde ahora, hasta la resolucion de la postulacion, pueden pasar dias. "
                    "Por favor, ten paciencia.\n\n"
                    "> Te notificaremos por este medio en cuanto el equipo tome una decision.\n\n"
                    "📋 **Actualizacion del estado**\n"
                    "> Estado actual: `Pendiente`"
                ),
                color=discord.Color.purple(),
                timestamp=datetime.now()
            )
            dm_embed.set_image(url=IMG_PENDIENTE)
            dm_embed.set_footer(text="ChowBox Staff · Sistema de postulaciones")
            dm_msg = await interaction.user.send(embed=dm_embed)
            dm_mensajes_postulacion[str(interaction.user.id)] = dm_msg.id
        except Exception as e:
            print(f"No se pudo enviar DM (chat): {e}")
        del postulaciones_activas[self.user_id]
        await asyncio.sleep(5)
        try: await interaction.channel.delete()
        except: pass

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.danger, emoji="❌")
    async def cancelar(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("❌ Esta no es tu postulacion.", ephemeral=True)
            return
        await interaction.response.send_message("❌ Postulacion cancelada. Cerrando en 5 segundos.")
        if self.user_id in postulaciones_activas:
            del postulaciones_activas[self.user_id]
        await asyncio.sleep(5)
        try: await interaction.channel.delete()
        except: pass


def tiene_rol_staff(interaction: discord.Interaction) -> bool:
    if interaction.user.guild_permissions.administrator:
        return True
    return any(role.id == ROL_STAFF_AUTORIZADO_ID for role in interaction.user.roles)

@bot.tree.command(name="abrir_postulaciones", description="Abre las postulaciones de staff")
async def abrir_postulaciones(interaction: discord.Interaction):
    if not tiene_rol_staff(interaction):
        await interaction.response.send_message("❌ No tienes permiso para usar este comando.", ephemeral=True)
        return
    estado_postulaciones["abierto"] = True
    postulaciones_enviadas.clear()
    dm_mensajes_postulacion.clear()
    embed = discord.Embed(
        title="✅ Postulaciones abiertas",
        description=(
            "Las postulaciones de staff estan ahora **abiertas**.\n\n"
            "🔄 El historial de postulaciones fue reiniciado — todos pueden volver a postularse."
        ),
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="limpiar_postulacion", description="Permite a un usuario volver a postularse")
@app_commands.describe(usuario="El usuario al que quieres resetear la postulacion")
async def limpiar_postulacion(interaction: discord.Interaction, usuario: discord.Member):
    if not tiene_rol_staff(interaction):
        await interaction.response.send_message("❌ No tienes permiso para usar este comando.", ephemeral=True)
        return
    uid = str(usuario.id)
    eliminado = uid in postulaciones_enviadas
    postulaciones_enviadas.discard(uid)
    dm_mensajes_postulacion.pop(uid, None)
    if eliminado:
        embed = discord.Embed(title="🔄 Postulacion reseteada", description=f"{usuario.mention} puede volver a postularse.", color=discord.Color.green())
    else:
        embed = discord.Embed(title="⚠️ Sin postulacion registrada", description=f"{usuario.mention} no tenia ninguna postulacion enviada.", color=discord.Color.orange())
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="cerrar_postulaciones", description="Cierra las postulaciones de staff")
async def cerrar_postulaciones(interaction: discord.Interaction):
    if not tiene_rol_staff(interaction):
        await interaction.response.send_message("❌ No tienes permiso para usar este comando.", ephemeral=True)
        return
    estado_postulaciones["abierto"] = False
    embed = discord.Embed(title="🔒 Postulaciones cerradas", description="Las postulaciones de staff estan ahora **cerradas**.", color=discord.Color.purple())
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.event
async def rotar_status():
    await bot.wait_until_ready()
    while not bot.is_closed():
        total = len(postulaciones_enviadas)
        actividades = [
            discord.Activity(type=discord.ActivityType.watching, name="Revisando postulaciones"),
            discord.Activity(type=discord.ActivityType.watching, name=f"Postulaciones: {total} enviadas"),
        ]
        for actividad in actividades:
            await bot.change_presence(status=discord.Status.online, activity=actividad)
            await asyncio.sleep(10)

@bot.event
async def on_ready():
    global bot_loop
    bot_loop = asyncio.get_event_loop()
    print(f'Bot conectado como {bot.user}')
    print(f'Pagina web activa con OAuth2 Discord')
    try:
        synced = await bot.tree.sync()
        print(f'{len(synced)} comandos sincronizados')
    except Exception as e:
        print(f'Error: {e}')
    bot.add_view(BotonPostular())
    bot.add_view(BotonesRevision(0, ""))
    bot.loop.create_task(procesar_postulaciones_web())
    bot.loop.create_task(rotar_status())
    print("Sistema listo")

@bot.event
async def on_member_join(member):
    canal = member.guild.get_channel(CANAL_BIENVENIDA_ID)
    if not canal:
        return
    numero = member.guild.member_count
    chowbox_e = get_emoji(member.guild, EMOJI_MAPPING['chowbox']) or '⚡'
    embed = discord.Embed(
        title=f"Bienvenido(a) a Chowbox Network",
        description=(
            f"¡Hola {member.mention} bienvenido(a) a DiosesMC Postulaciones! ⚡\n\n"
            f"» Recuerda leer cómo postularte para entrenarte antes de postularte. "
            f"También será muy útil que revises el canal <#1500701909672005813>.\n\n"
            f"# ENLACES ÚTILES\n"
            f"> `01.` <#1500702297397919814>\n"
            f"> `02.` <#1500702077125398548>\n\n"
            f"{member.mention} Te has convertido en el usuario número **{numero}**"
        ),
        color=discord.Color.purple(),
        timestamp=datetime.now()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.set_footer(text="Chowbox Network · Bienvenidas")
    await canal.send(content=member.mention, embed=embed)

@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if message.author.id in postulaciones_activas:
        postulacion = postulaciones_activas[message.author.id]
        if message.channel.id == postulacion["canal_id"]:
            pregunta_actual = postulacion["pregunta_actual"]
            if pregunta_actual < len(preguntas_data["preguntas"]):
                postulacion["respuestas"][pregunta_actual] = message.content
                postulacion["pregunta_actual"] += 1
                try: await message.add_reaction("✅")
                except: pass
                try: await enviar_pregunta(message.channel, message.author.id, postulacion["pregunta_actual"])
                except Exception as e: print(f"Error: {e}")
    await bot.process_commands(message)

@bot.tree.command(name="setup_postulaciones", description="Configura el sistema de postulaciones")
async def setup_postulaciones(interaction: discord.Interaction):
    if not tiene_rol_staff(interaction):
        await interaction.response.send_message("❌ No tienes permiso para usar este comando.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    chow_logo          = get_emoji(guild, EMOJI_MAPPING['chow_logo'])         or '🌙'
    chowbox_confirmado = get_emoji(guild, EMOJI_MAPPING['chowbox_confirmado']) or '✅'
    chowbox_mono       = get_emoji(guild, EMOJI_MAPPING['chowbox_mono'])       or '🐒'
    embed = discord.Embed(
        description=(
            f"# {chow_logo} - ¡POSTULACIONES ABIERTAS!\n"
            "¿Estas interesado en ser parte del Staff-Team?\n"
            "Si es asi, no esperes mas. Esta es tu oportunidad. Postulate dando clic en el boton de abajo.\n\n"
            "# Requisitos a cumplir:\n"
            f"{chowbox_confirmado}: Tener minimo 14 Anos.\n"
            f"{chowbox_confirmado}: Ser premium.\n"
            f"{chowbox_confirmado}: Historial limpio en el servidor.\n"
            f"{chowbox_confirmado}: No ser staff en otro servidor.\n"
            f"{chowbox_confirmado}: Buena ortografia y madurez.\n\n"
            f"{chowbox_mono} - **¡Postulate dando clic en el boton de abajo!**\n\n"
            f"{chow_logo} | ChowBox Postulaciones"
        ),
        color=discord.Color.purple()
    )
    view = discord.ui.View(timeout=None)
    view.add_item(discord.ui.Button(
        label="Postularse",
        style=discord.ButtonStyle.link,
        url=WEB_URL or "https://nighboxpostulaciones.up.railway.app/",
        emoji="🌐"
    ))
    await interaction.channel.send(embed=embed, view=view)
    await interaction.followup.send("✅ Configurado!", ephemeral=True)

@bot.tree.command(name="ayuda_postulaciones", description="Ayuda sobre el sistema")
async def ayuda_postulaciones(interaction: discord.Interaction):
    embed = discord.Embed(title="ℹ️ Ayuda - Postulaciones", color=discord.Color.purple())
    embed.add_field(name="🌐 Web", value="Haz clic en el boton → inicia sesion con Discord → completa el formulario.", inline=False)
    embed.add_field(name="🔐 Seguridad", value="El sistema verifica tu identidad con Discord OAuth2.", inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

if __name__ == "__main__":
    TOKEN = os.environ.get("TOKEN") or os.environ.get("token") or ""
    TOKEN = TOKEN.strip()
    print(f"DEBUG: TOKEN existe={bool(TOKEN)}, largo={len(TOKEN)}")
    if not TOKEN:
        print("❌ ERROR: Variable de entorno TOKEN no configurada.")
    else:
        hilo_web = threading.Thread(target=iniciar_servidor_web, daemon=True)
        hilo_web.start()
        try:
            bot.run(TOKEN)
        except discord.LoginFailure:
            print("❌ Token invalido.")
        except Exception as e:
            print(f"❌ ERROR: {e}")
