# ⚡ Short Video Pipeline CLI

Herramienta CLI modular y profesional en Python para automatizar el 100% de la creación de videos verticales (9:16, 1080x1920) para TikTok, Instagram Reels y YouTube Shorts, con edición dinámica, efectos de sonido (SFX whoosh), música de fondo (BGM) y subtítulos animados estilo viral quemados en el video.

---

## 🌟 Características Principales

1. **Proveedor de IA Unificado (Fal.ai):**
   - **Guiones:** `fal-ai/any-llm` configurado con `google/gemini-2.5-flash` para estructuración viral en JSON estricto validado con Pydantic.
   - **Imágenes:** `fal-ai/flux/schnell` en resolución vertical nativa 9:16 (`portrait_16_9`).
2. **Caché por Huella de Contenido (por escena):**
   - Cada proyecto opera en `./workspace/<slug>/` y guarda `cache_manifest.json` con la huella (sha256) de cada artefacto.
   - Solo se rehace lo que realmente cambió: otra voz regenera el audio (y los clips cuya duración cambie), editar a mano la narración o el prompt de una escena en `script.json` rehace solo esa escena, y cambiar `--music` rehace solo el video final.
   - `--force` / `--force-script|audio|images|video` ignoran la caché de una etapa concreta.
   - Flags `--mock-script` y `--mock-images`: guion de demostración local e imágenes de prueba con Pillow para depurar todo el flujo sin gastar saldo ($0.00).
3. **Perfiles Desacoplados (`--profile`):**
   - **`curiosity`:** Ritmo rápido (15 a 18 escenas de ~3.5s), voz enérgica (`rate="+22%"`), estilo visual 3D Zack D. Films cutaway render.
   - **`micronovela`:** Ritmo dramático (10 a 12 escenas de ~5s), voz dramática (`rate="+8%"`), consistencia física de personajes inyectada en cada prompt, y cierre con cliffhanger ("Parte 2 en el perfil").
4. **Regeneración Quirúrgica (`retry-scene`):** repite la imagen (o corrige la narración) de una escena sin tocar el resto.
5. **Locución y Timings al Milisegundo:**
   - `edge-tts` asíncrono con captura en tiempo real de eventos `WordBoundary`.
6. **Subtítulos Virales Dinámicos:**
   - Archivo `.ass` a resolución 1080x1920 con bloques de 2-3 palabras.
   - Animación pop-in (`{\fscx120\fscy120\t(0,65,\fscx100\fscy100)}`).
   - Resaltado automático de palabras clave en Amarillo Brillante (`&H0000FFFF&`).
7. **Ensamblado FFmpeg con Zero-Jitter:**
   - Movimiento de cámara Ken Burns suave (`zoom_in_fast`, `zoom_in_slow`, `zoom_out`, `pan_right`, `pan_left`) con supersampling.
   - Efecto de sonido `whoosh` en cada corte de escena a `volume=0.18`.
   - Música ambiental sutil a `volume=0.10` con fade-out al final.
   - Generación de `post_metadata.txt` con título, descripción y hashtags.

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

`main.py` tiene dos comandos: **`generate`** (video completo) y **`retry-scene`** (regenerar escenas). Ejecuta `python main.py --help` para verlos.

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
- `--music`: Ruta a un archivo MP3 de música de fondo personalizado.
- `--force`, `-f`: Ignorar la caché de **todas** las etapas.
- `--force-script`: Regenerar solo el guion. Ojo: un guion nuevo suele traer prompts nuevos, es decir, **volver a pagar las imágenes**.
- `--force-audio`: Regenerar solo la locución.
- `--force-images`: Regenerar solo las imágenes (consume créditos sin `--mock-images`).
- `--force-video`: Re-renderizar solo clips y video final.

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

### Ediciones manuales
Puedes editar `workspace/<slug>/script.json` a mano (narración, prompts, efectos de cámara) y volver a ejecutar `generate` con las mismas opciones: solo se rehacen las escenas modificadas.
