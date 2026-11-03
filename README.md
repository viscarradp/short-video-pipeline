# ⚡ Short Video Pipeline CLI

Herramienta CLI modular y profesional en Python para automatizar el 100% de la creación de videos verticales (9:16, 1080x1920) para TikTok, Instagram Reels y YouTube Shorts, con edición dinámica, efectos de sonido (SFX whoosh), música de fondo (BGM) y subtítulos animados estilo viral quemados en el video.

---

## 🌟 Características Principales

1. **Proveedor de IA Unificado (Fal.ai):**
   - **Guiones:** `fal-ai/any-llm` configurado con `google/gemini-2.5-flash` para estructuración viral en JSON estricto validado con Pydantic.
   - **Imágenes:** `fal-ai/flux/schnell` a 768x1360 (~1 megapíxel, ~9:16; `FLUX_IMAGE_SIZE` en `config.py`), reescaladas a 1080x1920 cubriendo el cuadro y recortando al centro (nunca se estiran). Las imágenes pagadas con el tamaño anterior (`portrait_16_9`, 576x1024) se siguen aceptando en caché; solo `--force-images` / `retry-scene` las rehacen al tamaño nuevo.
2. **Caché por Huella de Contenido (por escena):**
   - Cada proyecto opera en `./workspace/<slug>/` y guarda `cache_manifest.json` con la huella (sha256) de cada artefacto.
   - Solo se rehace lo que realmente cambió: otra voz regenera el audio (y los clips cuya duración cambie), editar a mano la narración o el prompt de una escena en `script.json` rehace solo esa escena, y cambiar `--music` rehace solo el video final.
   - `--force` / `--force-script|audio|images|video` ignoran la caché de una etapa concreta.
   - El guion incluye `hook_text`, `post_description` y `hashtags`; la narración de cada escena tiene un presupuesto de palabras calculado del perfil (`target_scene_duration_sec` x 2.7 palabras/s x (1 + velocidad de la voz)) y la frase de cierre del perfil (`ending_call_to_action`) se garantiza al final del último texto.
   - Flags `--mock-script` y `--mock-images`: guion de demostración local e imágenes de prueba con Pillow para depurar todo el flujo sin gastar saldo ($0.00).
3. **Perfiles Desacoplados (`--profile`):**
   - **`curiosity`:** Ritmo rápido (15 a 18 escenas de ~3.5s), voz enérgica (`rate="+22%"`), estilo visual por defecto `explainer_3d` (render 3D con cortes transversales).
   - **`micronovela`:** Ritmo dramático (10 a 12 escenas de ~5s), voz dramática (`rate="+8%"`), consistencia física de personajes inyectada en cada prompt, estilo visual por defecto `telenovela_cine`, y cierre con cliffhanger ("Parte 2 en el perfil").
4. **Regeneración Quirúrgica (`retry-scene`):** repite la imagen (o corrige la narración) de una escena sin tocar el resto.
5. **Locución y Timings al Milisegundo:**
   - `edge-tts` asíncrono con captura en tiempo real de eventos `WordBoundary`.
6. **Subtítulos Virales Dinámicos:**
   - Archivo `.ass` a resolución 1080x1920, Fontsize 100 con contorno grueso, en la zona segura (centro al 64 % de la altura; evita el 20 % inferior y el 12 % superior).
   - Bloques de 1 a 3 palabras que nunca superan ~900 px de ancho (los anchos se parten en bloques menores).
   - Karaoke: la palabra que se está diciendo va en amarillo con un pequeño pop de escala (112 %); las palabras clave y los números van en cian (si una palabra clave es la activa gana el amarillo). Un evento por palabra, sin huecos ni solapes.
   - Texto gancho (`hook_text`): caja de color en la parte superior, en mayúsculas, durante la escena 1 (máx. 3 s).
7. **Ensamblado FFmpeg con Zero-Jitter:**
   - Movimiento de cámara Ken Burns suave (`zoom_in_fast`, `zoom_in_slow`, `zoom_out`, `pan_right`, `pan_left`) con supersampling.
   - Efecto de sonido `whoosh` en cada corte de escena a `volume=0.18`.
   - Música de fondo con *ducking*: baja automáticamente bajo la voz (`sidechaincompress`) y sube en las pausas, con fade-out al final. Su ganancia se calcula midiendo la sonoridad de la pista y de la voz para que, sin ducking, quede `BGM_RELATIVE_DB` (-18 dB) bajo la voz, sin importar cómo esté masterizada la canción (medición cacheada por hash del archivo).
   - Biblioteca de música en `assets/music/` (subcarpetas `curiosity/` y `micronovela/` + raíz compartida; ver el README de esa carpeta). Sin `--music`, se elige una pista de forma determinista según el slug y se guarda en `run_config.json`; si la biblioteca está vacía se usa el drone sintético con un aviso.
   - Los clips se renderizan en paralelo (`CLIP_RENDER_WORKERS`, por defecto `max(1, min(4, cpus // 2))`; se puede fijar con la variable de entorno del mismo nombre).
   - Mezcla de audio como etapa propia (`audio/mix_final.wav`) normalizada a **-14 LUFS** con `loudnorm` de dos pasadas (techo -1.5 dBTP).
   - Línea de tiempo exacta por fotogramas: cada escena dura un número entero de fotogramas y su audio un número entero de muestras (1 fotograma = 1600 muestras a 48 kHz), así cortes, whooshes y subtítulos no se desfasan.
   - Generación de `post_metadata.txt` con el título, la descripción y los hashtags del guion (con respaldo estático si faltan) más etiquetas de alcance (`REACH_HASHTAGS`: #shorts #fyp #parati).

---

## 🚀 Instalación y Requisitos

### Requisitos del Sistema
- Python 3.10+
- FFmpeg con soporte `libass` (build "full").

### Configuración en Windows (recomendado)
```powershell
cd short-video-pipeline
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# FFmpeg con libass (build de gyan.dev, incluye ffprobe)
winget install Gyan.FFmpeg
```
Abre una terminal nueva tras instalar FFmpeg para que `ffmpeg` quede en el `PATH`. Si usas otra ruta, define `FFMPEG_PATH` y `FFPROBE_PATH`.

Copia tu clave de Fal.ai en el archivo `.env`:
```env
FAL_KEY=tu_clave_aqui
```

### Notas para macOS
```bash
brew install ffmpeg-full          # keg-only; el pipeline lo detecta automáticamente
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

---

## 🎬 Uso

`main.py` tiene tres comandos: **`generate`** (video completo), **`retry-scene`** (regenerar escenas) y **`styles`** (listar estilos visuales). Ejecuta `python main.py --help` para verlos.

### 1. Prueba gratuita (Costo $0.00)
Guion de demostración + imágenes de prueba locales; la voz (`edge-tts`) es real y gratuita:
```powershell
python main.py generate --profile curiosity --topic "Qué pasa en tu motor si usas gasolina barata" --slug motor-gasolina --mock-script --mock-images
```

### 2. Producción real (Fal.ai: guion con any-llm + imágenes Flux/schnell)
```powershell
python main.py generate --profile curiosity --topic "Qué pasa en tu motor si usas gasolina barata" --slug motor-gasolina
```
Volver a ejecutar el mismo comando no regenera nada (todo sale "en caché").

### 3. Modo Micronovela (consistencia de personajes y drama)
```powershell
python main.py generate --profile micronovela --topic "Mi hermano hipotecó la casa de mi abuela en secreto" --slug casa-abuela --mock-images
```

### Opciones de `generate`
- `--profile`, `-p`: `curiosity` o `micronovela`.
- `--topic`, `-t`: Tema del video (requerido).
- `--slug`, `-s`: Nombre de la carpeta en `./workspace/`.
- `--mock-script`: Guion de demostración local en lugar de Fal.ai (no respeta el tema).
- `--mock-images`, `-m`: Imágenes de prueba con Pillow en lugar de Flux.
- `--voice`, `-v`: Voz TTS (ej. `es-MX-JorgeNeural`, `es-MX-DaliaNeural`).
- `--style`: Estilo visual (ver `python main.py styles` y la sección *Estilos visuales*). Por defecto, el del guion en caché o el del perfil.
- `--music`: Ruta a una pista de música de fondo (`.mp3`, `.m4a` o `.wav`). Sin esta opción se elige una de `assets/music/`.
- `--force`, `-f`: Ignorar la caché de **todas** las etapas.
- `--force-script`: Regenerar solo el guion. Ojo: un guion nuevo suele traer prompts nuevos, es decir, **volver a pagar las imágenes**.
- `--force-audio`: Regenerar solo la locución.
- `--force-images`: Regenerar solo las imágenes (consume créditos sin `--mock-images`).
- `--force-video`: Re-renderizar solo clips, mezcla de audio y video final.

Cada ejecución guarda sus opciones en `workspace/<slug>/run_config.json`.

### Corregir una escena: `retry-scene`
Regenera escenas concretas de un proyecto ya generado, reutilizando las opciones de `run_config.json` (no hay que repetirlas):
```powershell
# Otra imagen para la escena 3 (semilla nueva)
python main.py retry-scene --slug motor-gasolina --scene 3

# Varias escenas a la vez
python main.py retry-scene --slug motor-gasolina --scene 3 --scene 7

# Nuevo prompt visual (se guarda en script.json) con semilla fija
python main.py retry-scene --slug motor-gasolina --scene 3 --prompt "Cutaway of a clean engine piston, studio lighting" --seed 1234

# Corregir solo la narración: la imagen se conserva; se rehace audio, clip y video final
python main.py retry-scene --slug motor-gasolina --scene 4 --narration "Texto corregido para la escena cuatro."
```
- `--scene` es repetible; cada escena debe existir. `--prompt` y `--narration` solo se admiten con una única `--scene`.
- Toda escena seleccionada obtiene una imagen nueva, salvo que solo se indique `--narration`.
- Solo se rehacen el audio/imagen/clip afectados y el video final. Al terminar se muestra un resumen con el costo estimado de Fal.ai (imágenes Flux reales × $0.003; $0 en modo mock).

### Estilos visuales
El aspecto de las imágenes lo define un **estilo** del catálogo `STYLES` en `styles.py` (6 incluidos), independiente del perfil.
```powershell
python main.py styles                                   # tabla: id, nombre, descripción y perfiles que lo usan por defecto
python main.py generate --profile curiosity --topic "..." --style clay_miniature
```
- **Resolución:** `--style` explícito > estilo del `script.json` en caché > `default_style_id` del perfil (solo para guiones nuevos). Se guarda en `script.json` (`visual_style_id`) y en `run_config.json`; `retry-scene` usa siempre el estilo del guion.
- **Cambiar el estilo de un video existente** actualiza `script.json` y **regenera todas las imágenes** (el prompt compuesto cambia; ~$0.003 por imagen en modo real). Los `visual_prompt_en` ya escritos corresponden al estilo anterior: usa `--force-script` si quieres que el guionista los reescriba para el estilo nuevo.
- **Añadir un estilo:** agrega una entrada a `STYLES` en `styles.py` (`id`, `label`, `description`, `image_suffix` en inglés de menos de ~45 palabras y `scene_guidance` para el guionista). Aparece solo en `styles` y `--style`. Para que un perfil lo use por defecto, cambia su `default_style_id` en `profiles.py`.
- **Los videos existentes conservan su estilo:** los `script.json` antiguos (sin `visual_style_id`) siguen componiendo el prompt exactamente como antes, así que sus imágenes ya pagadas se reutilizan. Editar el texto de un estilo en `styles.py` solo afecta a los videos que ya lo usan (sus imágenes se regeneran la próxima vez).
- El prompt de imagen se compone como: prompt de la escena, personajes (`Featuring:`), sufijo del estilo y sufijo de composición. Flux schnell trunca a ~256 tokens: si el prompt estima más de 230 tokens, se avisa y se recortan primero las descripciones de personajes (el estilo nunca se elimina).

### Ediciones manuales
Puedes editar `workspace/<slug>/script.json` a mano (narración, prompts, efectos de cámara) y volver a ejecutar `generate` con las mismas opciones: solo se rehacen las escenas modificadas.
