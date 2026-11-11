"""Plantillas de prompt del guionista (LLM). Solo contenido: la lógica vive en script_engine.build_system_prompt.

Para ajustar el tono o las reglas de un perfil, edita PROFILE_INSTRUCTIONS. Para cambiar cómo se describen las
imágenes, edita el estilo correspondiente en styles.py (scene_guidance) — no hace falta tocar este archivo.
"""

# Instrucciones narrativas por perfil (español). Se insertan en {profile_instructions}.
PROFILE_INSTRUCTIONS: dict[str, str] = {
    "curiosity": """
FORMATO: short vertical de 30 a 35 segundos de datos curiosos (ciencia, mecánica, cuerpo humano, cómo funcionan las
cosas): unas 8 escenas de ~4 segundos (si más abajo se pide otro número EXACTO de escenas, ese manda), una idea
por escena, a ritmo rápido.
ESTRUCTURA OBLIGATORIA:
- Escena 1 (gancho): una pregunta o afirmación de CHOQUE directa, legible en menos de 3 segundos, sobre algo que
  le importa al espectador (su dinero, su salud, su seguridad, algo que hace todos los días). Entra de golpe:
  sin saludo, sin introducción y sin anunciar lo que vas a explicar. Ejemplo de tono: "Tu auto pierde potencia
  cada vez que haces esto sin saberlo."
- Escenas 2 a 6: cada escena revela UN dato nuevo y concreto que avanza la explicación paso a paso, como una cadena
  de causa y efecto. Nada de relleno ni de repetir lo ya dicho. Hacia la mitad, reabre la curiosidad UNA vez con un
  micro-gancho breve ("pero eso no es lo peor", "y aquí viene lo raro"); no uses uno más.
- Escena 7 (u 8 si el guion tiene 9): el dato más impactante, el payoff que prometió el gancho.
- Última escena: una frase conectora corta que haga LOOP natural con la escena 1 (que al terminar el video el
  espectador sienta que la primera frase vuelve a empezar) o que invite a ver el video completo relacionado
  ("En el video completo te cuento por qué"). Sin despedidas ni "gracias por ver".
ESTILO DE NARRACIÓN:
- Segunda persona ("tú"), español neutro latinoamericano, frases cortas: una sola frase por escena, de 9 a 11 palabras,
  con palabras cortas y comunes (evita tecnicismos largos si hay una palabra simple).
- Cifras y comparaciones concretas en vez de adjetivos vagos ("tres veces más caliente que un horno", no
  "muy caliente").
- Precisión: no inventes datos. Si una cifra es aproximada, dilo ("casi", "más de", "alrededor de").
- Sin consejos médicos o financieros categóricos; explica el fenómeno, no prescribas.
""".strip(),
    "micronovela": """
FORMATO: micronovela vertical de 30 a 35 segundos narrada en primera persona por la protagonista o el protagonista
(formato storytime): secretos familiares, traiciones, herencias, infidelidades, suspenso. Unas 8 escenas de
~4 segundos (si más abajo se pide otro número EXACTO de escenas, ese manda), una idea o un golpe de tensión por escena.
ESTRUCTURA OBLIGATORIA:
- Escena 1 (gancho): empieza en el momento de mayor tensión con una confesión o afirmación de CHOQUE directa,
  legible en menos de 3 segundos ("Mi suegra me dio un sobre que no debía abrir."). Sin saludo, sin
  presentaciones y sin contexto previo.
- Escenas 2 y 3: el contexto mínimo indispensable, siempre con tensión creciente.
- Escena 4 o 5: un giro que cambie lo que el espectador creía.
- Escenas siguientes: la revelación escala hasta el punto más alto.
- Última escena: un cliffhanger breve, de pocas palabras, que deje una pregunta abierta, haga loop con la
  escena 1 y obligue a buscar la parte 2 (la frase de cierre del perfil va dentro de esa escena y cuenta en el
  máximo de palabras).
ESTILO DE NARRACIÓN:
- Primera persona, emocional y coloquial, español neutro latinoamericano. Una sola frase corta por escena, de 10 a
  12 palabras, ritmo de confesión.
- Detalles sensoriales y objetos concretos (una carta, un anillo, un mensaje en el celular) que hagan la
  historia visual.
- Diálogo en estilo indirecto o citas muy breves dentro de la narración (la voz es una sola).
SEGURIDAD DE MARCA (para no perder monetización):
- Sin contenido sexual explícito, sin violencia gráfica, sin menores en situaciones románticas o de riesgo,
  sin insultos fuertes. El drama viene de los secretos y las decisiones, no de lo explícito.
PERSONAJES:
- Define de 2 a 3 personajes en 'characters'. Cada descripción en inglés, máximo 30 palabras: edad exacta,
  tono de piel, rasgos faciales, peinado y una prenda o accesorio distintivo que se repita en todas las escenas.
- En cada escena lista en 'characters_in_scene' los IDs que aparecen en la imagen.
""".strip(),
    "que_pasaria": """
FORMATO: video de escenarios hipotéticos "¿Qué pasaría si…?" (apocalipsis de ficción, mundos alterados,
situaciones imposibles) en el que UN mismo personaje recurrente atraviesa el escenario y toma decisiones.
ESTRUCTURA OBLIGATORIA (se reparte según el número total de escenas, sean las que sean):
- Escena 1 (gancho): plantea la premisa como pregunta o afirmación con algo en juego y deja al protagonista ya
  dentro de la situación. Ejemplo de tono: "¿Qué pasaría si mañana amaneciera… y nadie más lo hiciera?"
- Primer cuarto del video: las consecuencias inmediatas, una por escena, como cadena de causa y efecto, y las
  primeras decisiones del protagonista.
- Segundo y tercer cuarto: el escenario escala. Cada 5 o 6 escenas, una complicación o un giro que cambia las
  reglas (un peligro nuevo, un recurso que se agota, un hallazgo inesperado) con un micro-gancho ("pero eso no
  es lo peor", "y entonces pasa algo que nadie esperaba").
- Último cuarto: el punto más extremo del escenario y su desenlace (el protagonista sobrevive, se adapta o
  descubre algo).
- Última escena: un cierre memorable que conecte con la pregunta inicial o una pregunta directa al espectador
  ("¿Tú habrías sobrevivido?") que invite a comentar.
ESTILO DE NARRACIÓN:
- Narrador en tercera persona que nombra al protagonista; de vez en cuando puede dirigirse al espectador
  ("imagina que…", "¿qué harías tú?"). Español neutro latinoamericano, frases cortas de máximo 15 palabras.
- Lógica interna: cada consecuencia sale de la anterior y es creíble dentro del escenario. Cifras y detalles
  concretos en vez de adjetivos vagos. Es ficción especulativa: no presentes como hecho real datos científicos
  inventados.
- Cada escena aporta algo nuevo (un lugar, un peligro, una decisión): nada de relleno ni de repetir lo ya dicho.
SEGURIDAD DE MARCA (para no perder monetización):
- Sin violencia gráfica ni gore, sin contenido sexual, sin menores en peligro, sin política ni religión y sin
  catástrofes reales con víctimas (guerras, pandemias o desastres que ocurrieron de verdad). El peligro se
  sugiere; la tensión viene del ingenio del protagonista, no de lo explícito.
PERSONAJES:
- Si más abajo aparece el bloque 'PROTAGONISTA FIJO', ese personaje ya existe: no lo definas en 'characters'.
- Si no aparece, define al protagonista en 'characters' (descripción en inglés, máximo 30 palabras: edad exacta,
  tono de piel, rasgos faciales, peinado y una prenda distintiva que se repita) y lístalo en cada escena donde
  aparezca.
- Los personajes secundarios (un sobreviviente, un rival) son opcionales y se definen igual en 'characters'.
""".strip(),
    "historia": """
FORMATO: historia de aventura narrada (viaje, misión, rescate, exploración, misterio) con UN personaje
recurrente como protagonista.
ESTRUCTURA OBLIGATORIA (se reparte según el número total de escenas, sean las que sean):
- Escena 1 (gancho): empieza en plena acción o con el problema ya encima del protagonista ("Tenía tres minutos
  para cruzar el puente… y el puente se estaba cayendo."). Nada de presentaciones.
- Primer cuarto: qué quiere el protagonista, qué se pierde si fracasa y los primeros obstáculos.
- Segundo y tercer cuarto: obstáculos cada vez más difíciles. Cada 5 o 6 escenas, un giro (una traición, un
  aliado inesperado, un mapa falso, un plan que falla) con un micro-gancho. Alterna la acción con momentos de
  respiro para que el ritmo no se aplane.
- Hacia el 75 % del video: el punto más bajo, donde todo parece perdido.
- Último cuarto: el clímax y una resolución que cumpla lo que prometió el gancho.
- Última escena: un cierre emotivo o ingenioso que deje la puerta abierta a la siguiente aventura.
ESTILO DE NARRACIÓN:
- Narrador en tercera persona que nombra al protagonista, con un solo tiempo verbal en todo el guion (el
  presente narrativo da inmediatez). Español neutro latinoamericano, frases cortas de máximo 15 palabras.
- Acciones concretas ("salta", "corta la cuerda") mejor que descripciones largas. Detalles sensoriales y
  objetos que hagan la historia visual (una llave, un mapa, una linterna).
- Diálogo en estilo indirecto o citas muy breves dentro de la narración (la voz es una sola).
SEGURIDAD DE MARCA (para no perder monetización):
- Sin violencia gráfica ni gore, sin contenido sexual, sin menores en peligro real, sin insultos fuertes. La
  tensión viene de los obstáculos y las decisiones, no de lo explícito.
PERSONAJES:
- Si más abajo aparece el bloque 'PROTAGONISTA FIJO', ese personaje ya existe: no lo definas en 'characters'.
- Si no aparece, define al protagonista en 'characters' (descripción en inglés, máximo 30 palabras: edad exacta,
  tono de piel, rasgos faciales, peinado y una prenda distintiva que se repita) y lístalo en cada escena donde
  aparezca.
- Aliados y antagonistas son opcionales y se definen igual en 'characters'.
""".strip(),
    "chisme": """
FORMATO: chisme narrado (storytime): un narrador chismoso y cómplice cuenta lo que le pasó al protagonista y a
otros personajes (traiciones, secretos, malentendidos, escándalos de barrio, de oficina o de familia). Si el
tema lo pide, puede fusionarse con un escenario hipotético ("¿Qué pasaría si…?"): el chisme ocurre dentro de ese
escenario.
ESTRUCTURA OBLIGATORIA (se reparte según el número total de escenas, sean las que sean):
- Escena 1 (gancho): suelta la bomba o el rumor más jugoso SIN resolverlo ("Dicen que lo que pasó en esa fiesta
  no se lo cuentan a nadie… pero yo me enteré."). Nada de presentaciones.
- Primer cuarto: quién es quién y el detalle que desata el enredo, siempre con tensión creciente.
- Segundo y tercer cuarto: cada 4 o 5 escenas, una revelación que cambia lo que el espectador creía ("pero
  espera, que hay más", "y aquí viene lo mejor").
- Hacia el 75 % del video: el secreto grande sale a la luz.
- Último cuarto: las consecuencias y el remate.
- Última escena: un remate con ironía o un cliffhanger que deje al espectador comentando de qué lado está
  ("¿Tú de qué lado estás?").
ESTILO DE NARRACIÓN:
- Narrador en tercera persona con voz chismosa: coloquial, cómplice y expresiva, con preguntas retóricas y
  apartes al espectador ("¿Tú qué harías?", "y aquí viene lo bueno"). Nombra al protagonista y a los demás
  personajes por su nombre. Español neutro latinoamericano, frases cortas de máximo 15 palabras.
- Detalles concretos que hagan jugoso el chisme (un mensaje en el celular, un anillo, una invitación, un audio
  filtrado).
- Diálogo en estilo indirecto o citas muy breves dentro de la narración (la voz es una sola).
SEGURIDAD DE MARCA (para no perder monetización):
- Personajes y hechos ficticios: nada de personas reales ni famosos ni nada que pueda leerse como difamación.
  Sin contenido sexual explícito, sin violencia gráfica, sin menores en situaciones románticas o de riesgo, sin
  insultos fuertes. El drama viene de los secretos y las decisiones, no de lo explícito.
PERSONAJES:
- Define de 1 a 3 personajes secundarios en 'characters' (al menos 1: la pareja, el rival, la vecina…).
  Cada descripción en inglés, máximo 30 palabras: edad exacta, tono de piel, rasgos faciales, peinado y una
  prenda o accesorio distintivo que se repita en todas las escenas.
- Si más abajo aparece el bloque 'PROTAGONISTA FIJO', ese personaje ya existe: no lo definas en 'characters'.
  Si no aparece, define también al protagonista con el mismo formato.
- En cada escena lista en 'characters_in_scene' los IDs que aparecen en la imagen.
""".strip(),
}


# Bloque que se inserta tras la guía de estilo cuando el video tiene un protagonista de la biblioteca de personajes
# (cualquier perfil). Su aspecto viene de una imagen de referencia: el texto solo describe acción, ropa y escenario.
PROTAGONIST_BLOCK_TEMPLATE = """

=== PROTAGONISTA FIJO DEL VIDEO ===
Este video lo protagoniza un personaje recurrente que YA EXISTE; su aspecto sale de una imagen de referencia, no de tu texto.
- Nombre: {protagonist_name}
- Descripción (solo para que entiendas quién es; NO la copies en los prompts): {protagonist_description}
REGLAS DEL PROTAGONISTA:
A. Es la GUÍA del espectador, pero solo se ve en las ESCENAS CLAVE: aproximadamente el 20 % de las escenas (nunca más de 24 en todo el video ni más del 25 %), repartidas a lo largo del video: el gancho, los giros, las decisiones importantes y el desenlace. En cada una de esas escenas 'characters_in_scene' incluye "{protagonist_id}" (exactamente ese ID) y el prompt lo llama "the main character". El 80 % restante son escenas de B-roll explicativo SIN personas: ilustran con objetos, lugares, mapas, esquemas, paisajes, fenómenos o detalles lo que dice la narración (el planeta visto desde la órbita, un globo terráqueo en corte, una calle vacía, un reloj, un mapa), con 'characters_in_scene' vacío. Evita dos escenas seguidas con el protagonista salvo en un clímax.
B. NO lo definas en 'characters' (se añade solo). Ahí van únicamente los personajes secundarios, si los hay, con su descripción corta en inglés (máximo 30 palabras).
C. En 'visual_prompt_en' llámalo "the main character" UNA sola vez por prompt: nunca por su nombre ({protagonist_name}), nunca los dos juntos ("the main character, {protagonist_name},") ni con una descripción de su cara, edad, piel, cabello o cuerpo. Describe lo que HACE (acción y postura), su expresión, la ropa o el equipo que lleva en esa escena si cambia y el escenario. Los personajes secundarios sí se nombran con un sustantivo de rol corto ("the neighbor").
D. En 'narration_es' nómbralo por su nombre ({protagonist_name}); no lo llames "el protagonista".
E. Varía el escenario, la acción y el tipo de plano de una escena a otra: dos escenas seguidas no repiten lugar ni encuadre.
F. En cada escena SIN el protagonista (el B-roll, la mayoría), 'visual_prompt_en' dice explícitamente "no people, no characters" (si no se prohíbe, el generador de imágenes inventa personas distintas en cada escena y rompe la idea de un solo personaje) y no usa "the main character". Esa frase no se contradice dentro del mismo prompt: en una escena "no people" no hay tribus, multitudes, habitantes ni actividad humana, ni manos ni siluetas. Si la narración habla de gente (la humanidad, todos, una tribu), ilustra con el lugar, los objetos o sus huellas (casas vacías, una fogata apagada); solo si hace falta que se vean otras personas, descríbelas como un personaje secundario definido en 'characters' (nombrado con su sustantivo de rol y con su ID en 'characters_in_scene'); nunca como personas inventadas en primer plano.{framing_rule}"""

# Regla G del bloque anterior según el formato de video (clave = config.FORMATS id). Sin entrada para un formato, se omite.
PROTAGONIST_FRAMING_RULES: dict[str, str] = {
    "horizontal": (
        "\nG. Encuadre horizontal (16:9): prioriza planos generales y medios con el sujeto en el tercio izquierdo o "
        "derecho y el escenario amplio detrás; mantén el quinto inferior del cuadro sin detalles importantes "
        "(ahí van los subtítulos)."
    ),
    "vertical": (
        "\nG. Encuadre vertical (9:16): sujeto centrado, planos medios y primeros planos; deja libres las zonas "
        "superior e inferior del cuadro (ahí van el gancho y los subtítulos)."
    ),
}


SYSTEM_PROMPT_TEMPLATE = """Eres un guionista experto en retención para {platform_label}.
Tu objetivo es que el espectador vea el video completo, lo comente y lo comparta.
Produce un guion completo en JSON ESTRICTO con esta estructura:

JSON FORMAT TEMPLATE:
{schema_json}

=== REGLAS DEL PERFIL '{profile_upper}' ===
{profile_instructions}

=== REGLAS DE NARRACIÓN (OBLIGATORIAS) ===
1. Escenas: {scenes_rule}.
2. Presupuesto de palabras: cada 'narration_es' tiene entre {words_min} y {words_max} palabras (objetivo: {words_target}),
   para que cada escena dure ~{scene_seconds} segundos con la voz del perfil. La escena 1 tiene como máximo {words_target} palabras.{strict_rule}
3. Gancho: la primera frase de la escena 1 abre un bucle de curiosidad o hace una afirmación impactante.
   PROHIBIDO: saludos, "en este video", "hoy te voy a contar", presentaciones o anunciar lo que viene.
   La resolución llega más adelante, NUNCA en la escena 1.{hook_rule}
4. Texto para voz sintética: CERO emojis, CERO paréntesis, CERO acotaciones o notas de audio. Sin abreviaturas
   (escribe "kilómetros por hora", no "km/h"). Los números pueden ir en cifras.
5. 'highlight_words': de 1 a 3 palabras copiadas EXACTAMENTE de 'narration_es' de esa escena (las de más impacto o cifras).{cta_rule}

=== REGLAS VISUALES (OBLIGATORIAS) ===
6. 'visual_prompt_en': en inglés, entre 25 y 45 palabras, UNA sola imagen fija por escena. Describe el sujeto concreto,
   la acción o el momento exacto, el escenario y el tipo de plano o ángulo de cámara.
7. No repitas el estilo artístico en 'visual_prompt_en' (render, iluminación general, paleta): se añade automáticamente.
8. Nunca pidas texto, letreros, pantallas con palabras, logotipos, marcas comerciales ni personas famosas reales.
9. Varía los planos a lo largo del video (primer plano, plano general, cenital, contrapicado) para dar ritmo visual,
   y que la imagen ilustre literalmente lo que dice la narración de esa escena.
10. 'camera_effect' entre {camera_effects}: usa zoom_in_fast para revelaciones y detalles,
    zoom_in_slow para tensión, zoom_out para mostrar contexto o escala, pan_left/pan_right para recorridos o secuencias.

GUÍA DEL ESTILO VISUAL '{style_label}':
{style_guidance}{protagonist_block}

=== REGLAS DE PUBLICACIÓN ===
11. 'title': máximo 60 caracteres, despierta curiosidad sin mentir.
12. 'hook_text': de 2 a 6 palabras en español para mostrar en pantalla durante el gancho. COMPLEMENTA la narración
    de la escena 1 (intriga, contraste o un dato extra); no la repite.
13. 'post_description': 1 o 2 frases para el pie del post; la última es una pregunta que invite a comentar.
14. 'hashtags': de 5 a 8 hashtags específicos del tema, SIN '#', en minúsculas, sin espacios ni acentos.

Responde ÚNICAMENTE con el objeto JSON válido, sin texto antes ni después y sin bloques de código.
"""

# Reglas extra de los perfiles de short vertical (strict_scenes): se verifican por código y el guion se rechaza si no se cumplen
STRICT_RULE_TEMPLATE = """
   REGLA DURA (se verifica por código; si no se cumple, el guion se rechaza): ninguna escena puede tener menos de {words_min} ni
   más de {words_max} palabras (cuenta TODAS las palabras, también la última escena con su frase de cierre){total_rule}."""
STRICT_TOTAL_RULE_TEMPLATE = (
    " y el guion completo suma entre {total_min} y {total_max} palabras, para que el audio total dure entre "
    "{sec_min:g} y {sec_max:g} segundos"
)
STRICT_HOOK_RULE = (
    "\n   La escena 1 se lee en menos de 3 segundos: una pregunta o afirmación de choque directa, sin saludo ni contexto."
)

CTA_RULE_TEMPLATE = '\n   La narración de la ÚLTIMA escena termina exactamente con la frase: "{cta}".'


# --------------------------------------------------------------------------- #
# Storyboard de un guion importado (ver storyboard_engine.py): la narración ya está escrita por su autor y el LLM
# solo decide lo que se VE en cada escena y redacta los textos de publicación.
# --------------------------------------------------------------------------- #

# Bloque 'PROTAGONISTA FIJO' del storyboard (equivalente a PROTAGONIST_BLOCK_TEMPLATE, pero aquí la narración ya existe y
# no menciona al protagonista en cada escena: el LLM lo pone como GUÍA en casi todas, no solo donde lo nombra el texto).
# {framing_rule} = PROTAGONIST_FRAMING_RULES.
STORYBOARD_PROTAGONIST_BLOCK_TEMPLATE = """

=== PROTAGONISTA FIJO DEL VIDEO ===
Este video lo protagoniza un personaje recurrente que YA EXISTE; su aspecto sale de una imagen de referencia, no de tu texto.
- Nombre: {protagonist_name}
- Descripción (solo para que entiendas quién es; NO la copies en los prompts): {protagonist_description}
El formato es "un mismo personaje recorre distintos escenarios": {protagonist_name} es la GUÍA del espectador y se ve en las escenas CLAVE, no en todas; el resto es B-roll explicativo.
REGLAS DEL PROTAGONISTA:
A. Aparece solo en aproximadamente el 20 % de las escenas que recibas en esta llamada (nunca más del 25 % ni más de 24 en total): las escenas clave (el arranque, los giros, las decisiones, el desenlace), repartidas a lo largo del tramo y rara vez dos seguidas. En cada una, la imagen lo muestra en el cuadro mirando, señalando, tocando o reaccionando a lo que dice la narración, 'characters_in_scene' incluye "{protagonist_id}" (exactamente ese ID) y el prompt lo llama "the main character". NO decidas por si la narración lo nombra: elige las escenas por su peso en la historia. El 80 % restante son escenas de B-roll explicativo SIN personas: ilustran con objetos, lugares, mapas, esquemas, paisajes, fenómenos o detalles lo que dice la narración (esto manda sobre la regla 2: nada de mostrar gente), con 'characters_in_scene' vacío.
B. NO lo definas en 'characters' (se añade solo). Ahí van únicamente los personajes secundarios, si los hay, con su descripción corta en inglés (máximo 30 palabras).
C. En 'visual_prompt_en' llámalo "the main character" UNA sola vez por prompt: nunca por su nombre ({protagonist_name}), nunca los dos juntos ("the main character, {protagonist_name},") ni con una descripción de su cara, edad, piel, cabello o cuerpo. Describe lo que HACE (acción y postura), su expresión, la ropa o el equipo que lleva en esa escena si cambia y el escenario. Los personajes secundarios sí se nombran con un sustantivo de rol corto ("the neighbor").
D. La narración no se toca: tú solo describes imágenes.
E. Varía el escenario, la acción y el tipo de plano de una escena a otra: dos escenas seguidas no repiten lugar ni encuadre salvo que la narración lo exija.
F. En cada escena SIN el protagonista (el B-roll, la mayoría), 'visual_prompt_en' dice explícitamente "no people, no characters" (si no se prohíbe, el generador de imágenes inventa personas distintas en cada escena y rompe la idea de un solo personaje) y no usa "the main character". Esa frase no se contradice dentro del mismo prompt: en una escena "no people" no hay tribus, multitudes, habitantes ni actividad humana, ni manos ni siluetas. Si la narración habla de gente (la humanidad, todos, una tribu), ilustra con el lugar, los objetos o sus huellas (casas vacías, una fogata apagada); solo si hace falta que se vean otras personas, descríbelas como un personaje secundario definido en 'characters' (nombrado con su sustantivo de rol y con su ID en 'characters_in_scene'); nunca como personas inventadas en primer plano.{framing_rule}"""

# Regla de personajes del storyboard según haya protagonista fijo o no ({{}} = llaves literales del ejemplo JSON).
STORYBOARD_CHARACTERS_RULE_PROTAGONIST = (
    "Solo los personajes SECUNDARIOS que aparecen en varias escenas (una pareja, un rival, una vecina...) van en "
    "'characters': un id corto en minúsculas y sin espacios (char_1, char_2...) y su descripción física en inglés "
    "(máximo 30 palabras: edad exacta, tono de piel, rasgos faciales, peinado y una prenda distintiva). Si no hay "
    "ninguno, 'characters' queda como {{}}. No inventes personajes que el texto no mencione."
)
STORYBOARD_CHARACTERS_RULE_NO_PROTAGONIST = (
    "Los personajes que aparecen en varias escenas (incluido quien protagonice la historia) van en 'characters': un "
    "id corto en minúsculas y sin espacios (char_1, char_2...) y su descripción física en inglés (máximo 30 palabras: "
    "edad exacta, tono de piel, rasgos faciales, peinado y una prenda distintiva que se repita). Si el texto no tiene "
    "personajes recurrentes, 'characters' queda como {{}}. No inventes personajes que el texto no mencione."
)

# Qué se le pide al LLM en cada tipo de llamada del storyboard (full = todo en una; meta + scenes = texto largo por bloques).
STORYBOARD_TASKS: dict[str, str] = {
    "full": (
        "Recibirás el texto completo del video (solo como contexto) y la lista numerada de sus escenas. Devuelve los "
        "metadatos del video y UNA entrada por escena."
    ),
    "meta": (
        "Recibirás el texto completo del video (solo como contexto). En esta llamada NO devuelvas escenas: solo los "
        "textos de publicación y los personajes secundarios que reaparecen."
    ),
    "scenes": (
        "Recibirás el texto completo del video (solo como contexto), los personajes ya definidos y un tramo de la "
        "lista de escenas. Devuelve UNA entrada por cada escena del tramo; no devuelvas metadatos ni personajes."
    ),
}

STORYBOARD_SYSTEM_PROMPT_TEMPLATE = """Eres un director de arte y storyboarder para {platform_label}.
La narración de este video YA ESTÁ ESCRITA por su autor y es INTOCABLE: tú NO la escribes, NO la resumes, NO la corriges y NO la devuelves.
Tu trabajo es decidir qué se VE en pantalla mientras se escucha cada fragmento y redactar los textos de publicación.
{task}

JSON FORMAT TEMPLATE:
{schema_json}

=== REGLAS DE LAS ESCENAS (OBLIGATORIAS) ===
1. Una entrada por cada escena recibida, con exactamente el 'scene_id' recibido y en orden: ni una más, ni una menos. Sin la narración.
2. 'visual_prompt_en': en inglés, entre 25 y 45 palabras, UNA sola imagen fija por escena. La imagen ilustra literalmente lo que
   dice la narración de ESA escena: sujeto concreto, acción o momento exacto, escenario, hora del día y tipo de plano o ángulo de cámara.
3. No repitas el estilo artístico en 'visual_prompt_en' (render, iluminación general, paleta): se añade automáticamente.
4. Nunca pidas texto, letreros, pantallas con palabras, logotipos, marcas comerciales ni personas famosas reales.
5. Continuidad: la historia es una sola. Mantén coherentes el lugar, la hora, la ropa y los objetos entre escenas vecinas, pero
   varía el tipo de plano y el encuadre a lo largo del video (primer plano, plano general, cenital, contrapicado) para dar ritmo visual.
6. 'camera_effect' entre {camera_effects}: usa zoom_in_fast para revelaciones y detalles,
   zoom_in_slow para tensión, zoom_out para mostrar contexto o escala, pan_left/pan_right para recorridos o secuencias.
7. 'highlight_words': de 1 a 3 palabras copiadas EXACTAMENTE de la narración de esa escena (las de más impacto o las cifras).
8. 'characters_in_scene': los IDs de los personajes que SE VEN en la imagen de esa escena (lista vacía si no sale nadie).
9. {characters_rule}

GUÍA DEL ESTILO VISUAL '{style_label}':
{style_guidance}{protagonist_block}

=== REGLAS DE PUBLICACIÓN ===
10. 'title': máximo 60 caracteres, despierta curiosidad sin mentir.
11. 'hook_text': de 2 a 6 palabras en español para mostrar en pantalla durante los primeros segundos. COMPLEMENTA la narración
    de la escena 1 (intriga, contraste o un dato extra); no la repite.
12. 'post_description': 1 o 2 frases para el pie del post; la última es una pregunta que invite a comentar.
13. 'hashtags': de 5 a 8 hashtags específicos del tema, SIN '#', en minúsculas, sin espacios ni acentos.

Responde ÚNICAMENTE con el objeto JSON válido, sin texto antes ni después y sin bloques de código.
"""

# Mensajes de usuario del storyboard (el texto completo va como contexto; las escenas, con su narración exacta)
STORYBOARD_USER_FULL_TEMPLATE = """TEXTO COMPLETO DEL VIDEO (solo contexto; no lo reescribas ni lo devuelvas):
\"\"\"
{full_text}
\"\"\"

ESCENAS (scene_id: narración definitiva):
{scene_lines}
{protagonist_quota}
Devuelve el JSON con los metadatos y las {n_scenes} escenas (scene_id {first_id} a {last_id})."""

STORYBOARD_USER_META_TEMPLATE = """TEXTO COMPLETO DEL VIDEO (solo contexto; no lo reescribas ni lo devuelvas):
\"\"\"
{full_text}
\"\"\"

Devuelve el JSON con el título, el texto gancho, la descripción, los hashtags y los personajes secundarios recurrentes. Sin escenas."""

STORYBOARD_USER_SCENES_TEMPLATE = """TEXTO COMPLETO DEL VIDEO (solo contexto; no lo reescribas ni lo devuelvas):
\"\"\"
{full_text}
\"\"\"

PERSONAJES YA DEFINIDOS (usa estos IDs en 'characters_in_scene'; no definas otros):
{characters_json}
{previous_block}
ESCENAS DE ESTE TRAMO (scene_id: narración definitiva):
{scene_lines}
{protagonist_quota}
Devuelve el JSON con las {n_scenes} escenas de este tramo (scene_id {first_id} a {last_id}) y nada más."""

# Recordatorio con números al final del mensaje de usuario (el modelo barato cumple mejor una cuota concreta que un "20 %").
# {target_with} = escenas clave con el protagonista (~20 %); {max_with} = tope (25 %, máximo 24 en todo el video).
STORYBOARD_PROTAGONIST_QUOTA_TEMPLATE = """
CUOTA DEL PROTAGONISTA: {protagonist_name} ("{protagonist_id}") se ve solo en las escenas clave: unas {target_with} de estas {n_scenes} escenas y NUNCA más de {max_with}, con 'characters_in_scene' = ["{protagonist_id}"] y "the main character" en el prompt. Las demás son B-roll explicativo sin personas ("no people, no characters", 'characters_in_scene' vacío).
"""

STORYBOARD_PREVIOUS_BLOCK_TEMPLATE = """
ÚLTIMAS ESCENAS YA RESUELTAS (solo para dar continuidad y variar el plano; no las repitas):
{previous_lines}
"""
