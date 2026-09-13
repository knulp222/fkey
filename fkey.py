"""FKey — correcteur de texte 100 % local (hors-ligne).

Version production : aucun service cloud, aucune clé API. Un modèle de langue
tourne sur l'ordinateur via llama.cpp ; les textes ne quittent jamais la machine.
"""
import os, sys, time, threading, json, ctypes
import tkinter as tk

# ─── Instance unique ──────────────────────────────────────────────────────────
_mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Global\\FKey_v1")
if ctypes.windll.kernel32.GetLastError() == 183:
    ctypes.windll.user32.MessageBoxW(
        0, "FKey est déjà lancé.\nRetrouvez-le dans la zone de notification (près de l'horloge).", "FKey", 0x40)
    sys.exit(0)

import pyperclip, pystray
from pystray import MenuItem as pitem
from PIL import Image, ImageTk
from pynput import keyboard as kb

import local_llm
import ui
from ui import C, F, Label, Button, Card, Badge, Toggle, ProgressBar, KeyCap, OptionCard, NavItem, ScrollFrame

APP_NAME = 'FKey'
VERSION  = '1.0'

# ─── Chemins ──────────────────────────────────────────────────────────────────
def _res(r):
    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, r)

def _data(r):
    base = (os.path.dirname(sys.executable) if getattr(sys, 'frozen', False)
            else os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, r)

CONFIG_FILE = _data('config.json')
LOG_FILE    = _data('fkey_log.txt')
ICON_FILE   = _res('fkey.ico')          # icône de l'exe / fenêtres
TRAY_ICON   = _res('fkey_tray.ico')     # recadrage serré, lisible en 16 px
LOGO_PNG    = _res('fkey_logo.png')     # logo pour l'interface

# ─── Config ───────────────────────────────────────────────────────────────────
KEY_LABELS = {f'f{i}': f'F{i}' for i in range(1, 13)}
KEY_LABELS.update({'escape': 'Échap', 'tab': 'Tab', 'space': 'Espace', 'delete': 'Suppr',
                   'home': 'Début', 'end': 'Fin', 'insert': 'Inser', 'pause': 'Pause',
                   'page_up': 'Pg↑', 'page_down': 'Pg↓'})
LANGS = ['Anglais', 'Français', 'Espagnol', 'Allemand', 'Italien',
         'Portugais', 'Néerlandais', 'Arabe', 'Japonais', 'Chinois']

DEFAULT_CFG = {
    'onboarded': False,
    'model': '',                     # fichier .gguf dans models\
    'startup': False,                # lancer avec Windows
    'preload': True,                 # charger le modèle dès le lancement
    'notify': True,                  # notification à chaque traitement
    'translation_language': 'Anglais',
    'shortcuts': {'correct': 'f2', 'improve': 'f3', 'translate': 'f4', 'prompt': 'f6'},
}

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                s = json.load(f)
            c = {**DEFAULT_CFG, **s}
            c['shortcuts'] = {**DEFAULT_CFG['shortcuts'], **s.get('shortcuts', {})}
            return c
        except Exception:
            pass
    return {**DEFAULT_CFG, 'shortcuts': dict(DEFAULT_CFG['shortcuts'])}

def save_config():
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f'Config save error: {e}')

config = load_config()

# ─── Logging ──────────────────────────────────────────────────────────────────
class _Logger:
    def __init__(self, p):
        self.t = sys.__stdout__
        self.f = open(p, 'a', encoding='utf-8')
    def write(self, m):
        try: self.t.write(m)
        except Exception: pass
        self.f.write(m); self.f.flush()
    def flush(self):
        try: self.t.flush()
        except Exception: pass
        self.f.flush()

sys.stdout = sys.stderr = _Logger(LOG_FILE)

# ─── Moteur ───────────────────────────────────────────────────────────────────
engine = local_llm.LlamaServer()
_engine_lock = threading.Lock()
_state = {'busy': False, 'loading': False, 'last_ms': None, 'count': 0}
_listeners = []          # callbacks UI à prévenir quand l'état change

def on_state_change(fn):
    _listeners.append(fn)

def _emit():
    for fn in list(_listeners):
        try: fn()
        except Exception: pass

def model_name():
    m = config.get('model', '')
    if m and os.path.isfile(os.path.join(local_llm.MODELS_DIR, m)):
        return m
    return local_llm.default_model()

def engine_ready():
    return engine.ready() and engine.model == model_name()

def ensure_engine():
    m = model_name()
    if not m or not local_llm.server_available():
        return False
    with _engine_lock:
        if engine.ready() and engine.model == m:
            return True
        _state['loading'] = True; _emit()
        try:
            ok = engine.start(m)
        finally:
            _state['loading'] = False; _emit()
        return ok

def start_engine_async():
    threading.Thread(target=ensure_engine, daemon=True).start()

def stop_engine():
    with _engine_lock:
        engine.stop()
    _emit()

def status_text():
    """(texte, tone) pour les badges."""
    if not local_llm.server_available() or not model_name():
        return 'Modèle à installer', 'warn'
    if _state['loading']:
        return 'Chargement…', 'warn'
    if _state['busy']:
        return 'Traitement…', 'accent'
    if engine.ready():
        return 'Prêt', 'success'
    return 'En veille', 'neutral'

# ─── Traitement texte ─────────────────────────────────────────────────────────
controller = kb.Controller()
tray_icon = None
kb_listener = None
_tk_root = None

def schedule_tk(fn):
    if _tk_root: _tk_root.after(0, fn)

def notify(msg, title=APP_NAME):
    if config.get('notify', True) and tray_icon:
        try: tray_icon.notify(msg, title)
        except Exception: pass

def _ctrl(k):
    controller.press(kb.Key.ctrl); controller.press(k)
    controller.release(k); controller.release(kb.Key.ctrl)

def _clip_wait(timeout=1.2):
    """Attend que l'application ait rempli le presse-papiers (Word met parfois
    plusieurs centaines de ms à poser ses formats, le Bloc-notes quelques ms)."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            r = pyperclip.paste()
        except Exception:
            r = ''
        if r:
            return r
        time.sleep(0.03)
    return ''

def _get_selected():
    s = pyperclip.paste(); pyperclip.copy('')
    _ctrl('c')
    r = _clip_wait(0.6)
    if not r: pyperclip.copy(s)
    return r

def _get_all():
    s = pyperclip.paste(); pyperclip.copy('')
    _ctrl('a'); time.sleep(0.05); _ctrl('c')
    r = _clip_wait()
    if not r: pyperclip.copy(s)
    return r

def _paste_then_restore(text, original):
    """Colle le résultat, puis rend son presse-papiers à l'utilisateur — mais
    seulement une fois que l'application a eu le temps de lire le nôtre."""
    pyperclip.copy(text); _ctrl('v')
    time.sleep(0.6)
    pyperclip.copy(original)

def run_instruction(instruction, text):
    """Applique l'instruction au texte via le moteur local. Renvoie le texte ou None."""
    if not ensure_engine():
        return None
    _state['busy'] = True; _emit()
    try:
        t0 = time.time()
        out = engine.chat(local_llm._wrap(instruction, text), local_llm.estimate_max_tokens(text))
        _state['last_ms'] = int((time.time() - t0) * 1000); _state['count'] += 1
        print(f'[fkey] {instruction[:30]}… {_state["last_ms"]} ms')
        return out
    except Exception as e:
        print(f'[fkey] erreur moteur : {e}')
        return None
    finally:
        _state['busy'] = False; _emit()

def run_free(prompt, original):
    """Instruction sans texte capturé : génère le résultat et le colle à la position du curseur."""
    if not ensure_engine():
        notify('✗ Modèle indisponible'); return
    _state['busy'] = True; _emit()
    try:
        t0 = time.time()
        task = f'Tâche : "{prompt}"\n\nRenvoie UNIQUEMENT le résultat textuel, sans introduction.'
        r = local_llm.chat(engine.url, task, engine.model, 1024)
        _state['last_ms'] = int((time.time() - t0) * 1000); _state['count'] += 1
    except Exception as e:
        print(f'[fkey] erreur moteur : {e}'); r = None
    finally:
        _state['busy'] = False; _emit()
    if r:
        _paste_then_restore(r, original)
        notify(f'✓ Terminé en {_state["last_ms"]/1000:.1f} s')
    else:
        pyperclip.copy(original); notify('✗ Échec du traitement')

def _process(instruction, text, original):
    if not text:
        pyperclip.copy(original); notify('Aucun texte sélectionné'); return
    r = run_instruction(instruction, text)
    if r:
        _paste_then_restore(r, original)
        notify(f'✓ Terminé en {_state["last_ms"]/1000:.1f} s')
    else:
        pyperclip.copy(original)
        notify('✗ Échec — vérifiez que le modèle est installé')

INSTRUCTIONS = {
    'correct':   "Corrige les fautes d'orthographe et de grammaire.",
    'improve':   "Améliore le style, la fluidité et la clarté. Garde exactement le même sens.",
    'translate': None,   # construit à la volée avec la langue configurée
}

def _act(kind):
    def go():
        o = pyperclip.paste(); t = _get_selected() or _get_all()
        ins = INSTRUCTIONS[kind] or f"Traduis ce texte en {config['translation_language']}. Renvoie uniquement la traduction."
        _process(ins, t, o)
    threading.Thread(target=go, daemon=True).start()

def _act_prompt():
    schedule_tk(lambda: PromptWindow(_tk_root))

ACTIONS = {'correct': lambda: _act('correct'), 'improve': lambda: _act('improve'),
           'translate': lambda: _act('translate'), 'prompt': _act_prompt}

# ─── Raccourcis clavier ───────────────────────────────────────────────────────
def _to_pynput(key_str):
    for i in range(1, 13):
        if key_str == f'f{i}': return getattr(kb.Key, f'f{i}')
    m = {'escape': kb.Key.esc, 'tab': kb.Key.tab, 'delete': kb.Key.delete, 'insert': kb.Key.insert,
         'home': kb.Key.home, 'end': kb.Key.end, 'page_up': kb.Key.page_up,
         'page_down': kb.Key.page_down, 'space': kb.Key.space, 'pause': kb.Key.pause}
    if key_str in m: return m[key_str]
    if len(key_str) == 1: return kb.KeyCode.from_char(key_str)
    return None

_hotkey_map = {}     # touche pynput -> action (touches sans code virtuel, ex. lettres)
_hotkey_vks = {}     # code virtuel Windows -> action (touches F1-F12, Échap, etc.)
_WM_KEYDOWN, _WM_SYSKEYDOWN = 0x0100, 0x0104

def _on_press(key):
    act = _hotkey_map.get(key)
    if act: ACTIONS[act]()

def _win32_filter(msg, data):
    """Filtre bas niveau : FKey consomme ses raccourcis pour qu'ils n'atteignent
    pas l'application active. Indispensable : dans Word, F2 est une commande
    native (« déplacer le texte ») qui passe en mode modal et avale le collage."""
    act = _hotkey_vks.get(data.vkCode)
    if act is None:
        return True
    if msg in (_WM_KEYDOWN, _WM_SYSKEYDOWN):
        ACTIONS[act]()
    kb_listener.suppress_event()

def start_listener():
    global kb_listener, _hotkey_map, _hotkey_vks
    _hotkey_map, _hotkey_vks = {}, {}
    for action, key_str in config['shortcuts'].items():
        pk = _to_pynput(key_str)
        if not pk: continue
        vk = getattr(getattr(pk, 'value', pk), 'vk', None)
        if vk: _hotkey_vks[vk] = action
        else:  _hotkey_map[pk] = action
    if kb_listener and kb_listener.is_alive(): kb_listener.stop()
    kb_listener = kb.Listener(on_press=_on_press, win32_event_filter=_win32_filter)
    kb_listener.daemon = True; kb_listener.start()

# ─── Démarrage Windows (sans droits admin : dossier Startup de l'utilisateur) ──
def _startup_path():
    return os.path.join(os.getenv('APPDATA'), r'Microsoft\Windows\Start Menu\Programs\Startup', 'FKey.vbs')

def set_startup(enable):
    path = _startup_path()
    if enable:
        if getattr(sys, 'frozen', False):
            vbs = f'CreateObject("WScript.Shell").Run Chr(34) & "{sys.executable}" & Chr(34), 0, False\n'
        else:
            py = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'venv', 'Scripts', 'pythonw.exe')
            if not os.path.isfile(py): py = sys.executable.replace('python.exe', 'pythonw.exe')
            sc = os.path.abspath(__file__)
            vbs = (f'Set sh = CreateObject("WScript.Shell")\nsh.CurrentDirectory = "{os.path.dirname(sc)}"\n'
                   f'sh.Run Chr(34) & "{py}" & Chr(34) & " " & Chr(34) & "{sc}" & Chr(34), 0, False\n')
        try:
            with open(path, 'w', encoding='utf-8') as f: f.write(vbs)
        except Exception as e:
            print(f'Startup err: {e}')
    else:
        try: os.remove(path)
        except Exception: pass


# ═══════════════════════════════════════════════════════════════════════════════
#  FENÊTRES
# ═══════════════════════════════════════════════════════════════════════════════

_logo_cache = {}
def logo(parent, size):
    """Label affichant le logo FKey à la taille voulue (px)."""
    if size not in _logo_cache:
        try:
            _logo_cache[size] = ImageTk.PhotoImage(Image.open(LOGO_PNG).resize((size, size), Image.LANCZOS))
        except Exception:
            _logo_cache[size] = None
    img = _logo_cache[size]
    return tk.Label(parent, image=img, bg=parent.cget('bg')) if img else Label(parent, 'F', 'title')


class Window:
    """Base : fenêtre Toplevel claire avec titre natif."""
    def __init__(self, master, title, w, h, resizable=False):
        self.w = tk.Toplevel(master)
        self.w.title(title)
        self.w.configure(bg=C['bg'])
        self.w.resizable(resizable, resizable)
        try: self.w.iconbitmap(TRAY_ICON)
        except Exception: pass
        ui.center(self.w, w, h)
        self.w.lift(); self.w.focus_force()


# ─── Onboarding (premier lancement) ───────────────────────────────────────────
class Onboarding(Window):
    STEPS = ['Bienvenue', 'Modèle', 'Installation', 'Prêt']

    def __init__(self, master, on_done):
        super().__init__(master, 'Bienvenue dans FKey', 720, 620)
        self.on_done = on_done
        self.w.protocol('WM_DELETE_WINDOW', self._quit)
        self.step = 0
        self.model_var = tk.StringVar(value=local_llm.CATALOG[0][0])
        self.startup_var = tk.BooleanVar(value=True)
        self._build_frame()
        self._show(0)

    def _build_frame(self):
        self.header = tk.Frame(self.w, bg=C['bg'], padx=36, pady=22)
        self.header.pack(fill='x')
        self.dots = tk.Frame(self.header, bg=C['bg']); self.dots.pack(anchor='w')
        # Pied de page packé avant le corps : il reste visible quelle que soit la hauteur du contenu.
        self.footer = tk.Frame(self.w, bg=C['bg'], padx=36, pady=22); self.footer.pack(fill='x', side='bottom')
        self.body = tk.Frame(self.w, bg=C['bg'], padx=36); self.body.pack(fill='both', expand=True)

    def _draw_dots(self):
        for c in self.dots.winfo_children(): c.destroy()
        for i, name in enumerate(self.STEPS):
            done, cur = i < self.step, i == self.step
            f = tk.Frame(self.dots, bg=C['bg']); f.pack(side='left', padx=(0, 18))
            cv = tk.Canvas(f, width=22, height=22, bg=C['bg'], highlightthickness=0); cv.pack(side='left')
            if done:
                cv.create_oval(2, 2, 20, 20, fill=C['accent'], outline='')
                cv.create_text(11, 11, text='✓', fill='white', font=F('tiny'))
            elif cur:
                cv.create_oval(2, 2, 20, 20, fill=C['accent'], outline='')
                cv.create_text(11, 11, text=str(i+1), fill='white', font=F('tiny'))
            else:
                cv.create_oval(2, 2, 20, 20, fill=C['bg'], outline=C['border_strong'], width=2)
                cv.create_text(11, 11, text=str(i+1), fill=C['faint'], font=F('tiny'))
            Label(f, name, 'small', 'text' if cur else 'faint').pack(side='left', padx=(6, 0))

    def _clear(self):
        for c in self.body.winfo_children(): c.destroy()
        for c in self.footer.winfo_children(): c.destroy()

    def _show(self, i):
        self.step = i; self._draw_dots(); self._clear()
        getattr(self, f'_step{i}')()

    # Étape 0 — bienvenue
    def _step0(self):
        b = self.body
        hero = tk.Frame(b, bg=C['bg']); hero.pack(fill='x', pady=(10, 4))
        logo(hero, 72).pack(side='left', padx=(0, 16))
        Label(hero, 'Bienvenue dans FKey', 'display').pack(side='left')
        Label(b, 'Corrigez, améliorez et traduisez vos textes dans n\'importe quelle application,\n'
                 'd\'une simple touche. Tout se passe sur votre ordinateur : rien n\'est envoyé sur internet.',
              'body', 'muted', justify='left', wraplength=640).pack(anchor='w')
        grid = tk.Frame(b, bg=C['bg']); grid.pack(fill='x', pady=(28, 0))
        feats = [('⌨', 'Une touche, partout', 'Sélectionnez un texte, appuyez sur F2 : il est corrigé sur place.'),
                 ('🔒', '100 % privé', 'Le modèle tourne en local. Fonctionne sans connexion.'),
                 ('⚡', 'Rapide', 'Environ une seconde par phrase sur un ordinateur portable récent.')]
        for i, (ico, t, d) in enumerate(feats):
            c = Card(grid, padx=16, pady=14); c.grid(row=0, column=i, sticky='nsew', padx=(0, 12) if i < 2 else 0)
            grid.columnconfigure(i, weight=1, uniform='f')
            Label(c, ico, 'title').pack(anchor='w')
            Label(c, t, 'h3').pack(anchor='w', pady=(6, 2))
            Label(c, d, 'small', 'muted', wraplength=165, justify='left').pack(anchor='w', fill='x')
        Button(self.footer, 'Commencer  →', lambda: self._show(1), 'primary', 'lg').pack(side='right')
        Label(self.footer, 'Installation en 2 minutes · aucun compte requis', 'small', 'faint').pack(side='left', pady=10)

    # Étape 1 — choix du modèle
    def _step1(self):
        b = self.body
        Label(b, 'Choisissez votre modèle', 'title').pack(anchor='w', pady=(6, 2))
        Label(b, 'Un seul suffit. Vous pourrez en changer plus tard dans les réglages.', 'body', 'muted').pack(anchor='w', pady=(0, 14))
        for fname, url, mb, label, desc, rec in local_llm.CATALOG:
            present = os.path.isfile(os.path.join(local_llm.MODELS_DIR, fname))
            badges = []
            if rec and fname == local_llm.CATALOG[0][0]: badges.append(('Recommandé', 'accent'))
            if present: badges.append(('Déjà installé', 'success'))
            OptionCard(b, self.model_var, fname, label, desc, badges, meta=f'{mb/1000:.1f} Go à télécharger'.replace('.', ',')).pack(fill='x', pady=(0, 10))
        Label(b, 'Espace disque et mémoire : le modèle est chargé en RAM pendant l\'utilisation.', 'small', 'faint').pack(anchor='w', pady=(4, 0))
        Button(self.footer, 'Télécharger  →', lambda: self._show(2), 'primary', 'lg').pack(side='right')
        Button(self.footer, '←  Retour', lambda: self._show(0), 'ghost').pack(side='left', pady=4)

    # Étape 2 — téléchargement
    def _step2(self):
        b = self.body
        fname = self.model_var.get()
        label = local_llm.model_label(fname)
        Label(b, 'Installation', 'title').pack(anchor='w', pady=(6, 2))
        Label(b, f'Téléchargement de {label}. Vous pouvez réduire cette fenêtre, cela continue en arrière-plan.',
              'body', 'muted', wraplength=640, justify='left').pack(anchor='w', pady=(0, 24))
        card = Card(b, padx=24, pady=22); card.pack(fill='x')
        row = tk.Frame(card, bg=C['surface']); row.pack(fill='x')
        self.dl_title = Label(row, label, 'h2'); self.dl_title.pack(side='left')
        self.dl_badge = Badge(row, 'En cours', 'accent'); self.dl_badge.pack(side='right')
        self.dl_bar = ProgressBar(card, width=600); self.dl_bar.pack(fill='x', pady=(16, 8))
        self.dl_info = Label(card, 'Connexion…', 'small', 'muted'); self.dl_info.pack(anchor='w')
        self.dl_err = Label(b, '', 'small', 'danger', wraplength=640, justify='left'); self.dl_err.pack(anchor='w', pady=(10, 0))
        tips = Card(b, padx=24, pady=18); tips.pack(fill='x', pady=(14, 0))
        Label(tips, 'Pendant ce temps…', 'h3').pack(anchor='w', pady=(0, 6))
        for t in ('FKey fonctionne dans Word, Outlook, votre navigateur, Teams… partout où l\'on peut sélectionner du texte.',
                  'Sélectionnez un texte, appuyez sur F2 : il est remplacé par sa version corrigée.',
                  'Rien ne quitte votre ordinateur : le modèle travaille hors-ligne.'):
            r = tk.Frame(tips, bg=C['surface']); r.pack(fill='x', pady=2)
            Label(r, '•', 'body', 'accent_text').pack(side='left', anchor='n')
            Label(r, t, 'small', 'muted', wraplength=580, justify='left').pack(side='left', padx=(8, 0))
        self.btn_next = Button(self.footer, 'Continuer  →', lambda: self._show(3), 'primary', 'lg')
        self.btn_next.pack(side='right'); self.btn_next.set_enabled(False)
        self.btn_retry = Button(self.footer, 'Réessayer', lambda: self._show(2), 'secondary')
        self._start_download(fname)

    def _start_download(self, fname):
        t_start = time.time()
        def progress(label, done, total):
            pct = done / total if total else 0
            speed = done / max(0.1, time.time() - t_start) / 1e6
            eta = (total - done) / max(0.1, speed * 1e6) if total and speed > 0 else 0
            def ui_():
                self.dl_bar.set(pct)
                self.dl_title.config(text=label)
                self.dl_info.config(text=f'{done/1e6:.0f} / {total/1e6:.0f} Mo  ·  {speed:.1f} Mo/s  ·  ~{int(eta)} s restantes')
            self.w.after(0, ui_)
        def run():
            try:
                local_llm.install_all([fname], progress)
                ok = local_llm.has_local()
                def done():
                    self.dl_bar.set(1)
                    self.dl_badge.set('Installé' if ok else 'Erreur', 'success' if ok else 'danger')
                    self.dl_info.config(text='Prêt à l\'emploi.' if ok else 'Fichier manquant.')
                    self.btn_next.set_enabled(ok)
                    if not ok: self.btn_retry.pack(side='right', padx=(0, 10))
                self.w.after(0, done)
            except Exception as e:
                msg = f'Le téléchargement a échoué : {e}'
                def fail():
                    self.dl_badge.set('Erreur', 'danger')
                    self.dl_err.config(text=msg + '\nVérifiez votre connexion internet puis réessayez.')
                    self.btn_retry.pack(side='right', padx=(0, 10))
                self.w.after(0, fail)
        threading.Thread(target=run, daemon=True).start()

    # Étape 3 — prêt
    def _step3(self):
        b = self.body
        Label(b, 'Tout est prêt', 'title').pack(anchor='w', pady=(6, 2))
        Label(b, 'FKey reste discret dans la zone de notification. Voici comment l\'utiliser :', 'body', 'muted').pack(anchor='w', pady=(0, 16))
        card = Card(b); card.pack(fill='x')
        rows = [('correct', 'Corriger', 'orthographe et grammaire'),
                ('improve', 'Améliorer', 'style et fluidité'),
                ('translate', 'Traduire', f"vers {config['translation_language']}"),
                ('prompt', 'Instruction libre', 'saisissez ce que vous voulez faire')]
        for i, (k, t, d) in enumerate(rows):
            r = tk.Frame(card, bg=C['surface']); r.pack(fill='x', pady=(0, 10) if i < 3 else 0)
            KeyCap(r, KEY_LABELS.get(config['shortcuts'][k], config['shortcuts'][k].upper())).pack(side='left')
            Label(r, t, 'h3').pack(side='left', padx=(14, 6))
            Label(r, d, 'small', 'muted').pack(side='left')
        opt = Card(b); opt.pack(fill='x', pady=(12, 0))
        r = tk.Frame(opt, bg=C['surface']); r.pack(fill='x')
        Toggle(r, self.startup_var).pack(side='left')
        Label(r, 'Lancer FKey au démarrage de Windows', 'body').pack(side='left', padx=(12, 0))
        Label(opt, 'Recommandé pour avoir vos raccourcis toujours disponibles.', 'small', 'faint').pack(anchor='w', padx=(56, 0))
        Button(self.footer, 'Terminer', self._finish, 'primary', 'lg').pack(side='right')

    def _finish(self):
        config['onboarded'] = True
        config['model'] = self.model_var.get()
        config['startup'] = bool(self.startup_var.get())
        save_config(); set_startup(config['startup'])
        self.w.destroy(); self.on_done()

    def _quit(self):
        sys.exit(0)


# ─── Fenêtre principale ───────────────────────────────────────────────────────
class MainWindow(Window):
    PAGES = [('home', '⌂', 'Accueil'), ('model', '◈', 'Modèle'), ('keys', '⌨', 'Raccourcis'),
             ('general', '⚙', 'Réglages'), ('about', 'ⓘ', 'À propos')]

    def __init__(self, master):
        super().__init__(master, 'FKey', 900, 680)
        self.w.protocol('WM_DELETE_WINDOW', self.w.destroy)
        self._nav = {}; self._pages = {}; self._cur = None
        self._cap_act = None; self._cap_btns = {}
        self._unloading = False
        self._temp_sc = dict(config['shortcuts'])
        self._build()
        self._listener = lambda: self.w.after(0, self._refresh_status)
        on_state_change(self._listener)
        self.w.bind('<Destroy>', self._on_destroy)
        self.show('home')

    def _on_destroy(self, e):
        if e.widget is self.w and self._listener in _listeners:
            _listeners.remove(self._listener)

    def _build(self):
        sb = tk.Frame(self.w, bg=C['sidebar'], width=210); sb.pack(side='left', fill='y'); sb.pack_propagate(False)
        hdr = tk.Frame(sb, bg=C['sidebar'], padx=18, pady=20); hdr.pack(fill='x')
        logo(hdr, 40).pack(side='left', padx=(0, 10))
        t = tk.Frame(hdr, bg=C['sidebar']); t.pack(side='left')
        Label(t, 'FKey', 'title').pack(anchor='w')
        Label(t, 'Correcteur local', 'small', 'muted').pack(anchor='w')
        for key, ico, txt in self.PAGES:
            self._nav[key] = NavItem(sb, ico, txt, lambda k=key: self.show(k)); self._nav[key].pack(fill='x')
        foot = tk.Frame(sb, bg=C['sidebar'], padx=22, pady=18); foot.pack(side='bottom', fill='x')
        self.sb_badge = Badge(foot, 'Prêt', 'success'); self.sb_badge.pack(anchor='w')
        self.sb_model = Label(foot, '', 'small', 'muted'); self.sb_model.pack(anchor='w', pady=(6, 0))

        self.content = tk.Frame(self.w, bg=C['bg']); self.content.pack(side='left', fill='both', expand=True)
        for key, *_ in self.PAGES:
            sf = ScrollFrame(self.content)
            page = getattr(self, f'_pg_{key}')(sf.inner)
            page.pack(fill='both', expand=True, padx=32, pady=28)
            self._pages[key] = sf

    def show(self, key):
        self._cancel_capture()
        for k, item in self._nav.items(): item.set_active(k == key)
        for k, p in self._pages.items():
            p.pack_forget()
        self._pages[key].pack(fill='both', expand=True)
        self._cur = key
        self._refresh_status()

    def _refresh_status(self):
        txt, tone = status_text()
        self.sb_badge.set(txt, tone)
        m = model_name()
        self.sb_model.config(text=local_llm.model_label(m) if m else 'Aucun modèle')
        if hasattr(self, 'home_badge'):
            self.home_badge.set(txt, tone)
            self.home_model.config(text=local_llm.model_label(m) if m else 'Aucun modèle installé')
            ms = _state['last_ms']
            self.home_stats.config(text=(f'Dernier traitement : {ms/1000:.1f} s  ·  {_state["count"]} traitement(s) cette session'
                                         if ms else 'Aucun traitement pour l\'instant'))
            if _state['loading']:
                self.btn_load.set_busy('Chargement du modèle')
            elif self._unloading:
                self.btn_load.set_busy('Déchargement')
            elif not m:
                self.btn_load.set_busy(None)
                self.btn_load.set_text('Installer un modèle'); self.btn_load.command = lambda: self.show('model')
            else:
                self.btn_load.set_busy(None)
                self.btn_load.set_text('Décharger de la RAM' if engine.ready() else 'Charger en RAM')
                self.btn_load.command = self._toggle_engine
            for b in (self.btn_try_c, self.btn_try_i):
                if _state['loading'] or _state['busy']: b.set_busy('Patientez')
                else: b.set_busy(None)

    # — Accueil
    def _pg_home(self, parent):
        p = tk.Frame(parent, bg=C['bg'])
        Label(p, 'Accueil', 'display').pack(anchor='w')
        Label(p, 'Sélectionnez un texte dans n\'importe quelle application, puis appuyez sur un raccourci.',
              'body', 'muted', wraplength=600, justify='left').pack(anchor='w', pady=(2, 20))

        st = Card(p); st.pack(fill='x')
        r = tk.Frame(st, bg=C['surface']); r.pack(fill='x')
        self.home_model = Label(r, '', 'h2'); self.home_model.pack(side='left')
        self.home_badge = Badge(r, 'Prêt', 'success'); self.home_badge.pack(side='left', padx=(10, 0))
        self.btn_load = Button(r, 'Charger en RAM', self._toggle_engine, 'secondary', 'sm'); self.btn_load.pack(side='right')
        self.home_stats = Label(st, '', 'small', 'muted'); self.home_stats.pack(anchor='w', pady=(6, 0))

        Label(p, 'Raccourcis', 'h2').pack(anchor='w', pady=(18, 8))
        grid = tk.Frame(p, bg=C['bg']); grid.pack(fill='x')
        items = [('correct', 'Corriger', 'Orthographe et grammaire'), ('improve', 'Améliorer', 'Style et fluidité'),
                 ('translate', 'Traduire', 'Vers la langue choisie'), ('prompt', 'Instruction libre', 'Votre propre consigne')]
        self.home_keys = {}
        for i, (k, t, d) in enumerate(items):
            c = Card(grid, padx=16, pady=14); c.grid(row=i // 2, column=i % 2, sticky='nsew', padx=(0, 12) if i % 2 == 0 else 0, pady=(0, 12))
            grid.columnconfigure(i % 2, weight=1, uniform='k')
            rr = tk.Frame(c, bg=C['surface']); rr.pack(fill='x')
            kc = KeyCap(rr, KEY_LABELS.get(config['shortcuts'][k], config['shortcuts'][k].upper())); kc.pack(side='left')
            self.home_keys[k] = kc
            Label(rr, t, 'h3').pack(side='left', padx=(12, 0))
            Label(c, d, 'small', 'muted').pack(anchor='w', pady=(6, 0))

        Label(p, 'Essayer ici', 'h2').pack(anchor='w', pady=(6, 8))
        tc = Card(p, padx=14, pady=12); tc.pack(fill='x')
        self.try_in = tk.Text(tc, height=2, bg=C['surface'], fg=C['text'], relief='flat', font=F('body'),
                              insertbackground=C['accent'], wrap='word', highlightthickness=0)
        self.try_in.pack(fill='x'); self.try_in.insert('1.0', "Je vous écrit pour vous informé que la réunion est reporter à jeudi.")
        rr = tk.Frame(tc, bg=C['surface']); rr.pack(fill='x', pady=(8, 0))
        self.btn_try_c = Button(rr, 'Corriger', lambda: self._try('correct'), 'primary', 'sm'); self.btn_try_c.pack(side='left')
        self.btn_try_i = Button(rr, 'Améliorer', lambda: self._try('improve'), 'secondary', 'sm'); self.btn_try_i.pack(side='left', padx=(8, 0))
        self.try_out = Label(tc, '', 'body', 'accent_text', wraplength=560, justify='left'); self.try_out.pack(anchor='w', pady=(10, 0))
        return p

    def _toggle_engine(self):
        if _state['loading'] or self._unloading:
            return
        if engine.ready():
            self._unloading = True; self._refresh_status()
            def run():
                stop_engine(); self._unloading = False; _emit()
            threading.Thread(target=run, daemon=True).start()
        else:
            start_engine_async()

    def _try(self, kind):
        txt = self.try_in.get('1.0', 'end').strip()
        if not txt: return
        self.try_out.config(text='…', fg=C['muted'])
        def run():
            r = run_instruction(INSTRUCTIONS[kind], txt)
            self.w.after(0, lambda: self.try_out.config(text=r or 'Échec — modèle indisponible.',
                                                        fg=C['accent_text'] if r else C['danger']))
        threading.Thread(target=run, daemon=True).start()

    # — Modèle
    def _pg_model(self, parent):
        p = tk.Frame(parent, bg=C['bg'])
        Label(p, 'Modèle', 'display').pack(anchor='w')
        Label(p, 'Le modèle actif est chargé en mémoire lors du premier traitement.', 'body', 'muted').pack(anchor='w', pady=(2, 20))
        self.model_var = tk.StringVar(value=model_name())
        self.model_var.trace_add('write', lambda *a: self._model_changed())
        self.model_list = tk.Frame(p, bg=C['bg']); self.model_list.pack(fill='x')
        self._fill_models()
        r = tk.Frame(p, bg=C['bg']); r.pack(fill='x', pady=(6, 0))
        Button(r, '📁  Ouvrir le dossier des modèles', lambda: os.startfile(local_llm.MODELS_DIR), 'ghost', 'sm').pack(side='left')
        Label(p, 'Vous pouvez y déposer n\'importe quel fichier .gguf : il apparaîtra dans cette liste.', 'small', 'faint').pack(anchor='w', padx=(14, 0), pady=(4, 0))
        self.model_info = Label(p, '', 'small', 'muted'); self.model_info.pack(anchor='w', pady=(10, 0))
        return p

    def _fill_models(self):
        for c in self.model_list.winfo_children(): c.destroy()
        installed = set(local_llm.list_models())
        seen = set()
        for fname, url, mb, label, desc, rec in local_llm.CATALOG:
            seen.add(fname)
            badges = [('Recommandé', 'accent')] if fname == local_llm.CATALOG[0][0] else []
            badges.append(('Installé', 'success') if fname in installed else ('À télécharger', 'neutral'))
            OptionCard(self.model_list, self.model_var, fname, label, desc, badges, meta=f'{mb/1000:.1f} Go').pack(fill='x', pady=(0, 10))
        for fname in sorted(installed - seen):
            OptionCard(self.model_list, self.model_var, fname, local_llm.model_label(fname),
                       local_llm.model_desc(fname), [('Installé', 'success'), ('Personnalisé', 'neutral')]).pack(fill='x', pady=(0, 10))
        self.dl_frame = tk.Frame(self.model_list, bg=C['bg']); self.dl_frame.pack(fill='x')

    def _model_changed(self):
        fname = self.model_var.get()
        for c in self.dl_frame.winfo_children(): c.destroy()
        if fname in local_llm.list_models():
            config['model'] = fname; save_config()
            if engine.alive() and engine.model != fname:
                threading.Thread(target=lambda: (stop_engine(), config.get('preload') and ensure_engine()), daemon=True).start()
            self.model_info.config(text=f'Modèle actif : {local_llm.model_label(fname)}')
            self._refresh_status()
        else:
            self.model_info.config(text='')
            card = Card(self.dl_frame, padx=18, pady=14); card.pack(fill='x')
            r = tk.Frame(card, bg=C['surface']); r.pack(fill='x')
            Label(r, f'{local_llm.model_label(fname)} n\'est pas encore téléchargé.', 'body').pack(side='left')
            bar = ProgressBar(card, width=560); info = Label(card, '', 'small', 'muted')
            def go():
                btn.set_enabled(False); bar.pack(fill='x', pady=(12, 6)); info.pack(anchor='w')
                def prog(label, done, total):
                    self.w.after(0, lambda: (bar.set(done / total if total else 0),
                                             info.config(text=f'{done/1e6:.0f} / {total/1e6:.0f} Mo')))
                def run():
                    try:
                        local_llm.install_all([fname], prog)
                        self.w.after(0, lambda: (self._fill_models(), self._model_changed()))
                    except Exception as e:
                        msg = f'Échec : {e}'
                        self.w.after(0, lambda: (info.config(text=msg, fg=C['danger']), btn.set_enabled(True)))
                threading.Thread(target=run, daemon=True).start()
            btn = Button(r, 'Télécharger', go, 'primary', 'sm'); btn.pack(side='right')

    # — Raccourcis
    def _pg_keys(self, parent):
        p = tk.Frame(parent, bg=C['bg'])
        Label(p, 'Raccourcis', 'display').pack(anchor='w')
        Label(p, 'Cliquez sur une touche puis appuyez sur la nouvelle touche. Échap pour annuler.', 'body', 'muted').pack(anchor='w', pady=(2, 20))
        card = Card(p, padx=22, pady=8); card.pack(fill='x')
        rows = [('correct', 'Corriger', 'Orthographe et grammaire'), ('improve', 'Améliorer', 'Style, fluidité, clarté'),
                ('translate', 'Traduire', 'Vers la langue choisie ci-dessous'), ('prompt', 'Instruction libre', 'Ouvre une petite fenêtre de saisie')]
        for i, (k, t, d) in enumerate(rows):
            r = tk.Frame(card, bg=C['surface'], pady=12); r.pack(fill='x')
            left = tk.Frame(r, bg=C['surface']); left.pack(side='left', fill='x', expand=True)
            Label(left, t, 'h3').pack(anchor='w'); Label(left, d, 'small', 'muted').pack(anchor='w')
            kc = KeyCap(r, KEY_LABELS.get(self._temp_sc[k], self._temp_sc[k].upper()), cursor='hand2')
            kc.pack(side='right'); kc.bind('<Button-1>', lambda e, a=k: self._start_capture(a))
            self._cap_btns[k] = kc
            if i < 3: tk.Frame(card, bg=C['border'], height=1).pack(fill='x')

        Label(p, 'Langue de traduction', 'h2').pack(anchor='w', pady=(22, 8))
        lc = Card(p); lc.pack(fill='x')
        self.lang_var = tk.StringVar(value=config['translation_language'])
        g = tk.Frame(lc, bg=C['surface']); g.pack(anchor='w')
        for i, lang in enumerate(LANGS):
            rb = tk.Radiobutton(g, text=lang, variable=self.lang_var, value=lang, bg=C['surface'], fg=C['text'],
                                activebackground=C['surface'], selectcolor=C['surface'], font=F('body'),
                                command=lambda: (config.__setitem__('translation_language', self.lang_var.get()), save_config()))
            rb.grid(row=i // 5, column=i % 5, sticky='w', padx=(0, 18), pady=2)
        return p

    def _start_capture(self, action):
        self._cancel_capture(); self._cap_act = action
        self._cap_btns[action].config(text='…', bg=C['accent_soft'], fg=C['accent_text'])
        self.w.bind('<KeyPress>', self._on_key); self.w.bind('<Escape>', lambda e: self._cancel_capture())
        self.w.focus_force()

    @staticmethod
    def _sym_to_key(sym):
        if sym.startswith('F') and sym[1:].isdigit() and 1 <= int(sym[1:]) <= 12: return sym.lower()
        m = {'Escape': 'escape', 'Tab': 'tab', 'space': 'space', 'Delete': 'delete', 'Insert': 'insert',
             'Home': 'home', 'End': 'end', 'Prior': 'page_up', 'Next': 'page_down', 'Pause': 'pause'}
        return m.get(sym, sym.lower())

    def _on_key(self, event):
        if event.keysym in ('Shift_L', 'Shift_R', 'Control_L', 'Control_R', 'Alt_L', 'Alt_R', 'Super_L', 'Super_R'): return
        key = self._sym_to_key(event.keysym)
        if key == 'escape': self._cancel_capture(); return
        for act, k in self._temp_sc.items():
            if k == key and act != self._cap_act:
                self._cap_btns[self._cap_act].config(text='Déjà utilisée', bg=C['danger_soft'], fg=C['danger'])
                self.w.after(900, self._cancel_capture); return
        self._temp_sc[self._cap_act] = key
        config['shortcuts'] = dict(self._temp_sc); save_config(); start_listener()
        for k, kc in self.home_keys.items():
            kc.config(text=KEY_LABELS.get(config['shortcuts'][k], config['shortcuts'][k].upper()))
        self._cancel_capture()

    def _cancel_capture(self):
        if self._cap_act:
            k = self._temp_sc[self._cap_act]
            self._cap_btns[self._cap_act].config(text=KEY_LABELS.get(k, k.upper()), bg=C['key_bg'], fg=C['text'])
            self._cap_act = None
        self.w.unbind('<KeyPress>'); self.w.unbind('<Escape>')

    # — Réglages
    def _pg_general(self, parent):
        p = tk.Frame(parent, bg=C['bg'])
        Label(p, 'Réglages', 'display').pack(anchor='w')
        Label(p, 'Comportement de l\'application.', 'body', 'muted').pack(anchor='w', pady=(2, 20))
        card = Card(p, padx=22, pady=6); card.pack(fill='x')
        self.v_startup = tk.BooleanVar(value=config['startup'])
        self.v_preload = tk.BooleanVar(value=config['preload'])
        self.v_notify  = tk.BooleanVar(value=config['notify'])
        rows = [(self.v_startup, 'Lancer au démarrage de Windows', 'FKey s\'ouvre discrètement dans la zone de notification.', self._apply_startup),
                (self.v_preload, 'Charger le modèle dès le lancement', 'Le premier raccourci répond immédiatement (utilise ~2,7 Go de RAM en permanence).', self._apply_preload),
                (self.v_notify,  'Notifications', 'Un petit message confirme chaque traitement.', self._apply_notify)]
        for i, (var, t, d, cmd) in enumerate(rows):
            r = tk.Frame(card, bg=C['surface'], pady=12); r.pack(fill='x')
            Toggle(r, var, cmd).pack(side='left', anchor='n', pady=2)
            txt = tk.Frame(r, bg=C['surface']); txt.pack(side='left', padx=(14, 0))
            Label(txt, t, 'h3').pack(anchor='w'); Label(txt, d, 'small', 'muted', wraplength=520, justify='left').pack(anchor='w')
            if i < 2: tk.Frame(card, bg=C['border'], height=1).pack(fill='x')
        Label(p, 'Maintenance', 'h2').pack(anchor='w', pady=(22, 8))
        mc = Card(p); mc.pack(fill='x')
        r = tk.Frame(mc, bg=C['surface']); r.pack(fill='x')
        Button(r, 'Réactiver les raccourcis', start_listener, 'secondary', 'sm').pack(side='left')
        Button(r, 'Ouvrir le journal', lambda: os.startfile(LOG_FILE), 'secondary', 'sm').pack(side='left', padx=(8, 0))
        Label(mc, 'Si un raccourci ne répond plus (après une mise en veille par exemple), réactivez-les ici.', 'small', 'faint').pack(anchor='w', pady=(8, 0))
        return p

    def _apply_startup(self):
        config['startup'] = bool(self.v_startup.get()); save_config(); set_startup(config['startup'])
    def _apply_preload(self):
        config['preload'] = bool(self.v_preload.get()); save_config()
        if config['preload']: start_engine_async()
    def _apply_notify(self):
        config['notify'] = bool(self.v_notify.get()); save_config()

    # — À propos
    def _pg_about(self, parent):
        p = tk.Frame(parent, bg=C['bg'])
        Label(p, 'À propos', 'display').pack(anchor='w')
        Label(p, f'FKey {VERSION} — correcteur de texte local', 'body', 'muted').pack(anchor='w', pady=(2, 20))
        card = Card(p); card.pack(fill='x')
        for t, d in [('Confidentialité', 'Vos textes sont traités sur cet ordinateur uniquement. FKey ne se connecte à internet que pour télécharger un modèle, à votre demande.'),
                     ('Moteur', 'llama.cpp (licence MIT) — modèles Gemma 4 (Google, licence Gemma) et Luth-2 (Kurakura AI, Apache 2.0).'),
                     ('Fonctionnement', 'FKey simule Ctrl+C / Ctrl+V dans l\'application active : il fonctionne partout où l\'on peut sélectionner du texte.')]:
            Label(card, t, 'h3').pack(anchor='w', pady=(6, 2))
            Label(card, d, 'small', 'muted', wraplength=600, justify='left').pack(anchor='w', pady=(0, 8))
        return p


# ─── Instruction libre ────────────────────────────────────────────────────────
class PromptWindow(Window):
    def __init__(self, master):
        super().__init__(master, 'FKey — Instruction', 540, 300)
        self.w.attributes('-topmost', True)
        self._orig = pyperclip.paste(); self._text = ''
        b = tk.Frame(self.w, bg=C['bg'], padx=24, pady=20); b.pack(fill='both', expand=True)
        Label(b, 'Que faire du texte sélectionné ?', 'h2').pack(anchor='w')
        Label(b, 'Ex. « Rends ce texte plus formel », « Résume en 3 points », « Traduis en italien »',
              'small', 'muted', wraplength=480, justify='left').pack(anchor='w', pady=(2, 10))
        fr = tk.Frame(b, bg=C['border_strong'], padx=1, pady=1); fr.pack(fill='x')
        self.entry = tk.Text(fr, height=3, bg=C['surface'], fg=C['text'], relief='flat', bd=8, font=F('body'),
                             insertbackground=C['accent'], wrap='word', highlightthickness=0)
        self.entry.pack(fill='x')
        r = tk.Frame(b, bg=C['bg']); r.pack(fill='x', pady=(12, 0))
        self.cap = Button(r, 'Capturer la sélection (3 s)', self._prep_cap, 'secondary'); self.cap.pack(side='left')
        Button(r, 'Appliquer  →', self._submit, 'primary').pack(side='right')
        self.status = Label(b, '', 'small', 'muted', wraplength=480, justify='left'); self.status.pack(anchor='w', pady=(8, 0))
        self.w.bind('<Return>', self._submit); self.w.bind('<Escape>', lambda e: self.w.destroy())
        self.entry.focus_set()

    def _prep_cap(self):
        self.cap.set_enabled(False); self.status.config(text='Cliquez dans la fenêtre contenant votre texte…', fg=C['warn'])
        for i, s in enumerate(('3', '2', '1')):
            self.w.after(1000 * i, lambda s=s: self.cap.set_text(f'Capture dans {s}…'))
        self.w.after(3000, self._do_cap)

    def _do_cap(self):
        self.w.withdraw()
        def run():
            time.sleep(0.15)
            saved = pyperclip.paste(); pyperclip.copy(''); _ctrl('c'); time.sleep(0.07)
            got = pyperclip.paste(); pyperclip.copy(saved)
            self.w.after(0, lambda: self._cap_done(got))
        threading.Thread(target=run, daemon=True).start()

    def _cap_done(self, got):
        self.w.deiconify(); self.w.lift(); self.w.focus_force(); self.cap.set_enabled(True)
        if got:
            self._text = got; self.cap.set_text('✓ Texte capturé')
            self.status.config(text=f'« {got[:60]}{"…" if len(got) > 60 else ""} »', fg=C['success'])
        else:
            self.cap.set_text('Réessayer'); self.status.config(text='Aucun texte détecté.', fg=C['danger'])

    def _submit(self, _=None):
        p = self.entry.get('1.0', 'end').strip()
        if not p: self.w.destroy(); return
        t, o = self._text, self._orig; self.w.destroy()
        def run():
            if t: _process(p, t, o)
            else: run_free(p, o)
        threading.Thread(target=run, daemon=True).start()


# ─── Zone de notification ─────────────────────────────────────────────────────
_main_win = None
def _open_main(ico=None, itm=None):
    def fn():
        global _main_win
        if _main_win and _main_win.w.winfo_exists():
            _main_win.w.deiconify(); _main_win.w.lift(); _main_win.w.focus_force()
        else:
            _main_win = MainWindow(_tk_root)
    schedule_tk(fn)

def _quit(ico=None, itm=None):
    if kb_listener: kb_listener.stop()
    stop_engine()
    if tray_icon: tray_icon.stop()
    schedule_tk(lambda: _tk_root.after(100, _tk_root.destroy))

def _tray_status(itm):
    txt, _ = status_text(); m = model_name()
    return f'{txt}  ·  {local_llm.model_label(m) if m else "aucun modèle"}'

def _model_menu():
    def choose(fname):
        def fn(ico, itm):
            config['model'] = fname; save_config()
            threading.Thread(target=lambda: (stop_engine(), ensure_engine()), daemon=True).start()
        return fn
    return pystray.Menu(lambda: [
        pitem(local_llm.model_label(f), choose(f), checked=(lambda f=f: lambda itm: model_name() == f)(), radio=True)
        for f in local_llm.list_models()] or [pitem('Aucun modèle installé', None, enabled=False)])

def setup_tray():
    global tray_icon
    try: img = Image.open(TRAY_ICON); img.size  # ICO multi-tailles : PIL prend la plus grande
    except Exception: img = Image.new('RGB', (64, 64), C['accent'])
    menu = pystray.Menu(
        pitem(_tray_status, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pitem('Ouvrir FKey', _open_main, default=True),
        pitem('Instruction libre…', lambda i, m: _act_prompt()),
        pitem('Modèle', _model_menu()),
        pystray.Menu.SEPARATOR,
        pitem('Lancer au démarrage', lambda i, m: (config.__setitem__('startup', not config['startup']), save_config(), set_startup(config['startup'])),
              checked=lambda itm: config['startup']),
        pitem('Réactiver les raccourcis', lambda i, m: start_listener()),
        pystray.Menu.SEPARATOR,
        pitem('Quitter', _quit),
    )
    tray_icon = pystray.Icon('FKey', img, 'FKey — correcteur local', menu)
    tray_icon.run_detached()


# ─── Entrée ───────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    ui.enable_dpi()
    print('=' * 50); print(f'FKey {VERSION} démarré.'); print('=' * 50)
    _tk_root = tk.Tk(); _tk_root.withdraw()

    def _start():
        start_listener(); setup_tray()
        if config.get('preload', True): start_engine_async()
        print('Prêt.')

    if not config.get('onboarded') or not local_llm.has_local():
        Onboarding(_tk_root, lambda: (_start(), _open_main()))
    else:
        _start()
    _tk_root.mainloop()
