"""FKey — kit d'interface (thème clair 2026, widgets dessinés sur Canvas).

Tout est du tkinter pur : aucune dépendance supplémentaire, compatible PyInstaller.
"""
import tkinter as tk
from tkinter import font as tkfont

# ─── Palette (claire, épurée) ─────────────────────────────────────────────────
C = dict(
    bg='#F4F5F9',        # fond de fenêtre
    surface='#FFFFFF',   # cartes
    sidebar='#EEF0F6',
    border='#E3E5EC',
    border_strong='#CBD0DC',
    text='#14161C',
    muted='#6A7080',
    faint='#9AA0AE',
    accent='#2F63F0',        # bleu du logo FKey
    accent_hover='#2452D6',
    accent_soft='#EAF0FF',
    accent_text='#2452D6',
    success='#1E9E5A',
    success_soft='#E4F5EB',
    warn='#B8791E',
    warn_soft='#FFF3DC',
    danger='#D63C5E',
    danger_soft='#FDE8EC',
    key_bg='#FFFFFF',
    track='#D9DCE5',
)

_FONTS = {}
def F(kind='body'):
    """Polices : Segoe UI (Windows 11), avec repli automatique."""
    if kind not in _FONTS:
        fam_semi = 'Segoe UI Semibold' if 'Segoe UI Semibold' in tkfont.families() else 'Segoe UI'
        table = {
            'display': ('Segoe UI', 22, 'bold'),
            'title':   (fam_semi, 16, 'normal'),
            'h2':      (fam_semi, 12, 'normal'),
            'h3':      (fam_semi, 10, 'normal'),
            'body':    ('Segoe UI', 10),
            'small':   ('Segoe UI', 9),
            'tiny':    ('Segoe UI', 8),
            'mono':    ('Consolas', 9),
            'key':     (fam_semi, 9, 'normal'),
        }
        _FONTS[kind] = tkfont.Font(font=table[kind])
    return _FONTS[kind]

# ─── Primitives Canvas ────────────────────────────────────────────────────────
def round_rect(cv, x1, y1, x2, y2, r=10, **kw):
    """Rectangle arrondi (polygone lissé) — la technique classique tkinter."""
    r = max(1, min(r, (x2 - x1) // 2, (y2 - y1) // 2))
    pts = [x1+r, y1, x2-r, y1, x2, y1, x2, y1+r, x2, y2-r, x2, y2, x2-r, y2,
           x1+r, y2, x1, y2, x1, y2-r, x1, y1+r, x1, y1]
    return cv.create_polygon(pts, smooth=True, splinesteps=24, **kw)

# ─── Widgets ──────────────────────────────────────────────────────────────────
class Button(tk.Canvas):
    """Bouton arrondi. style: 'primary' | 'secondary' | 'ghost' | 'danger'."""
    STYLES = {
        'primary':   dict(bg=C['accent'],  hover=C['accent_hover'], fg='#FFFFFF', border=C['accent']),
        'secondary': dict(bg=C['surface'], hover='#F3F4F9',         fg=C['text'],  border=C['border_strong']),
        'ghost':     dict(bg=C['bg'],      hover='#E8EAF1',         fg=C['accent_text'], border=C['bg']),
        'danger':    dict(bg=C['surface'], hover=C['danger_soft'],  fg=C['danger'], border=C['border_strong']),
        'soft':      dict(bg=C['accent_soft'], hover='#E2E4FA',     fg=C['accent_text'], border=C['accent_soft']),
    }
    def __init__(self, parent, text, command=None, style='primary', size='md', width=None, **kw):
        h = {'sm': 30, 'md': 38, 'lg': 46}[size]
        fnt = F('h3') if size != 'lg' else F('h2')
        w = width or (fnt.measure(text) + {'sm': 28, 'md': 40, 'lg': 52}[size])
        super().__init__(parent, width=w, height=h, bg=parent.cget('bg'),
                         highlightthickness=0, bd=0, cursor='hand2', **kw)
        self.st = self.STYLES[style]; self.command = command; self.text = text
        self.enabled = True; self.w, self.h, self.fnt = w, h, fnt
        self._draw(self.st['bg'])
        self.bind('<Enter>', lambda e: self.enabled and self._draw(self.st['hover']))
        self.bind('<Leave>', lambda e: self.enabled and self._draw(self.st['bg']))
        self.bind('<Button-1>', self._click)

    def _draw(self, fill):
        self.delete('all')
        round_rect(self, 1, 1, self.w-1, self.h-1, r=9, fill=fill, outline=self.st['border'])
        fg = self.st['fg'] if self.enabled else C['faint']
        self.create_text(self.w//2, self.h//2, text=self.text, fill=fg, font=self.fnt)

    def _click(self, e=None):
        if self.enabled and self.command: self.command()

    def set_text(self, text):
        self.text = text
        need = self.fnt.measure(text) + 40
        if need > self.w:
            self.w = need; self.config(width=need)
        self._draw(self.st['bg'])

    def set_enabled(self, on):
        self.enabled = on
        self.config(cursor='hand2' if on else 'arrow')
        self._draw(self.st['bg'] if on else '#F0F1F5')

    # Etat "occupé" : bouton désactivé + points animés, pour que l'utilisateur
    # voie qu'une action de quelques secondes est en cours (et ne reclique pas).
    _busy_job = None
    def set_busy(self, text=None):
        if self._busy_job:
            self.after_cancel(self._busy_job); self._busy_job = None
        if text is None:
            self.set_enabled(True); return
        self._busy_base = text; self._busy_tick = 0
        self.enabled = False; self.config(cursor='watch')
        need = self.fnt.measure(text + '…') + 40
        if need > self.w:
            self.w = need; self.config(width=need)
        self._busy_step()

    def _busy_step(self):
        dots = ['   ', '.  ', '.. ', '...'][self._busy_tick % 4]
        self._busy_tick += 1
        self.delete('all')
        round_rect(self, 1, 1, self.w-1, self.h-1, r=9, fill='#F0F1F5', outline=self.st['border'])
        self.create_text(self.w//2, self.h//2, text=self._busy_base + dots, fill=C['muted'], font=self.fnt)
        self._busy_job = self.after(350, self._busy_step)


class Card(tk.Frame):
    """Carte blanche à bordure fine."""
    def __init__(self, parent, padx=20, pady=16, **kw):
        super().__init__(parent, bg=C['surface'], highlightthickness=1,
                         highlightbackground=C['border'], highlightcolor=C['border'],
                         padx=padx, pady=pady, **kw)


def Label(parent, text, kind='body', color='text', **kw):
    # Un texte multi-ligne/wrapé doit rester calé à gauche (tk centre par défaut).
    if ('wraplength' in kw or kw.get('justify') == 'left') and 'anchor' not in kw:
        kw['anchor'] = 'w'
    return tk.Label(parent, text=text, bg=parent.cget('bg'), fg=C[color], font=F(kind), **kw)


class Badge(tk.Label):
    """Pastille (statut, 'Recommandé', taille…). tone: accent|success|warn|neutral|danger"""
    TONES = {
        'accent':  (C['accent_soft'],  C['accent_text']),
        'success': (C['success_soft'], C['success']),
        'warn':    (C['warn_soft'],    C['warn']),
        'danger':  (C['danger_soft'],  C['danger']),
        'neutral': ('#EDEEF3',         C['muted']),
    }
    def __init__(self, parent, text, tone='neutral', **kw):
        bg, fg = self.TONES[tone]
        super().__init__(parent, text=text, bg=bg, fg=fg, font=F('tiny'), padx=8, pady=2, **kw)
    def set(self, text, tone=None):
        self.config(text=text)
        if tone:
            bg, fg = self.TONES[tone]; self.config(bg=bg, fg=fg)


class Toggle(tk.Canvas):
    """Interrupteur on/off."""
    def __init__(self, parent, variable, command=None, **kw):
        super().__init__(parent, width=44, height=24, bg=parent.cget('bg'),
                         highlightthickness=0, bd=0, cursor='hand2', **kw)
        self.var = variable; self.command = command
        self.bind('<Button-1>', self._flip)
        self.var.trace_add('write', lambda *a: self._draw())
        self._draw()
    def _draw(self):
        self.delete('all')
        on = bool(self.var.get())
        round_rect(self, 1, 1, 43, 23, r=11, fill=C['accent'] if on else C['track'], outline='')
        x = 32 if on else 12
        self.create_oval(x-8, 4, x+8, 20, fill='#FFFFFF', outline='')
    def _flip(self, e=None):
        self.var.set(not self.var.get())
        if self.command: self.command()


class ProgressBar(tk.Canvas):
    def __init__(self, parent, width=400, height=8, **kw):
        super().__init__(parent, width=width, height=height, bg=parent.cget('bg'),
                         highlightthickness=0, bd=0, **kw)
        self.w, self.h = width, height
        self.set(0)
    def set(self, ratio):
        self.delete('all')
        round_rect(self, 0, 0, self.w, self.h, r=4, fill=C['track'], outline='')
        if ratio > 0:
            round_rect(self, 0, 0, max(8, int(self.w * min(1, ratio))), self.h, r=4, fill=C['accent'], outline='')


class KeyCap(tk.Label):
    """Touche clavier stylisée (ex. F2)."""
    def __init__(self, parent, text, **kw):
        super().__init__(parent, text=text, bg=C['key_bg'], fg=C['text'], font=F('key'),
                         padx=10, pady=4, highlightthickness=1,
                         highlightbackground=C['border_strong'], **kw)


class Entry(tk.Frame):
    """Champ de saisie avec bordure fine et focus accent."""
    def __init__(self, parent, textvariable=None, width=30, placeholder='', **kw):
        super().__init__(parent, bg=C['border_strong'], padx=1, pady=1)
        self.e = tk.Entry(self, textvariable=textvariable, width=width, bg=C['surface'], fg=C['text'],
                          relief='flat', bd=6, font=F('body'), insertbackground=C['accent'],
                          highlightthickness=0, **kw)
        self.e.pack(fill='x')
        self.e.bind('<FocusIn>',  lambda e: self.config(bg=C['accent']))
        self.e.bind('<FocusOut>', lambda e: self.config(bg=C['border_strong']))
    def get(self): return self.e.get()


class OptionCard(tk.Frame):
    """Carte sélectionnable (choix de modèle). Bordure accent quand sélectionnée."""
    def __init__(self, parent, variable, value, title, subtitle, badges=(), meta='', **kw):
        super().__init__(parent, bg=C['surface'], highlightthickness=2,
                         highlightbackground=C['border'], padx=16, pady=12, cursor='hand2', **kw)
        self.var, self.value = variable, value
        top = tk.Frame(self, bg=C['surface']); top.pack(fill='x')
        self.dot = tk.Canvas(top, width=18, height=18, bg=C['surface'], highlightthickness=0)
        self.dot.pack(side='left', padx=(0, 10))
        Label(top, title, 'h2').pack(side='left')
        for txt, tone in badges:
            Badge(top, txt, tone).pack(side='left', padx=(8, 0))
        if meta:
            Label(top, meta, 'small', 'faint').pack(side='right')
        Label(self, subtitle, 'small', 'muted', wraplength=520, justify='left').pack(anchor='w', padx=(28, 0), pady=(4, 0))
        for w in self.walk(self):
            w.bind('<Button-1>', lambda e: self.var.set(self.value))
        self.var.trace_add('write', lambda *a: self._refresh())
        self._refresh()

    @staticmethod
    def walk(w):
        yield w
        for c in w.winfo_children():
            yield from OptionCard.walk(c)

    def _refresh(self):
        sel = self.var.get() == self.value
        self.config(highlightbackground=C['accent'] if sel else C['border'],
                    highlightcolor=C['accent'] if sel else C['border'])
        self.dot.delete('all')
        self.dot.create_oval(1, 1, 17, 17, outline=C['accent'] if sel else C['border_strong'], width=2,
                             fill=C['surface'])
        if sel:
            self.dot.create_oval(5, 5, 13, 13, fill=C['accent'], outline='')


class NavItem(tk.Frame):
    """Entrée de la barre latérale."""
    def __init__(self, parent, icon, text, command, **kw):
        super().__init__(parent, bg=C['sidebar'], padx=10, pady=4, cursor='hand2', **kw)
        self.inner = tk.Frame(self, bg=C['sidebar'], padx=12, pady=8)
        self.inner.pack(fill='x')
        self.ico = tk.Label(self.inner, text=icon, bg=C['sidebar'], fg=C['muted'], font=F('body'), width=2)
        self.ico.pack(side='left')
        self.lbl = tk.Label(self.inner, text=text, bg=C['sidebar'], fg=C['text'], font=F('body'))
        self.lbl.pack(side='left', padx=(6, 0))
        self.command = command; self.active = False
        for w in (self, self.inner, self.ico, self.lbl):
            w.bind('<Button-1>', lambda e: command())
            w.bind('<Enter>', lambda e: not self.active and self._paint('#E4E7F0'))
            w.bind('<Leave>', lambda e: not self.active and self._paint(C['sidebar']))
    def _paint(self, bg, fg=None):
        for w in (self.inner, self.ico, self.lbl): w.config(bg=bg)
        if fg: self.lbl.config(fg=fg); self.ico.config(fg=fg)
    def set_active(self, on):
        self.active = on
        self._paint(C['accent_soft'] if on else C['sidebar'], C['accent_text'] if on else C['text'])
        if not on: self.ico.config(fg=C['muted'])


class ScrollFrame(tk.Frame):
    """Conteneur vertical scrollable (molette). Le contenu va dans .inner."""
    def __init__(self, parent, **kw):
        super().__init__(parent, bg=parent.cget('bg'), **kw)
        self.cv = tk.Canvas(self, bg=parent.cget('bg'), highlightthickness=0, bd=0)
        self.sb = tk.Scrollbar(self, orient='vertical', command=self.cv.yview)
        self.cv.configure(yscrollcommand=self._on_scroll)
        self.cv.pack(side='left', fill='both', expand=True)
        self.inner = tk.Frame(self.cv, bg=parent.cget('bg'))
        self._pending = False
        self._win = self.cv.create_window((0, 0), window=self.inner, anchor='nw')
        self.inner.bind('<Configure>', self._fit)
        self.cv.bind('<Configure>', lambda e: self.cv.itemconfig(self._win, width=e.width))
        for w in (self.cv, self.inner):
            w.bind('<Enter>', lambda e: self.cv.bind_all('<MouseWheel>', self._wheel))
            w.bind('<Leave>', lambda e: self.cv.unbind_all('<MouseWheel>'))

    def _fit(self, e=None):
        self.cv.configure(scrollregion=self.cv.bbox('all'))
        if self._pending: return
        self._pending = True
        self.after_idle(self._toggle_bar)

    def _toggle_bar(self):
        """Barre visible seulement si nécessaire — avec hystérésis pour éviter
        l'oscillation (afficher la barre rétrécit le canvas, ce qui re-déclenche Configure)."""
        self._pending = False
        h_in, h_cv = self.inner.winfo_reqheight(), self.cv.winfo_height()
        shown = self.sb.winfo_ismapped()
        if h_in > h_cv and not shown:
            self.sb.pack(side='right', fill='y')
        elif h_in < h_cv - 40 and shown:
            self.sb.pack_forget()

    def _on_scroll(self, *a):
        self.sb.set(*a)

    def _wheel(self, e):
        if self.inner.winfo_reqheight() > self.cv.winfo_height():
            self.cv.yview_scroll(int(-1 * (e.delta / 120)), 'units')


def center(win, w, h):
    win.update_idletasks()
    x = (win.winfo_screenwidth() - w) // 2
    y = (win.winfo_screenheight() - h) // 2 - 20
    win.geometry(f'{w}x{h}+{x}+{y}')


def sep(parent, pady=12):
    tk.Frame(parent, bg=C['border'], height=1).pack(fill='x', pady=pady)


def enable_dpi():
    """Rendu net sur écrans HiDPI (Windows)."""
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
