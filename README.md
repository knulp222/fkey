# FKey — correcteur de texte local pour Windows

Sélectionnez un texte dans n'importe quelle application, appuyez sur **F2** : il est corrigé sur place.
Un modèle de langue (Gemma 4 / Luth-2) tourne sur votre ordinateur via [llama.cpp](https://github.com/ggml-org/llama.cpp) — rien n'est envoyé sur internet.

**Site & téléchargement :** voir la page GitHub Pages du dépôt (dossier `docs/`) ou l'onglet *Releases*.

## Utiliser

Téléchargez `FKey-portable.zip` dans les Releases, décompressez, lancez `FKey.exe`. Aucune installation, aucun droit administrateur. L'assistant de premier lancement télécharge le modèle (≈ 3 Go, une seule fois).

| Touche | Action |
|---|---|
| F2 | Corriger (orthographe, grammaire) |
| F3 | Améliorer (style, fluidité) |
| F4 | Traduire (langue réglable) |
| F6 | Instruction libre |

## Compiler soi-même

```bat
python -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\python local_llm.py      :: télécharge llama-server.exe + le modèle recommandé
build.bat                             :: produit dist\FKey\FKey.exe et dist\FKey-portable.zip
```

Pour lancer sans compiler : `lancer.bat` (ou `python fkey.py`).

## Structure

- `fkey.py` — application (tray, raccourcis, fenêtres)
- `ui.py` — kit d'interface tkinter (thème clair, widgets Canvas)
- `local_llm.py` — moteur : lance `llama-server.exe`, prompt few-shot, catalogue et téléchargement des modèles
- `docs/` — site web (GitHub Pages)

## Licences

Code : MIT. Moteur llama.cpp : MIT. Modèles : Gemma 4 (licence Gemma, Google), Luth-2 (Apache 2.0, Kurakura AI).
