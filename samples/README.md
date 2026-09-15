# Exemples

## `rush_demonstration.json`

Transcription d'un rush facecam simulé qui contient **tous** les cas traités par
AutoRush : hésitations, bafouillages, mot abandonné, répétition volontaire à
protéger, trois reprises de phrase avec corrections orales, un fragment
orphelin, et une phrase en anglais au milieu du français.

Elle sert aux tests et permet d'essayer le logiciel sans vidéo :

```powershell
py -m autorush demo --out demo
```

Pour la brancher sur votre propre vidéo (et sauter la transcription) :

```powershell
py -m autorush rush.mp4 --transcript samples\rush_demonstration.json
```

Le format est celui de Whisper / faster-whisper : une liste de segments, chacun
portant ses mots horodatés. Les exports de WhisperX et les fichiers `.srt` sont
également acceptés — mais un `.srt` n'a pas d'horodatage par mot, la précision
des coupes en souffre.
