"""Test de bout en bout : une vraie video entre, une vraie timeline sort.

Ces tests fabriquent un petit rush (mire + voix de synthese), le passent dans
le pipeline complet et verifient le resultat avec ffprobe. Ils sont ignores si
ffmpeg n'est pas installe.
"""

from __future__ import annotations

import json
import subprocess
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from autorush.config import Settings
from autorush.media.ffmpeg import ffmpeg_available, find_ffmpeg, find_ffprobe, probe_media
from autorush.pipeline import PipelineOptions, run_pipeline
from autorush.transcription.io import save_transcript
from autorush.transcription.synthetic import ScriptLine, build_transcript

pytestmark = pytest.mark.skipif(
    not ffmpeg_available(), reason="ffmpeg n'est pas installe"
)

SCRIPT = [
    ScriptLine("Salut a tous et bienvenue dans cette video.", gap_before=0.6),
    ScriptLine("Euh aujourd'hui on parle du tournoi.", gap_before=0.5),
    ScriptLine("Je je pense que c'etait la meilleure edition.", gap_before=0.5),
    ScriptLine("Les joueurs japonais etaient plutot...", gap_before=0.9),
    ScriptLine("Non, je recommence.", gap_before=1.4),
    ScriptLine("Les joueurs japonais etaient vraiment excellents cette annee.", gap_before=1.1),
    ScriptLine("Le niveau global a explose cette saison.", gap_before=2.4),
    ScriptLine("Merci d'avoir regarde jusqu'au bout.", gap_before=0.8),
]


@pytest.fixture(scope="module")
def rush(tmp_path_factory):
    """Fabrique un rush facecam synthetique et sa transcription."""
    directory = tmp_path_factory.mktemp("rush")
    transcript = build_transcript(SCRIPT, start_at=0.8)
    transcript_path = save_transcript(transcript, directory / "transcription.json")

    sample_rate = 48000
    duration = transcript.duration + 0.5
    count = int(duration * sample_rate)
    rng = np.random.default_rng(5)
    audio = rng.normal(0, 0.0005, count).astype(np.float32)
    times = np.arange(count) / sample_rate
    for word in transcript.words:
        start, end = int(word.start * sample_rate), int(word.end * sample_rate)
        if end <= start:
            continue
        local = times[start:end] - word.start
        fundamental = 115 + 35 * np.sin(word.index * 0.6)
        signal = 0.30 * np.sin(2 * np.pi * fundamental * local) + 0.15 * np.sin(
            2 * np.pi * 2 * fundamental * local
        )
        envelope = np.clip(
            np.minimum(local, (word.end - word.start) - local) / 0.02, 0, 1
        )
        audio[start:end] += (signal * envelope).astype(np.float32)

    import soundfile as sf

    voice = directory / "voix.wav"
    sf.write(str(voice), audio, sample_rate, subtype="PCM_16")

    video = directory / "rush.mp4"
    subprocess.run(
        [
            find_ffmpeg(), "-loglevel", "error", "-y",
            "-f", "lavfi", "-i",
            f"testsrc2=size=640x360:rate=25:duration={duration:.2f}",
            "-i", str(voice),
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "34",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-shortest",
            str(video),
        ],
        check=True,
    )
    return video, transcript_path, directory


def run(rush, tmp_path, **kwargs):
    video, transcript_path, _ = rush
    settings = Settings.for_style(kwargs.pop("style", "dynamique"))
    settings.export.preview_height = 180
    settings.export.preview_preset = "ultrafast"
    settings.export.preview_crf = 34
    options = PipelineOptions(
        input_path=video,
        output_dir=tmp_path,
        settings=settings,
        transcript_path=transcript_path,
        **kwargs,
    )
    return run_pipeline(options)


# --------------------------------------------------------------------------- #
def test_le_pipeline_produit_tous_les_fichiers(rush, tmp_path):
    result = run(rush, tmp_path)
    for clef in ("premiere_xml", "edl", "json", "report_html", "report_md", "transcript", "srt"):
        assert clef in result.outputs, clef
        assert result.outputs[clef].exists()
        assert result.outputs[clef].stat().st_size > 0


def test_le_montage_raccourcit_le_rush(rush, tmp_path):
    result = run(rush, tmp_path)
    stats = result.analysis.stats
    assert stats["final_duration"] < stats["source_duration"]
    assert 0.3 < stats["compression_ratio"] < 0.95


def test_les_mauvaises_prises_disparaissent(rush, tmp_path):
    result = run(rush, tmp_path)
    removed = result.analysis.removed_word_indices
    texte = " ".join(
        w.clean for w in result.transcript.words if w.index not in removed
    ).lower()
    assert "je recommence" not in texte
    assert "plutot" not in texte
    assert "vraiment excellents cette annee" in texte
    assert "niveau global a explose" in texte
    assert "merci d'avoir regarde" in texte


def test_le_xml_est_importable(rush, tmp_path):
    result = run(rush, tmp_path)
    root = ET.parse(result.outputs["premiere_xml"]).getroot()
    assert root.tag == "xmeml"
    clips = root.findall("sequence/media/video/track/clipitem")
    assert len(clips) == len(result.analysis.timeline.shots)
    url = clips[0].findtext("file/pathurl")
    assert url.startswith("file://localhost/")
    assert url.endswith("rush.mp4")


def test_la_variante_de_keyframes_est_ecrite(rush, tmp_path):
    result = run(rush, tmp_path)
    assert "premiere_xml_alt" in result.outputs
    root = ET.parse(result.outputs["premiere_xml_alt"]).getroot()
    assert root.tag == "xmeml"


def test_la_preview_est_synchrone(rush, tmp_path):
    """L'image et le son de la preview doivent avoir la meme duree."""
    result = run(rush, tmp_path, make_preview=True)
    assert "preview" in result.outputs, result.warnings
    preview = result.outputs["preview"]

    def duree(flux):
        sortie = subprocess.run(
            [
                find_ffprobe(), "-v", "error", "-select_streams", flux,
                "-show_entries", "stream=duration", "-of", "csv=p=0", str(preview),
            ],
            capture_output=True, text=True, check=True,
        )
        return float(sortie.stdout.strip().splitlines()[0])

    video_duree, audio_duree = duree("v:0"), duree("a:0")
    assert abs(video_duree - audio_duree) < 0.06, (video_duree, audio_duree)
    attendu = result.analysis.timeline.duration
    assert abs(video_duree - attendu) < 0.5, (video_duree, attendu)


def test_la_preview_a_le_bon_nombre_dimages(rush, tmp_path):
    result = run(rush, tmp_path, make_preview=True)
    preview = result.outputs["preview"]
    sortie = subprocess.run(
        [
            find_ffprobe(), "-v", "error", "-select_streams", "v:0",
            "-count_frames", "-show_entries", "stream=nb_read_frames",
            "-of", "csv=p=0", str(preview),
        ],
        capture_output=True, text=True, check=True,
    )
    images = int(sortie.stdout.strip())
    media = probe_media(result.media.path)
    from autorush.editing.frames import frame_clips, total_frames

    attendu = total_frames(
        frame_clips(result.analysis.timeline, media.fps, int(media.duration * media.fps))
    )
    assert images == attendu, (images, attendu)


def test_le_json_permet_de_tout_relire(rush, tmp_path):
    result = run(rush, tmp_path)
    data = json.loads(result.outputs["json"].read_text(encoding="utf-8"))
    assert data["media"]["fps"] == pytest.approx(25.0)
    assert data["analysis"]["stats"]["shots"] == len(result.analysis.timeline.shots)
    assert len(data["analysis"]["timeline"]["shots"]) == len(result.analysis.timeline.shots)


def test_les_styles_donnent_des_montages_differents(rush, tmp_path):
    naturel = run(rush, tmp_path / "n", style="naturel")
    tres = run(rush, tmp_path / "t", style="tres_dynamique")
    assert (
        tres.analysis.stats["final_duration"] < naturel.analysis.stats["final_duration"]
    )
    assert len(tres.zooms.events) >= len(naturel.zooms.events)


def test_la_progression_couvre_toutes_les_etapes(rush, tmp_path):
    vus = []
    video, transcript_path, _ = rush
    options = PipelineOptions(
        input_path=video,
        output_dir=tmp_path / "prog",
        settings=Settings.for_style("dynamique"),
        transcript_path=transcript_path,
    )
    run_pipeline(options, on_progress=lambda p: vus.append((p.stage, p.fraction)))
    etapes = [stage for stage, _ in vus]
    for attendu in ("import", "transcription", "analyse", "reprises", "montage", "zooms", "export"):
        assert attendu in etapes, attendu
    fractions = [f for _, f in vus]
    assert fractions == sorted(fractions), "la progression doit etre monotone"
    assert fractions[-1] == pytest.approx(1.0)


def test_annulation(rush, tmp_path):
    from autorush.errors import CancelledError

    video, transcript_path, _ = rush
    options = PipelineOptions(
        input_path=video,
        output_dir=tmp_path / "annule",
        settings=Settings.for_style("dynamique"),
        transcript_path=transcript_path,
        cancel_check=lambda: True,
    )
    with pytest.raises(CancelledError):
        run_pipeline(options)


def test_un_fichier_sans_audio_est_refuse(tmp_path):
    from autorush.errors import MediaError

    muet = tmp_path / "muet.mp4"
    subprocess.run(
        [
            find_ffmpeg(), "-loglevel", "error", "-y", "-f", "lavfi",
            "-i", "testsrc2=size=320x180:rate=25:duration=2",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(muet),
        ],
        check=True,
    )
    with pytest.raises(MediaError) as info:
        probe_media(muet)
    assert "audio" in str(info.value).lower()


def test_lanalyse_audio_detecte_les_respirations(rush, tmp_path):
    result = run(rush, tmp_path, audio_analysis=True)
    types = {gap.kind for gap in result.analysis.gaps}
    assert "respiration" in types or "pause_phrase" in types
    with_energy = [g for g in result.analysis.gaps if g.energy >= 0]
    assert with_energy, "l'analyse audio doit renseigner l'energie des blancs"
