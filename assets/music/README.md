# Biblioteca de música de fondo

Pon aquí pistas **libres de derechos** en formato `.mp3`, `.m4a` o `.wav`. Los archivos de audio no se suben a git
(solo este README y los `.gitkeep`).

## Organización

- `assets/music/curiosity/`: pistas para el perfil `curiosity`.
- `assets/music/micronovela/`: pistas para el perfil `micronovela`.
- `assets/music/` (esta carpeta): pool compartido; se usa cuando la subcarpeta del perfil está vacía.

Si no pasas `--music`, el pipeline elige una pista del perfil (o del pool) de forma determinista a partir del slug del
proyecto y guarda la elección en `workspace/<slug>/run_config.json`, así que repetir la ejecución o usar `retry-scene`
mantiene la misma pista. El volumen se ajusta solo (la música queda 18 dB por debajo de la voz, ver `BGM_RELATIVE_DB`
en `config.py`), da igual lo fuerte que esté masterizada la pista.

## Dónde conseguir música

- YouTube Audio Library (YouTube Studio > Biblioteca de audio).
- Pixabay Music (pixabay.com/music).

## Licencia (importante)

Antes de usar una pista **verifica que su licencia permite uso comercial / monetizado** en TikTok, Instagram y YouTube,
y si exige atribución. Las licencias cambian por pista y con el tiempo: guarda la fuente y la licencia de cada archivo.
