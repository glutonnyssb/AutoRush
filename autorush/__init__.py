"""AutoRush - montage automatique de rushes facecam.

Pipeline :
    import -> transcription -> analyse -> reprises -> montage -> zooms -> export

Le principe directeur du logiciel est la prudence : en cas de doute sur un
segment de parole, AutoRush garde le segment et le signale dans le rapport
plutot que de le supprimer.
"""

from autorush.version import APP_NAME, __version__

__all__ = ["__version__", "APP_NAME"]
