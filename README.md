# ⚡ Short Video Pipeline CLI

Herramienta CLI modular y profesional en Python para automatizar el 100% de la creación de videos verticales (9:16, 1080x1920) para TikTok, Instagram Reels y YouTube Shorts, con edición dinámica, efectos de sonido (SFX whoosh), música de fondo (BGM) y subtítulos animados estilo viral quemados en el video.

---

## 🌟 Características Principales

1. **Proveedor de IA Unificado (Fal.ai):**
   - **Guiones:** `fal-ai/any-llm` configurado con `google/gemini-2.5-flash` para estructuración viral en JSON estricto validado con Pydantic.
   - **Imágenes:** `fal-ai/flux/schnell` en resolución vertical nativa 9:16 (`portrait_16_9`).
2. **Idempotencia y Caché Estricto por Etapa:**
   - Cada proyecto opera en `./workspace/<slug>/`.
   - Reutiliza guiones, audios e imágenes existentes a menos que uses `--force`.
   - Flag `--mock-images`: Genera imágenes locales de prueba con degradado oscuro en Pillow para depurar todo el flujo sin gastar saldo ($0.00).
3. **Perfiles Desacoplados (`--profile`):**
   - **`curiosity`:** Ritmo rápido (13 a 18 escenas de 3 a 4s), voz enérgica (`rate="+12%"`), estilo visual 3D Zack D. Films cutaway render.
   - **`micronovela`:** Ritmo dramático (9 a 12 escenas de ~5s), voz dramática (`rate="+8%"`), consistencia física de personajes inyectada en cada prompt, y cierre con cliffhanger ("Parte 2 en el perfil").
4. **Locución y Timings al Milisegundo:**
   - `edge-tts` asíncrono con captura en tiempo real de eventos `WordBoundary`.
5. **Subtítulos Virales Dinámicos:**
   - Archivo `.ass` a resolución 1080x1920 con bloques de 2-3 palabras.
   - Animación pop-in (`{\fscx120\fscy120\t(0,65,\fscx100\fscy100)}`).
   - Resaltado automático de palabras clave en Amarillo Brillante (`&H0000FFFF&`).
6. **Ensamblado FFmpeg con Zero-Jitter:**
   - Movimiento de cámara Ken Burns suave (`zoom_in_fast`, `zoom_in_slow`, `zoom_out`, `pan_right`, `pan_left`) con supersampling.
   - Efecto de sonido `whoosh` en cada corte de escena a `volume=0.18`.
   - Música ambiental sutil a `volume=0.10` con fade-out al final.
   - Generación de `post_metadata.txt` con título, descripción y hashtags.

---

## 🚀 Instalación y Requisitos

### Requisitos del Sistema
- Python 3.10+
- FFmpeg con soporte `libass` (instalado automáticamente via `ffmpeg-full` en macOS).

### Configuración
```bash
cd short-video-pipeline
source .venv/bin/activate
pip install -r requirements.txt
```

Copia tu clave de Fal.ai en el archivo `.env`:
```env
FAL_KEY=tu_clave_aqui
```

---

## 🎬 Ejemplos de Uso

### 1. Prueba gratuita (Costo $0.00 con `--mock-images`)
Genera la voz real con `edge-tts`, aplica zoompan, mezcla SFX + BGM y quema subtítulos animados usando imágenes de prueba locales:
```bash
python main.py --profile curiosity --topic "Qué pasa en tu motor si usas gasolina barata" --slug motor-gasolina --mock-images
```

### 2. Producción Real (con Fal.ai Flux/schnell)
```bash
python main.py --profile curiosity --topic "Qué pasa en tu motor si usas gasolina barata" --slug motor-gasolina
```

### 3. Modo Micronovela (Consistencia de personajes y drama)
```bash
python main.py --profile micronovela --topic "Mi hermano hipotecó la casa de mi abuela en secreto" --slug casa-abuela --mock-images
```

### Opciones y Flags Disponibles:
- `--profile`, `-p`: `curiosity` o `micronovela`.
- `--topic`, `-t`: Tema del video (requerido).
- `--slug`, `-s`: Nombre de la carpeta en `./workspace/`.
- `--mock-images`, `-m`: Generar imágenes de prueba con Pillow.
- `--force`, `-f`: Forzar regeneración ignorando la caché.
- `--voice`, `-v`: Cambiar voz TTS (ej. `es-MX-JorgeNeural`, `es-MX-DaliaNeural`).
- `--music`: Ruta a un archivo MP3 de música personalizado.
