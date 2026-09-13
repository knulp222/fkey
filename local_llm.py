"""Zora — moteur IA local (llama.cpp / tout serveur compatible OpenAI).

Partagé par correcteur.py et tester_modeles_groq.py.
Aucune dépendance externe : urllib uniquement, pour rester léger et
fonctionner sans le moindre accès réseau.
"""
import os, sys, time, json, subprocess, zipfile, shutil, urllib.request, urllib.error

# ─── Chemins ──────────────────────────────────────────────────────────────────
def _data(r):
    base = (os.path.dirname(sys.executable) if getattr(sys, 'frozen', False)
            else os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, r)

LLAMA_DIR   = _data('llama')
MODELS_DIR  = _data('models')
SERVER_EXE  = os.path.join(LLAMA_DIR, 'llama-server.exe')
SERVER_LOG  = os.path.join(LLAMA_DIR, 'llama_server_log.txt')
DEFAULT_PORT = 8178           # serveur résident de Zora
BENCH_PORT   = 8179           # serveur temporaire du comparateur

# ─── Catalogue ────────────────────────────────────────────────────────────────
LLAMA_RELEASE = 'b10941'
LLAMA_ZIP_URL = (f'https://github.com/ggml-org/llama.cpp/releases/download/'
                 f'{LLAMA_RELEASE}/llama-{LLAMA_RELEASE}-bin-win-cpu-x64.zip')

# (fichier, url, taille Mo, libellé, description, recommandé)
# Mesuré sur i7-1165G7 / 16 Go, 16 phrases pièges FR+EN, réglage ngram-simple 2/4, 8 threads.
CATALOG = [
    ('gemma-4-E2B-it-q4_0.gguf',
     'https://huggingface.co/google/gemma-4-E2B-it-qat-q4_0-gguf/resolve/main/gemma-4-E2B_q4_0-it.gguf',
     3350, 'Gemma 4 E2B', 'Meilleure qualité de correction (FR/EN). ~1 s par phrase, 2,7 Go de mémoire.', True),
    ('Luth-2-2B-Q4_K_M.gguf',
     'https://huggingface.co/kurakurai/Luth-2-2B-GGUF/resolve/main/Luth-2-2B-Q4_K_M.gguf',
     1274, 'Luth-2 2B', 'Plus léger, spécialisé français. Bon compromis avec peu de mémoire (2 Go).', True),
    ('Luth-2-0.8B-Q4_K_M.gguf',
     'https://huggingface.co/kurakurai/Luth-2-0.8B-GGUF/resolve/main/Luth-2-0.8B-Q4_K_M.gguf',
     529, 'Luth-2 0.8B', 'Le plus petit et le plus rapide (1 Go), mais rate les fautes subtiles.', False),
]
_CATALOG_BY_FILE = {c[0]: c for c in CATALOG}

def model_label(fname):
    c = _CATALOG_BY_FILE.get(fname)
    return c[3] if c else os.path.splitext(fname)[0]

def model_desc(fname):
    c = _CATALOG_BY_FILE.get(fname)
    if c: return c[4]
    try:
        mb = os.path.getsize(os.path.join(MODELS_DIR, fname)) / 1e6
        return f'Fichier GGUF personnalisé, ~{mb/1000:.1f} Go'
    except OSError:
        return 'Fichier GGUF personnalisé'

def list_models():
    """Fichiers .gguf présents dans models\\ (les 'mmproj' vision sont ignorés)."""
    if not os.path.isdir(MODELS_DIR):
        return []
    files = [f for f in os.listdir(MODELS_DIR)
             if f.lower().endswith('.gguf') and 'mmproj' not in f.lower()]
    order = {c[0]: i for i, c in enumerate(CATALOG)}
    return sorted(files, key=lambda f: (order.get(f, 99), f.lower()))

def default_model():
    m = list_models()
    return m[0] if m else ''

def server_available():
    return os.path.isfile(SERVER_EXE)

def has_local():
    return server_available() and bool(list_models())

# ─── Prompt few-shot ──────────────────────────────────────────────────────────
# Les petits modèles ont besoin d'exemples explicites, présentés comme de vrais
# tours user/assistant. Ce préfixe est identique à chaque appel, donc mis en
# cache par llama-server : il ne coûte rien après le premier appel.
SYSTEM_PROMPT = (
    "Tu es Zora, un correcteur de texte. On te donne une instruction et un texte.\n"
    "Règles absolues :\n"
    "- Renvoie UNIQUEMENT le texte final, sans explication, sans guillemets, sans préambule.\n"
    "- Garde la langue du texte : un texte en français reste en français, un texte en anglais "
    "reste en anglais. Ne traduis jamais sauf si l'instruction le demande.\n"
    "- Garde le sens, le ton, les retours à la ligne et la ponctuation d'origine.\n"
    "- Ne modifie que ce qui est nécessaire à l'instruction. Ne rajoute rien, n'enlève rien, "
    "ne change ni les mots ni le temps des verbes : corrige seulement l'orthographe et les accords.\n"
    "- Vérifie surtout les homophones : on/ont, a/à, ce/se, c'est/s'est, ces/ses, sa/ça, "
    "et/est, ou/où, tout/tous, -er/-é/-ez, ainsi que les accords sujet-verbe et participe passé.\n"
    "- Si le texte est déjà correct, renvoie-le tel quel."
)

def _wrap(instruction, text):
    return (f'Instruction : "{instruction}"\n\n'
            'Applique cette instruction au texte ci-dessous. '
            f'Renvoie UNIQUEMENT le texte modifié.\n\nTEXTE :\n"""\n{text}\n"""')

_CORR = "Corrige les fautes d'orthographe et de grammaire."
FEW_SHOT = [
    (_wrap(_CORR, "Je suis aller au marché et j'ai acheter des pomme."),
     "Je suis allé au marché et j'ai acheté des pommes."),
    (_wrap(_CORR, "Sa fait longtemps qu'on ce parle plus, il faut qu'on ce voit."),
     "Ça fait longtemps qu'on ne se parle plus, il faut qu'on se voie."),
    (_wrap(_CORR, "Il c'est trompé de date et tout les invités son venu pour rien."),
     "Il s'est trompé de date et tous les invités sont venus pour rien."),
    (_wrap(_CORR, "Merci pour votre retour, on se voit demain."),
     "Merci pour votre retour, on se voit demain."),
    (_wrap(_CORR, "Bonjour Marie,\nje t'envoie le fichier corriger.\nBonne journée"),
     "Bonjour Marie,\nje t'envoie le fichier corrigé.\nBonne journée"),
    (_wrap("Améliore le style, la fluidité et la clarté. Garde exactement le même sens.",
           "Le projet il avance bien mais y'a des trucs à checker."),
     "Le projet avance bien, mais certains points restent à vérifier."),
]

def build_messages(prompt, system=SYSTEM_PROMPT):
    msgs = [{'role': 'system', 'content': system}]
    for u, a in FEW_SHOT:
        msgs.append({'role': 'user', 'content': u})
        msgs.append({'role': 'assistant', 'content': a})
    msgs.append({'role': 'user', 'content': prompt})
    return msgs

def estimate_max_tokens(text):
    """Borne la génération : ~1 token / 3 caractères en français, marge 1.6×."""
    if not text:
        return 1024
    return int(len(text) / 3 * 1.6) + 48

# ─── HTTP minimal ─────────────────────────────────────────────────────────────
def _get(url, timeout=2):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, r.read().decode('utf-8', 'replace')

def _post_json(url, payload, timeout=120):
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))

def norm_url(base_url):
    """'http://localhost:11434/v1/' -> 'http://localhost:11434' (le /v1 est ajouté par nous)."""
    u = (base_url or '').strip().rstrip('/')
    if u.endswith('/v1'):
        u = u[:-3]
    if u and not u.startswith('http'):
        u = 'http://' + u
    return u

def server_ready(base_url):
    """True si un serveur OpenAI-compatible répond (llama-server, Ollama, LM Studio)."""
    base = norm_url(base_url)
    if not base:
        return False
    for path in ('/health', '/v1/models'):
        try:
            st, body = _get(base + path)
            if st == 200 and '"loading' not in body:
                return True
        except urllib.error.HTTPError as e:
            if e.code == 503:      # llama-server : modèle en cours de chargement
                return False
        except Exception:
            pass
    return False

def list_remote_models(base_url):
    """Modèles exposés par un serveur externe (/v1/models)."""
    try:
        st, body = _get(norm_url(base_url) + '/v1/models', timeout=3)
        return [m.get('id', '') for m in json.loads(body).get('data', [])]
    except Exception:
        return []

def chat(base_url, prompt, model='', max_tokens=512, system=SYSTEM_PROMPT, timeout=120):
    """Appel /v1/chat/completions. Renvoie le texte, ou lève une exception."""
    payload = {
        'model': model or 'local',
        'messages': build_messages(prompt, system),
        'temperature': 0,
        'max_tokens': max_tokens,
        'stream': False,
        # Qwen3 : coupe le mode "réflexion" (sinon 200 tokens de raisonnement avant la réponse)
        'chat_template_kwargs': {'enable_thinking': False},
        # Ollama comprend celui-ci
        'think': False,
    }
    res = _post_json(norm_url(base_url) + '/v1/chat/completions', payload, timeout=timeout)
    msg = res['choices'][0]['message']
    out = (msg.get('content') or '').strip()
    # Sécurité : certains modèles glissent quand même un bloc <think>
    if '</think>' in out:
        out = out.split('</think>', 1)[1].strip()
    # Le prompt encadre le texte par des guillemets triples ; on les retire si copiés
    if out.startswith('"""') and out.endswith('"""'):
        out = out[3:-3].strip()
    return out

# ─── Job Object : le serveur meurt avec Zora, même en cas de crash ───────────
_job = None
def _bind_to_job(proc):
    """Rattache le sous-processus à un Job Windows 'kill on close' : si Zora
    disparaît (crash, taskkill), Windows termine llama-server automatiquement."""
    global _job
    if os.name != 'nt':
        return
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        if _job is None:
            _job = k32.CreateJobObjectW(None, None)
            class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                            ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                            ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                            ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD),
                            ('SchedulingClass', wintypes.DWORD)]
            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [(n, ctypes.c_uint64) for n in ('ReadOperationCount', 'WriteOperationCount',
                            'OtherOperationCount', 'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]
            class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [('BasicLimitInformation', JOBOBJECT_BASIC_LIMIT_INFORMATION), ('IoInfo', IO_COUNTERS),
                            ('ProcessMemoryLimit', ctypes.c_size_t), ('JobMemoryLimit', ctypes.c_size_t),
                            ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]
            info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = 0x2000   # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            k32.SetInformationJobObject(_job, 9, ctypes.byref(info), ctypes.sizeof(info))  # 9 = ExtendedLimitInformation
        k32.AssignProcessToJobObject(_job, int(proc._handle))
    except Exception as e:
        print(f'[local] job object : {e}')

# ─── Serveur llama.cpp embarqué ───────────────────────────────────────────────
class LlamaServer:
    """Lance / arrête llama-server.exe et le garde résident en RAM."""

    def __init__(self, port=DEFAULT_PORT):
        self.port  = port
        self.proc  = None
        self.model = None
        self.load_time = 0.0

    @property
    def url(self):
        return f'http://127.0.0.1:{self.port}'

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def ready(self):
        return self.alive() and server_ready(self.url)

    def start(self, model_file, ctx=4096, threads=None, spec='ngram-simple', wait=90, extra_args=None):
        """Démarre (ou redémarre avec un autre modèle). Bloquant jusqu'à /health OK."""
        if not model_file:
            return False
        path = model_file if os.path.isabs(model_file) else os.path.join(MODELS_DIR, model_file)
        if not os.path.isfile(path) or not server_available():
            return False
        if self.alive() and self.model == model_file:
            return self.ready() or self._wait_ready(wait)
        self.stop()

        if threads is None:
            # Mesuré sur i7-1165G7 : tous les threads logiques (8) battent les 4 cœurs
            # physiques de ~12 %, en génération comme en lecture du prompt.
            threads = os.cpu_count() or 4

        cmd = [SERVER_EXE, '-m', path,
               '--host', '127.0.0.1', '--port', str(self.port),
               '-c', str(ctx), '-t', str(threads), '--parallel', '1',
               '--jinja', '--no-webui', '--reasoning-budget', '0']
        if spec and spec != 'none':
            # Décodage spéculatif sans modèle brouillon : idéal en correction,
            # la sortie recopie l'entrée et seuls les mots corrigés sont "payés".
            cmd += ['--spec-type', spec]
            if spec == 'ngram-simple':
                # Fenêtres courtes (lookup 2 tokens, brouillon 4) : les valeurs par
                # défaut ne déclenchent presque jamais sur une phrase ; avec 2/4 on
                # mesure ~90 % d'acceptation et +50 % de tokens/s.
                cmd += ['--spec-ngram-simple-size-n', '2', '--spec-ngram-simple-size-m', '4']
        if extra_args:
            cmd += list(extra_args)

        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        os.makedirs(LLAMA_DIR, exist_ok=True)
        log = open(SERVER_LOG, 'w', encoding='utf-8', errors='replace')
        t0 = time.time()
        try:
            self.proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT,
                                         cwd=LLAMA_DIR, creationflags=flags)
        except Exception as e:
            print(f'[local] lancement impossible : {e}')
            self.proc = None
            return False
        _bind_to_job(self.proc)
        self.model = model_file
        ok = self._wait_ready(wait)
        self.load_time = time.time() - t0
        if ok:
            print(f'[local] {model_file} prêt en {self.load_time:.1f}s '
                  f'(port {self.port}, threads {threads}, spec {spec})')
            self.warmup()
        else:
            print(f'[local] échec de démarrage de {model_file} — voir {SERVER_LOG}')
            self.stop()
        return ok

    def _wait_ready(self, wait):
        t_end = time.time() + wait
        while time.time() < t_end:
            if not self.alive():
                return False
            if server_ready(self.url):
                return True
            time.sleep(0.25)
        return False

    def stop(self):
        if self.proc is not None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                try: self.proc.kill()
                except Exception: pass
        self.proc = None
        self.model = None

    def warmup(self):
        """Pré-calcule le préfixe few-shot (~500 tokens, ~5 s) pour que le premier
        vrai appel profite déjà du cache de prompt (mesuré : 10 s → 1.3 s)."""
        try:
            t0 = time.time()
            self.chat(_wrap(_CORR, 'Bonjour'), 8, timeout=60)
            print(f'[local] cache de prompt prêt en {time.time()-t0:.1f}s')
        except Exception as e:
            print(f'[local] échauffement : {e}')

    def chat(self, prompt, max_tokens=512, timeout=120):
        return chat(self.url, prompt, self.model or 'local', max_tokens, timeout=timeout)

# ─── Téléchargement des ressources ────────────────────────────────────────────
def _download(url, dest, progress=None, label=''):
    """Télécharge en streaming vers dest.part puis renomme. progress(label, done, total)."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + '.part'
    req = urllib.request.Request(url, headers={'User-Agent': 'Zora/3.0'})
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, 'wb') as f:
        total = int(r.headers.get('Content-Length') or 0)
        done = 0
        while True:
            chunk = r.read(1024 * 256)
            if not chunk:
                break
            f.write(chunk); done += len(chunk)
            if progress:
                progress(label, done, total)
    os.replace(tmp, dest)

def install_server(progress=None):
    """Récupère llama-server.exe (+ DLL) depuis la release GitHub."""
    if server_available():
        return True
    zpath = os.path.join(LLAMA_DIR, 'llama-cpu-x64.zip')
    if not os.path.isfile(zpath):
        _download(LLAMA_ZIP_URL, zpath, progress, 'llama-server')
    extract_server_zip(zpath)
    return server_available()

def extract_server_zip(zpath):
    """Ne garde que llama-server.exe et ses DLL (le zip contient ~40 outils)."""
    with zipfile.ZipFile(zpath) as z:
        for info in z.infolist():
            name = os.path.basename(info.filename)
            if not name or info.is_dir():
                continue
            if name == 'llama-server.exe' or name.lower().endswith('.dll'):
                with z.open(info) as src, open(os.path.join(LLAMA_DIR, name), 'wb') as dst:
                    shutil.copyfileobj(src, dst)

def install_models(files=None, progress=None):
    """Télécharge les modèles du catalogue (par défaut : ceux marqués recommandés)."""
    wanted = files if files is not None else [c[0] for c in CATALOG if c[5]]
    for fname, url, mb, label, desc, rec in CATALOG:
        if fname not in wanted:
            continue
        dest = os.path.join(MODELS_DIR, fname)
        if os.path.isfile(dest):
            continue
        _download(url, dest, progress, label)

def install_all(files=None, progress=None):
    install_server(progress)
    install_models(files, progress)
    return has_local()

# ─── Ligne de commande : python local_llm.py [--all] [--only fichier.gguf] ────
if __name__ == '__main__':
    args = sys.argv[1:]
    files = [c[0] for c in CATALOG if c[5]]
    if '--all' in args:
        files = [c[0] for c in CATALOG]
    if '--only' in args:
        files = [args[args.index('--only') + 1]]

    _last = {'label': None}
    def _cli_progress(label, done, total):
        if label != _last['label']:
            _last['label'] = label
            print(f'\n{label} :')
        pct = f'{done * 100 // total:3d}%' if total else '   ?'
        print(f'\r  {pct}  {done/1e6:7.0f} / {total/1e6:.0f} Mo', end='', flush=True)

    print('Installation du moteur local Zora')
    print(f'  dossier binaire : {LLAMA_DIR}')
    print(f'  dossier modèles : {MODELS_DIR}')
    ok = install_all(files, _cli_progress)
    print('\n\nTerminé.' if ok else '\n\nÉchec — vérifiez la connexion internet.')
    print('Modèles disponibles :', ', '.join(list_models()) or 'aucun')
