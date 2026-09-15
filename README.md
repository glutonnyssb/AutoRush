# AutoRush

**Montage automatique de rushes facecam, prêt pour Adobe Premiere Pro.**

AutoRush prend une vidéo brute où vous parlez face caméra et en sort un premier
montage propre : silences resserrés, hésitations nettoyées, mauvaises prises
retirées, zooms placés. Vous récupérez une séquence Premiere directement
exploitable, une preview MP4 et un rapport qui explique chaque décision.

La règle qui gouverne tout le logiciel : **ne jamais supprimer une bonne phrase
par erreur.** En cas de doute, AutoRush garde le passage et le signale.

---

## Ce que fait AutoRush

| | |
| --- | --- |
| **Silences** | Raccourcit les blancs inutiles, distingue respiration, pause naturelle, hésitation, pause trop longue et silence de mauvaise prise. |
| **Hésitations** | Retire les « euh », les bafouillages (« Je je pense ») et les débuts de mots abandonnés (« Mal- Malgré »). Protège les répétitions voulues (« très très fort »). |
| **Reprises de phrases** | Détecte les tentatives successives d'une même phrase et ne garde que la meilleure version. |
| **Corrections orales** | Reconnaît « je recommence », « attends », « c'est pas ça »… puis supprime la mauvaise prise **et** la phrase de correction. |
| **Fragments** | Élimine les bouts de phrase orphelins laissés entre deux prises. |
| **Raccords** | Relit le montage et signale les raccords douteux. |
| **Zooms** | Place trois types de zooms : direct, progressif et lancé (effet d'inertie). |
| **Exports** | Séquence Premiere Pro (XML), EDL, JSON, rapport HTML et Markdown, preview MP4. |

---

## Installation sur Windows

### 1. Python

Installez [Python 3.11 ou plus récent](https://www.python.org/downloads/windows/)
en cochant **« Add python.exe to PATH »**.

### 2. AutoRush

```powershell
git clone https://github.com/glutonnyssb/AutoRush.git
cd AutoRush
py -m pip install -r requirements.txt
```

Pour une installation en tant que commande :

```powershell
py -m pip install -e .
```

### 3. Installation de ffmpeg

AutoRush a besoin de `ffmpeg` et `ffprobe` pour lire la vidéo et rendre la preview.

```powershell
winget install Gyan.FFmpeg
```

Trois autres possibilités, si `winget` n'est pas disponible :

- télécharger une archive sur <https://www.gyan.dev/ffmpeg/builds/> et placer le
  dossier `ffmpeg` (celui qui contient `bin\ffmpeg.exe`) **à côté d'AutoRush** ;
- ajouter `ffmpeg\bin` au `PATH` de Windows ;
- définir la variable d'environnement `AUTORUSH_FFMPEG` vers `ffmpeg.exe`.

### 4. Vérifier que tout est prêt

```powershell
py -m autorush doctor
```

La commande liste ffmpeg, le moteur de transcription, la carte graphique
détectée et le dossier de cache.

---

## Utilisation

### Interface graphique

```powershell
py -m autorush gui
```

Choisissez une vidéo (ou glissez-la dans la fenêtre), un style, l'intensité des
zooms, cochez la preview si vous la voulez, et lancez. L'avancement s'affiche
étape par étape : Transcription, Analyse, Reprises, Montage, Zooms, Export.

### Ligne de commande

```powershell
py -m autorush "C:\Rushes\mon rush.mp4"
py -m autorush rush.mp4 --style tres_dynamique --zoom-intensity 80 --preview
py -m autorush rush.mov --silences-only --out "D:\Montages"
```

Options principales :

| Option | Effet |
| --- | --- |
| `-s, --style` | `naturel`, `dynamique` (défaut) ou `tres_dynamique` |
| `-z, --zoom-intensity` | intensité des zooms, de 0 à 100 |
| `--no-zoom` | aucun zoom |
| `-p, --preview` | génère la preview MP4 |
| `-o, --out` | dossier de sortie |
| `-l, --lang` | `fr`, `en`, `auto` (défaut) ou `multi` pour une vidéo bilingue |
| `-m, --model` | modèle Whisper : `large-v3` (défaut), `medium`, `small`… |
| `--device` | `auto`, `cpu` ou `cuda` |
| `--silences-only` | ne touche qu'aux silences, ne supprime aucune parole |
| `--keep-all` | ne supprime rien : analyse et signale seulement |
| `--min-confidence` | seuil de confiance pour supprimer de la parole (0 à 1) |
| `--transcript` | réutilise une transcription existante (relance instantanée) |

`py -m autorush process --help` donne la liste complète.

### Essayer sans vidéo

```powershell
py -m autorush demo --out demo
```

Produit un montage de démonstration (XML Premiere, rapport, JSON) à partir d'un
rush simulé qui contient tous les cas traités par le logiciel.

---

## Résultat

Dans le dossier de sortie (`AutoRush_out` par défaut) :

```
mon_rush_premiere.xml                    <- à importer dans Premiere Pro
mon_rush_premiere_keyframes-clip.xml     <- variante (voir docs/PREMIERE.md)
mon_rush.edl                             <- points de coupe seuls
mon_rush_montage.json                    <- toutes les décisions, en machine
mon_rush_rapport.html                    <- rapport lisible
mon_rush_rapport.md
mon_rush_transcription.json
mon_rush_transcription.srt
mon_rush_preview.mp4                     <- si --preview
```

### Import dans Premiere Pro

`Fichier ▸ Importer…` puis choisissez `mon_rush_premiere.xml`. Premiere crée une
séquence avec tous les cuts, les fondus audio, et les zooms sous forme de
**keyframes visibles et modifiables** dans *Options d'effet ▸ Mouvement ▸
Échelle*.

Voir [docs/PREMIERE.md](docs/PREMIERE.md) pour le détail, y compris la marche à
suivre si les zooms apparaissent figés.

---

## Les trois styles

| Style | Rythme | Zooms |
| --- | --- | --- |
| **Naturel** | blancs conservés jusqu'à 0,42 s, respiration préservée | environ 3/min, surtout progressifs |
| **Dynamique** | blancs jusqu'à 0,30 s, montage serré | environ 5/min, les trois types |
| **Très dynamique** | blancs jusqu'à 0,19 s, tics de langage retirés aussi | environ 8/min, amplitude marquée |

L'intensité des zooms reste réglable indépendamment du style.

---

## Performances

La transcription est l'étape longue. Ordres de grandeur pour un rush de
20 minutes :

| Configuration | Modèle | Durée approximative |
| --- | --- | --- |
| GPU NVIDIA (8 Go) | `large-v3` | 2 à 4 min |
| Processeur récent (8 cœurs) | `medium` | 12 à 20 min |
| Processeur récent (8 cœurs) | `small` | 5 à 8 min |

Le reste du pipeline (analyse, montage, zooms, XML) prend quelques secondes. La
preview MP4 ajoute environ le temps d'un encodage classique.

Les transcriptions sont mises en cache : réessayer un autre style sur le même
rush est instantané. `py -m autorush cache --clear` vide le cache.

---

## Documentation

- [docs/PREMIERE.md](docs/PREMIERE.md) — import dans Premiere, keyframes, dépannage
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — comment le logiciel décide
- [docs/REGLAGES.md](docs/REGLAGES.md) — tous les seuils et leur effet

## Développement

```bash
python -m pip install -r requirements-dev.txt
python -m pytest            # 223 tests
python -m ruff check .
```

Les tests couvrent chaque exemple du cahier des charges, et mesurent les zooms
**sur les images rendues** pour vérifier qu'ils sont réellement visibles.

## Licence

MIT. Voir [LICENSE](LICENSE).
