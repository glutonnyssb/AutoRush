# Construire AutoRush pour Windows

## Lancer sans construire

Le plus simple pendant le développement :

```powershell
py -m pip install -r requirements.txt
py -m autorush gui
```

Ou par double-clic sur `build\windows\AutoRush.bat`.

## Construire un exécutable

```powershell
build\windows\build.bat
```

Le script crée un environnement virtuel, installe les dépendances, lance les
tests, puis construit avec PyInstaller. Résultat :

```
build\windows\dist\AutoRush\
    AutoRush.exe          <- interface graphique
    autorush-cli.exe      <- ligne de commande
    docs\
    README.md
    ... (bibliothèques)
```

## ffmpeg

ffmpeg n'est pas embarqué : la licence et le poids ne le justifient pas, et il
est déjà présent sur beaucoup de machines de montage.

Au lancement, AutoRush le cherche dans cet ordre :

1. la variable d'environnement `AUTORUSH_FFMPEG` ;
2. un dossier `ffmpeg\bin` **à côté de l'exécutable** ;
3. le `PATH` de Windows ;
4. les emplacements d'installation habituels, y compris WinGet.

Pour une distribution autonome, copiez `ffmpeg.exe` et `ffprobe.exe` dans :

```
build\windows\dist\AutoRush\ffmpeg\bin\
```

## Le moteur de transcription

Le modèle Whisper n'est pas embarqué non plus : `large-v3` pèse environ 3 Go. Il
est téléchargé automatiquement au premier lancement et mis en cache par
`faster-whisper`, dans `%LOCALAPPDATA%\huggingface`.

Pour une machine sans connexion, lancez une première transcription sur un poste
connecté puis recopiez ce dossier.

## Icône

Placez un fichier `autorush.ico` dans `build\windows\` : la recette PyInstaller
le détecte automatiquement.
