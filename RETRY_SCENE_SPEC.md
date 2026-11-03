> **Estado: implementado.** Comando real:
> `python main.py retry-scene --slug X --scene N [--scene M ...] [--prompt "..."] [--narration "..."] [--seed INT]`
> - `--scene` repetible; `--prompt`/`--narration` solo con una única escena y se persisten en `script.json`.
> - Se re-genera la imagen de cada escena indicada salvo que solo se pase `--narration` (el clip se rehace igualmente porque cambia la duración).
> - Usa `run_config.json` del proyecto (opciones de `generate`) y el caché por huella de contenido: solo se rehacen el audio, imagen y clip afectados y el video final.
> - Al final imprime el resumen y el costo estimado de Fal.ai (imágenes Flux reales × $0.003; $0 en mock).
> - Difiere del diseño original de abajo: no existe `regenerate_single_scene_image()`; la lógica pasa por `run_pipeline()` con huellas de contenido.

# 🔄 Propuesta de Feature: Regeneración Quirúrgica de Escena (`retry-scene`)

Documento técnico que detalla la viabilidad, arquitectura y pasos de implementación para regenerar una sola foto/frame y reensamblar el video final sin regenerar el resto del proyecto.

---

## 🎯 Objetivo
Permitir al creador corregir una escena específica cuyo resultado visual en Fal.ai Flux no fue satisfactorio (ej. escena con deformidades o que no coincide con la narración), **sin volver a pagar por las demás imágenes ni re-sintetizar el audio**.

---

## 💡 ¿Por qué es 100% Viable?
El pipeline ya fue diseñado con **desacoplamiento total e idempotencia por escena**:
1. **Audios intactos:** `audio/scene_XX.mp3` y `audio/timing_metadata.json` ya tienen la duración exacta en milisegundos y no se tocan.
2. **Subtítulos intactos:** `subtitles.ass` conserva la sincronización de tiempos continua.
3. **Imágenes y clips aislados:** Cada escena tiene su archivo físico independiente:
   - Imagen: `images/scene_XX.png`
   - Clip renderizado: `clips/clip_XX.mp4`

---

## ⚡ Flujo de Ejecución (Paso a Paso)

Cuando el usuario ejecute:
```bash
python main.py retry-scene --slug <slug> --scene <id> [--prompt "nuevo prompt opcional"]
```

1. **Lectura de contexto:**
   - Carga `workspace/<slug>/script.json` y ubica la escena con el `scene_id` indicado.
2. **Generación de la nueva imagen:**
   - Si se pasó `--prompt`, usa ese prompt. Si no, usa el prompt original del script.
   - Llama a `fal-ai/flux/schnell` (`portrait_16_9`) cobrando **únicamente 1 imagen** (~$0.003 USD).
   - Sobrescribe `workspace/<slug>/images/scene_XX.png`.
3. **Re-renderizado del clip individual:**
   - Ejecuta `render_scene_clip()` aplicando el efecto `zoompan` correspondiente a la duración exacta del audio de esa escena.
   - Sobrescribe únicamente `workspace/<slug>/clips/clip_XX.mp4` (~3 segundos de proceso).
4. **Re-ensamblado final:**
   - Ejecuta `assemble_final_video()` con `-f` (force).
   - Como los demás 15 clips ya existen, FFmpeg simplemente concatena la lista y quema los subtítulos en un solo paso (~4 segundos).
5. **Resultado:**
   - `workspace/<slug>/final_video.mp4` actualizado en **menos de 10 segundos**.

---

## 💻 Cambios de Código Requeridos para Implementarla

### 1. En `image_engine.py`:
Agregar función para generar una sola imagen aislada:
```python
async def regenerate_single_scene_image(
    scene_id: int,
    slug: str,
    custom_prompt: Optional[str] = None,
) -> Path: ...
```

### 2. En `main.py`:
Agregar el subcomando de Typer:
```python
@app.command()
def retry_scene(
    slug: str = typer.Option(..., "--slug", "-s", help="Slug del proyecto"),
    scene: int = typer.Option(..., "--scene", help="Número de escena a regenerar (1, 2, 3...)"),
    prompt: Optional[str] = typer.Option(None, "--prompt", help="Prompt visual personalizado para sustituir el original"),
):
    """Regenera la imagen de una sola escena y reensambla el video final en segundos."""
    ...
```

---

## 💰 Costo y Tiempos Estimados
- **Costo en créditos Fal.ai:** ~$0.003 USD (93% de ahorro vs generar todo el video).
- **Tiempo total de ejecución:** ~8 a 10 segundos.
