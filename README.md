# ⚡ Short Video Pipeline CLI

Herramienta CLI modular y profesional en Python para automatizar el 100% de la creación de videos verticales (9:16, 1080x1920) para TikTok, Instagram Reels y YouTube Shorts, o apaisados (16:9, 1920x1080) para YouTube con `--format horizontal`, con edición dinámica, efectos de sonido (SFX whoosh), música de fondo (BGM) y subtítulos animados estilo viral quemados en el video.

---

## 🌟 Características Principales

1. **Proveedor de IA Unificado (Fal.ai):**
   - **Guiones:** `openrouter/router` de Fal.ai con `google/gemini-2.5-flash` para estructuración viral en JSON estricto validado con Pydantic (`fal-ai/any-llm` quedó obsoleto en Fal). El costo real del LLM (`usage.cost`) aparece en el resumen final.
   - **Imágenes:** `fal-ai/flux/schnell` a 768x1360 en vertical (~1 megapíxel, ~9:16) o 1280x720 en horizontal (0.92 MP; `flux_image_size` de cada formato en `config.FORMATS`), reescaladas a la resolución del video cubriendo el cuadro y recortando al centro (nunca se estiran). Las imágenes pagadas con el tamaño anterior (`portrait_16_9`, 576x1024) se siguen aceptando en caché; solo `--force-images` / `retry-scene` las rehacen al tamaño nuevo.
2. **Caché por Huella de Contenido (por escena):**
   - Cada proyecto opera en `./workspace/<slug>/` y guarda `cache_manifest.json` con la huella (sha256) de cada artefacto.
   - Solo se rehace lo que realmente cambió: otra voz regenera el audio (y los clips cuya duración cambie), editar a mano la narración o el prompt de una escena en `script.json` rehace solo esa escena, y cambiar `--music` rehace solo el video final.
   - `--force` / `--force-script|audio|images|video` ignoran la caché de una etapa concreta.
   - El guion incluye `hook_text`, `post_description` y `hashtags`; la narración de cada escena tiene un presupuesto de palabras calculado del perfil (`target_scene_duration_sec` x palabras/s de la voz: las medidas del perfil si las tiene, o 2.7 x (1 + velocidad de la voz)) y la frase de cierre del perfil (`ending_call_to_action`) se garantiza al final del último texto.
   - Flags `--mock-script` y `--mock-images`: guion de demostración local e imágenes de prueba con Pillow para depurar todo el flujo sin gastar saldo ($0.00).
3. **Perfiles Desacoplados (`--profile`):**
   - **`curiosity`:** Ritmo rápido (15 a 18 escenas de ~3.5s), voz enérgica (`rate="+22%"`), estilo visual por defecto `explainer_3d` (render 3D con cortes transversales).
   - **`micronovela`:** Ritmo dramático (10 a 12 escenas de ~5s), voz dramática (`rate="+8%"`), consistencia física de personajes inyectada en cada prompt, estilo visual por defecto `telenovela_cine`, y cierre con cliffhanger ("Parte 2 en el perfil").
   - **`que_pasaria`:** escenarios hipotéticos tipo "¿Qué pasaría si...?" (apocalipsis, mundos alterados) con un protagonista recurrente. Voz Jorge a `rate="+40%"`, estilo por defecto `cine_realista`, cierre "Suscríbete para el siguiente escenario".
   - **`historia`:** aventuras narradas protagonizadas por un personaje recurrente. Misma voz, ritmo y estilo que `que_pasaria`; cierre "Suscríbete para la siguiente aventura".
   - **`chisme`:** chismes narrados con un protagonista y personajes secundarios; admite escenarios hipotéticos. Voz Dalia a `rate="+40%"`, estilo por defecto `telenovela_cine` (con `--style cine_realista` para escenarios fantásticos).
   - Los tres son de **YouTube horizontal** por defecto (16:9), con escenas de ~5 s (unas 15 palabras de narración cada una), **~2 minutos** por defecto (`--minutes` lo cambia) y **requieren `--character`**.
4. **Regeneración Quirúrgica (`retry-scene`):** repite la imagen (o corrige la narración) de una escena sin tocar el resto.
5. **Locución y Timings al Milisegundo:**
   - `edge-tts` asíncrono con captura en tiempo real de eventos `WordBoundary`.
6. **Subtítulos Virales Dinámicos:**
   - Archivo `.ass` a la resolución del formato. Vertical (1080x1920): Fontsize 100 con contorno grueso, en la zona segura (centro al 64 % de la altura; evita el 20 % inferior y el 12 % superior). Horizontal (1920x1080): Fontsize 90, contorno 6, centro al 83 % de la altura (quinto inferior).
   - Bloques de 1 a 3 palabras que nunca superan el ancho máximo del formato (~900 px en vertical, ~1480 px en horizontal; los anchos se parten en bloques menores).
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
   - Generación de `post_metadata.txt` con el título, la descripción y los hashtags del guion (con respaldo estático si faltan) más etiquetas de alcance (#shorts #fyp #parati en vertical; ninguna en horizontal, según `reach_hashtags` de cada formato).
8. **Guion Propio (`--script-file`):** importa tu texto como narración palabra por palabra (limpieza de formato, escenas partidas por código, storyboard visual con el LLM, estimación de costo antes de pagar imágenes). Ver *Importar tu propio guion*.

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

`main.py` tiene cinco comandos: **`generate`** (video completo), **`retry-scene`** (regenerar escenas), **`styles`** (listar estilos visuales), **`create-character`** y **`characters`** (biblioteca de personajes). Ejecuta `python main.py --help` para verlos.

### 1. Prueba gratuita (Costo $0.00)
Guion de demostración + imágenes de prueba locales; la voz (`edge-tts`) es real y gratuita:
```powershell
python main.py generate --profile curiosity --topic "Qué pasa en tu motor si usas gasolina barata" --slug motor-gasolina --mock-script --mock-images
```

### 2. Producción real (Fal.ai: guion con openrouter/router + imágenes Flux/schnell)
```powershell
python main.py generate --profile curiosity --topic "Qué pasa en tu motor si usas gasolina barata" --slug motor-gasolina
```
Volver a ejecutar el mismo comando no regenera nada (todo sale "en caché").

### 3. Modo Micronovela (consistencia de personajes y drama)
```powershell
python main.py generate --profile micronovela --topic "Mi hermano hipotecó la casa de mi abuela en secreto" --slug casa-abuela --mock-images
```

### 4. Protagonista recurrente (YouTube horizontal)
Crea primero el personaje (dos candidatas con Seedream v4.5, ~$0.08) y luego genera el video con él:
```powershell
python main.py create-character max --name "Max" --description "Latino man in his early 30s, short dark wavy hair, trimmed beard, athletic build, mustard-yellow hoodie, dark jeans" --style cine_realista --candidates 2
python main.py generate --profile que_pasaria --topic "¿Qué pasaría si despertaras y fueras la última persona en la Tierra?" --character max --style cine_apocaliptico --minutes 3
```
Si no te gusta la candidata elegida, `python main.py create-character max --choose 2` cambia la referencia sin pagar nada.

### 5. Importar tu propio guion
Si ya tienes la narración (la escribiste tú o la pulíste en tu chat con Claude/Gemini), la herramienta la convierte en video **sin reescribirla**: tu texto es la locución palabra por palabra y el LLM solo decide qué imagen va en cada escena y redacta el título, el gancho y los hashtags.
```powershell
python main.py generate --script-file examples/guion_ejemplo_max.txt --profile que_pasaria --character demo-max --scene-seconds 5
python main.py generate --script-file guion.txt --profile que_pasaria --character demo-max --mock-script --mock-images   # prueba gratis
```
- **Cómo escribir el texto:** texto plano UTF-8 en español, pensado para oírse: frases cortas, hechos concretos con cifras o fechas, un hilo claro (gancho, escalada, giro, cierre). Nombra al protagonista en el texto cuando actúe; el storyboard lo pone además como guía del espectador en el 70 % o más de las escenas (también en las de datos o explicaciones: lo señala, lo demuestra o reacciona), salvo unos pocos planos épicos. Las escenas sin él piden `no people, no characters` para que el generador de imágenes no invente personas. Los títulos (`# Guion`), los rótulos cortos en negrita o de sección (`**Escena 1**`, `Escena 3: El apagón`) y los prefijos `Narrador:` se descartan solos, con aviso; cualquier otra línea se narra. Hay un ejemplo en `examples/guion_ejemplo_max.txt`.
- **No se añade nada:** ni gancho ni la frase de cierre del perfil (si quieres un "Suscríbete para...", escríbelo en tu texto), ni presupuesto de palabras. `--topic` es opcional (por defecto, el nombre del archivo; solo da nombre a la carpeta de `workspace/`).
- **Limpieza automática** (un único aviso amarillo dice qué se quitó): formato Markdown de los chats (`#`, `**`, `_`, `>`, viñetas), emojis y acotaciones entre (paréntesis) o [corchetes]. Se conserva la puntuación española (¿ ¡ … — « »). Los párrafos sin punto final reciben uno para que la voz haga la pausa. Una línea con solo `---` fuerza un corte de escena. Si el nombre del protagonista no aparece en el texto, se avisa.
- **Escenas:** las parte el código, nunca el LLM, así que la narración no puede cambiar: primero en frases (sin cortar en decimales, `Sr.`, `Dra.`, `etc.`) y luego se agrupan hacia ~N palabras por escena (de ~55 % a ~145 % de N), cortando en comas o antes de "y", "pero", "porque"... si una frase es larga. `--scene-seconds` (por defecto, la duración de escena del perfil, 5 s) fija cada cuánto cambia la imagen: N = segundos x palabras por segundo de la voz (~15 palabras con 5 s).
- **Cuánto texto:** a +40 % (voz de `que_pasaria`, `historia` y `chisme`) se leen unas 2.95 palabras por segundo, ~175 por minuto:

| Duración | Palabras | Escenas (5 s) | Imágenes con protagonista (~$0.04) |
|---|---|---|---|
| 30 s | ~90 | 6 | ~$0.24 |
| 1 min | ~175 | 12 | ~$0.48 |
| 2 min | ~350 | 24 | ~$0.96 |
| 3 min | ~525 | 36 | ~$1.44 |
| 5 min | ~875 | 60 | ~$2.40 |

- **Estimación y confirmación:** con el guion ya listo y antes de la voz y de pagar imágenes, se muestra un panel con las palabras, la duración estimada (palabras / palabras por segundo del perfil; suele quedar algo por encima de la real), las escenas, las imágenes por pagar (las ya en caché no cuentan), el costo por imagen y el total, y se pregunta `[y/N]` (por defecto No). `--yes` no pregunta; con `--mock-images`, o si no hay nada que pagar, tampoco. Si dices que no, no se genera ninguna imagen y el storyboard queda guardado: repetir el comando continúa desde ahí.
- **Caché:** se guarda una copia del texto limpio en `workspace/<slug>/source_script.txt` y `run_config.json` recuerda que es un proyecto importado. Repetir el mismo comando no llama al LLM ni regenera nada. La huella del guion es el texto limpio + perfil + protagonista + `--scene-seconds` + la versión del storyboard: si cambias el texto, se rehace el storyboard (aviso amarillo) y, como los prompts visuales nuevos regeneran sus imágenes, lo normal es volver a pagar casi todas (el panel de estimación te lo muestra antes). Editar a mano `script.json` se respeta como siempre.
- **`retry-scene` en un proyecto importado** funciona sin el `.txt` original y nunca llama al LLM del storyboard: usa `script.json` (con `--narration` o `--prompt` corriges una escena y no se añade ninguna frase de cierre).
- **Textos largos:** hasta ~40 escenas el storyboard es una sola llamada; con más, una llamada global (título, publicación, personajes secundarios) y luego bloques de ~40 escenas que reciben los mismos personajes (los secundarios se mantienen entre bloques).

### Opciones de `generate`
- `--profile`, `-p`: `curiosity`, `micronovela`, `que_pasaria`, `historia` o `chisme` (ver *Características*).
- `--topic`, `-t`: Tema del video (requerido, salvo con `--script-file`).
- `--slug`, `-s`: Nombre de la carpeta en `./workspace/`.
- `--mock-script`: Guion de demostración local en lugar de Fal.ai (no respeta el tema).
- `--mock-images`, `-m`: Imágenes de prueba con Pillow en lugar de Flux.
- `--voice`, `-v`: Voz TTS (ej. `es-MX-JorgeNeural`, `es-MX-DaliaNeural`).
- `--style`: Estilo visual (ver `python main.py styles` y la sección *Estilos visuales*). Por defecto, el del guion en caché o el del perfil.
- `--format`: `vertical` (1080x1920, Shorts/Reels/TikTok) o `horizontal` (1920x1080, YouTube); ver *Formatos de video*. Por defecto, el del proyecto existente o el `default_format` del perfil.
- `--character`: Slug de un personaje de la biblioteca (ver *Biblioteca de personajes*). Obligatorio en `que_pasaria`, `historia` y `chisme`; con protagonista las imágenes usan Seedream v4.5 (~$0.04 c/u) en lugar de Flux.
- `--minutes`: Duración objetivo en minutos (de 0.5 a 15); ajusta el número de escenas del guion. Por defecto, la del perfil. No se usa con `--script-file`.
- `--script-file`: Importa tu propio guion (`.txt`): es la narración tal cual y el LLM solo hace el storyboard visual (ver *Importar tu propio guion*).
- `--scene-seconds`: Solo con `--script-file`: cada cuántos segundos cambia la imagen (2 a 20). Por defecto, la duración de escena del perfil.
- `--yes`, `-y`: Solo con `--script-file`: no pedir confirmación tras la estimación de costo.
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
- Solo se rehacen el audio/imagen/clip afectados y el video final. Al terminar se muestra un resumen con el costo real de Fal.ai (imágenes pagadas × precio de cada modelo; $0 en modo mock). Ver *Costos*.

### Biblioteca de personajes
Los perfiles con protagonista (`que_pasaria`, `historia`, `chisme`) usan un personaje persistente guardado en `characters/<slug>/`, para que tenga la misma cara en todas las escenas:
```powershell
python main.py create-character <slug> --name "..." --description "..." [--style <id>] [--candidates N]   # genera N candidatas con Seedream v4.5
python main.py create-character <slug> --from-image ruta\a\foto.png                                       # importa tu propia imagen (sin costo)
python main.py create-character <slug> --choose 2                                                         # cambia la referencia a la candidata 2 (sin costo)
python main.py characters                                                                                 # lista la biblioteca
```
- **Candidatas:** `--candidates` va de 1 a 6 (por defecto 2) y cada una cuesta $0.04 (Seedream v4.5 text-to-image, 2048x2048). La descripción física (`--description`) va **en inglés** y se inyecta en los prompts de las escenas; `--style` define el look de las candidatas.
- **Referencia:** `ref.png` es una copia de la candidata elegida (por defecto la 1). Cambiarla con `--choose` no genera nada, pero las escenas con el protagonista se regeneran la próxima vez que se haga un video que lo use.
- **Estructura:** `characters/<slug>/character.json` (nombre, descripción, origen y candidata elegida), `ref.png` (la referencia) y `candidate_NN.png`. El slug admite letras, números, `-` y `_`. La carpeta `characters/` está en `.gitignore`: las imágenes no se suben a git.
- **Tip:** una prenda o accesorio distintivo en la descripción (p. ej. "mustard-yellow hoodie", "round red glasses") ayuda a que el personaje se reconozca igual en todas las escenas.

### Estilos visuales
El aspecto de las imágenes lo define un **estilo** del catálogo `STYLES` en `styles.py` (9 incluidos), independiente del perfil.

| ID | Nombre | Por defecto en |
|---|---|---|
| `explainer_3d` | Explicativo 3D | `curiosity` |
| `macro_lab` | Macro de laboratorio | - |
| `clay_miniature` | Miniatura de plastilina | - |
| `telenovela_cine` | Telenovela cinematográfica | `micronovela`, `chisme` |
| `noir_suspense` | Suspenso noir | - |
| `graphic_novel` | Novela gráfica | - |
| `cine_realista` | Cine realista | `que_pasaria`, `historia` |
| `cine_apocaliptico` | Cine apocalíptico | - |
| `animacion_3d` | Animación 3D | - |

```powershell
python main.py styles                                   # tabla: id, nombre, descripción y perfiles que lo usan por defecto
python main.py generate --profile curiosity --topic "..." --style clay_miniature
```
- **Resolución:** `--style` explícito > estilo del `script.json` en caché > `default_style_id` del perfil (solo para guiones nuevos). Se guarda en `script.json` (`visual_style_id`) y en `run_config.json`; `retry-scene` usa siempre el estilo del guion.
- **Cambiar el estilo de un video existente** actualiza `script.json` y **regenera todas las imágenes** (el prompt compuesto cambia; ~$0.003 por imagen en modo real). Los `visual_prompt_en` ya escritos corresponden al estilo anterior: usa `--force-script` si quieres que el guionista los reescriba para el estilo nuevo.
- **Añadir un estilo:** agrega una entrada a `STYLES` en `styles.py` (`id`, `label`, `description`, `image_suffix` en inglés de menos de ~45 palabras y `scene_guidance` para el guionista). Aparece solo en `styles` y `--style`. Para que un perfil lo use por defecto, cambia su `default_style_id` en `profiles.py`.
- **Los videos existentes conservan su estilo:** los `script.json` antiguos (sin `visual_style_id`) siguen componiendo el prompt exactamente como antes, así que sus imágenes ya pagadas se reutilizan. Editar el texto de un estilo en `styles.py` solo afecta a los videos que ya lo usan (sus imágenes se regeneran la próxima vez).
- El prompt de imagen se compone como: prompt de la escena, personajes (`Featuring:`), sufijo del estilo y sufijo de composición. Flux schnell trunca a ~256 tokens: si el prompt estima más de 230 tokens, se avisa y se recortan primero las descripciones de personajes (el estilo nunca se elimina).

### Formatos de video
Cada video tiene un **formato** del catálogo `FORMATS` en `config.py` (`vertical` o `horizontal`), que define la resolución, el tamaño de la imagen Flux, la composición que se añade a cada prompt de imagen, el texto del guionista y la geometría de subtítulos y gancho.
```powershell
python main.py generate --profile curiosity --topic "..." --format horizontal   # YouTube 16:9, 1920x1080
```
- **Resolución:** `--format` explícito > `video_format` del `script.json` en caché > `video_format` de `run_config.json` > `default_format` del perfil (solo para proyectos nuevos). Se guarda en `script.json` y en `run_config.json`; `retry-scene` usa siempre el formato del guion.
- **Los videos existentes siguen siendo verticales:** los `script.json` y `run_config.json` antiguos (sin `video_format`) se leen como `vertical` y sus huellas de caché no cambian, así que no se vuelve a pagar nada.
- **Cambiar el formato de un video existente** actualiza `script.json` y regenera todas las imágenes (~$0.003 cada una en modo real) y los clips; la locución y la mezcla de audio se conservan. Los guiones legados (sin estilo visual) solo admiten `vertical`: para pasarlos a horizontal indica también `--style`.
- **Añadir un formato:** agrega una entrada a `FORMATS` en `config.py` (el tamaño Flux con lados múltiplos de 16 y hasta ~1 megapíxel).

### Costos
Precios de Fal.ai por unidad; el resumen de cada ejecución muestra el costo real (imágenes pagadas por modelo + guion).

| Concepto | Modelo | Costo |
|---|---|---|
| Imagen, video vertical u horizontal sin protagonista | `fal-ai/flux/schnell` | ~$0.003 por imagen |
| Imagen, proyecto con protagonista | `fal-ai/bytedance/seedream/v4.5` (edit con la referencia cuando sale el protagonista; text-to-image el resto) | $0.04 por imagen |
| Candidata de personaje (`create-character`) | Seedream v4.5 text-to-image | $0.04 por candidata |
| Importar imagen de personaje (`--from-image`) | - | $0.00 |
| Guion | `openrouter/router` (`google/gemini-2.5-flash`) | ~$0.01 por guion (`usage.cost`) |
| Storyboard de un guion importado | `openrouter/router` (`google/gemini-2.5-flash`) | ~$0.006 con 17 escenas; ~$0.03 con ~100 escenas |
| Locución | `edge-tts` | Gratis |

- **Vertical:** ~$0.003 por imagen; un short de `curiosity` (15 a 18 escenas) ronda los $0.05 en imágenes.
- **Con protagonista:** ≈ **$0.50–0.60 por minuto** de video (unas 12 imágenes por minuto a $0.04 cada una). Ejemplo medido: un video de 45 s con 7 imágenes costó $0.28.
- Las imágenes servidas de la caché y los modos `--mock-*` no cuestan nada.

### Ediciones manuales
Puedes editar `workspace/<slug>/script.json` a mano (narración, prompts, efectos de cámara) y volver a ejecutar `generate` con las mismas opciones: solo se rehacen las escenas modificadas.
