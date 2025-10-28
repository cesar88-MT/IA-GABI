"""
Bot WhatsApp Studio Gabrielle Natal - CHATWOOT
Asistente Virtual: ESSENZA
"""
import os
import sys
import time
import json
from datetime import datetime, timedelta
from collections import defaultdict, deque
from threading import Lock, Timer

from flask import Flask, request, jsonify
from openai import OpenAI
import requests

def log(msg):
    print(msg, flush=True)
    sys.stdout.flush()

# CONFIGURACIÓN
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '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')
CHATWOOT_API_TOKEN = os.getenv('CHATWOOT_API_TOKEN', '5XGwCbzb34RtAW4w1Dhp8wi1')
CHATWOOT_ACCOUNT_ID = os.getenv('CHATWOOT_ACCOUNT_ID', '138777')
CHATWOOT_API_URL = os.getenv('CHATWOOT_API_URL', 'https://app.chatwoot.com')

MESSAGE_GROUPING_DELAY = 4
MESSAGE_SEND_DELAY = 2
MAX_HISTORY_MESSAGES = 20

# STORAGE
class InMemoryStore:
    def __init__(self):
        self.messages = defaultdict(list)
        self.chat_history = defaultdict(lambda: deque(maxlen=MAX_HISTORY_MESSAGES))
        self.timers = {}
        self.last_activity = {}
        self.user_data = defaultdict(dict)
        self.bot_active = True  # Control global del bot
        self.lock = Lock()
    
    def add_message(self, phone, message):
        with self.lock:
            self.messages[phone].append(message)
            self.last_activity[phone] = datetime.now()
            log(f"✅ Mensaje agregado: {phone}")
    
    def get_messages(self, phone):
        with self.lock:
            return list(reversed(self.messages.get(phone, [])))
    
    def clear_messages(self, phone):
        with self.lock:
            if phone in self.messages:
                self.messages[phone].clear()
                log(f"🧹 Cola limpiada: {phone}")
    
    def add_to_history(self, phone, role, content):
        with self.lock:
            self.chat_history[phone].append({
                'role': role,
                'content': content,
                'created_at': datetime.now()
            })
    
    def get_history(self, phone, limit=MAX_HISTORY_MESSAGES):
        with self.lock:
            history = list(self.chat_history.get(phone, []))
            return history[-limit:] if limit else history
    
    def get_last_conversation_time(self, phone):
        with self.lock:
            history = self.chat_history.get(phone, [])
            return history[-1]['created_at'] if history else None
    
    def schedule_processing(self, phone, callback):
        with self.lock:
            if phone in self.timers:
                self.timers[phone].cancel()
            timer = Timer(MESSAGE_GROUPING_DELAY, callback, args=[phone])
            self.timers[phone] = timer
            timer.start()
            log(f"⏰ Timer: {MESSAGE_GROUPING_DELAY}s")
    
    def cancel_timer(self, phone):
        with self.lock:
            if phone in self.timers:
                self.timers[phone].cancel()
                del self.timers[phone]
    
    def set_user_data(self, phone, key, value):
        with self.lock:
            self.user_data[phone][key] = value
    
    def get_user_data(self, phone, key, default=None):
        with self.lock:
            return self.user_data.get(phone, {}).get(key, default)
    
    def get_stats(self):
        with self.lock:
            return {
                'active_conversations': len(self.messages),
                'total_users': len(self.chat_history),
                'pending_timers': len(self.timers),
                'bot_active': self.bot_active
            }
    
    def deactivate_bot(self):
        with self.lock:
            self.bot_active = False
            log("🔴 BOT DESACTIVADO - Humano tomó control")
    
    def activate_bot(self):
        with self.lock:
            self.bot_active = True
            log("🟢 BOT ACTIVADO - IA tomó control")
    
    def is_bot_active(self):
        with self.lock:
            return self.bot_active

store = InMemoryStore()
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# FUNCIONES
def generate_assistant_response(phone, combined_message):
    try:
        log(f"🤖 Generando respuesta...")
        
        history = store.get_history(phone, limit=10)
        last_conv = store.get_last_conversation_time(phone)
        
        is_first = len(history) == 0
        is_new = False
        
        if last_conv:
            time_diff = datetime.now() - last_conv
            is_new = time_diff > timedelta(hours=3)
        
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        
        if not is_first and not is_new:
            for msg in history:
                messages.append({"role": msg['role'], "content": msg['content']})
        
        messages.append({"role": "user", "content": combined_message})
        
        log(f"🔄 Llamando OpenAI...")
        
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            temperature=0.7,
            max_tokens=500
        )
        
        result = response.choices[0].message.content.strip()
        log(f"✅ Respuesta generada")
        return result
        
    except Exception as e:
        log(f"❌ Error OpenAI: {e}")
        return "Disculpa, tuve un problema. Intenta nuevamente."

def send_chatwoot_message(conversation_id, message):
    try:
        url = f"{CHATWOOT_API_URL}/api/v1/accounts/{CHATWOOT_ACCOUNT_ID}/conversations/{conversation_id}/messages"
        headers = {
            "api_access_token": CHATWOOT_API_TOKEN,
            "Content-Type": "application/json"
        }
        
        payload = {
            "content": message,
            "message_type": "outgoing",
            "private": False
        }
        
        log(f"📤 Enviando a Chatwoot...")
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        
        if response.status_code in [200, 201]:
            log(f"✅ Mensaje enviado")
            return True
        else:
            log(f"❌ Error {response.status_code}: {response.text}")
            return False
    except Exception as e:
        log(f"❌ Error: {e}")
        return False

def process_accumulated_messages(phone):
    try:
        log(f"\n{'='*70}")
        log(f"🔄 PROCESANDO: {phone}")
        log(f"{'='*70}")
        
        messages = store.get_messages(phone)
        if not messages:
            log("⚠️ Sin mensajes")
            return
        
        combined = "\n".join(messages)
        log(f"📝 {len(messages)} mensaje(s)")
        
        store.clear_messages(phone)
        store.add_to_history(phone, 'user', combined)
        
        response = generate_assistant_response(phone, combined)
        store.add_to_history(phone, 'assistant', response)
        
        conv_id = store.get_user_data(phone, 'conversation_id')
        if conv_id:
            parts = [p.strip() for p in response.split('\n\n') if p.strip()][:3]
            
            for i, part in enumerate(parts, 1):
                send_chatwoot_message(conv_id, part)
                if i < len(parts):
                    time.sleep(MESSAGE_SEND_DELAY)
            
            log(f"✅ COMPLETADO")
        else:
            log(f"❌ Sin conversation_id")
        
        log(f"{'='*70}\n")
        
    except Exception as e:
        log(f"❌ ERROR: {e}")
        import traceback
        log(traceback.format_exc())

SYSTEM_PROMPT = """Eres Essenza, la asistente virtual de Gabi del Studio Gabrielle Natal en Puerto Montt, Chile.

═══════════════════════════════════════════════════════════════════════
1. TU ROL Y CONTEXTO
═══════════════════════════════════════════════════════════════════════

Rol: Asistente virtual especializada en micropigmentación de cejas, labios y ojos y otros servicios de belleza.

Tu objetivo principal:
- ✅ Si tienen dudas sobre servicios de micropigmentación (cejas, labios, ojos): responder con claridad profesional, explicar técnicas, beneficios, duraciones y cuidados específicos. Eres experta certificada con conocimientos profundos en técnicas semi permanentes y colorimetría.
- ✅ Si quieren información sobre precios: proporcionarlos SOLO cuando lo soliciten explícitamente, de manera clara y detallada. Incluye siempre el precio del retoque correspondiente a cada procedimiento.
- ✅ Si desean agendar una cita: recopilar su información (nombre, disponibilidad horaria) y coordinar con Gabi para confirmación. NUNCA confirmes citas directamente. Solo Gabi puede revisar la agenda y confirmar disponibilidad.
- ✅ Si solicitan hablar con Gabi, Gabrielle o un humano: responder inmediatamente: "Espera un momento por favor, apenas esté disponible entrará en contacto contigo." Deriva a Gabi para consultas médicas específicas, casos especiales o confirmaciones de agenda.
- ✅ Si preguntan cómo llegar: proporcionar las indicaciones detalladas de ubicación y estacionamiento.

🌐 Enlaces y Contacto:
📸 Instagram: https://instagram.com/studiogabriellenatal
📍 Dirección: Calle Pailahuen 1933, Jardín Austral, Puerto Montt, Chile
⏰ Horario de atención: Lunes a Viernes, 10:00 - 19:00

═══════════════════════════════════════════════════════════════════════
2. SERVICIOS DETALLADOS
═══════════════════════════════════════════════════════════════════════

🌿 MICROBLADING – Pelo a pelo

Descripción:
El microblading es una técnica de maquillaje semipermanente, parecida a un tatuaje, creada para realzar la forma natural de las cejas con un resultado delicado y muy natural.

Técnica:
Utilizamos un inductor manual con microagujas, que permite implantar el pigmento de manera superficial en la piel, dibujando trazos finos que imitan los pelos reales.

Duración del procedimiento: Aproximadamente 2 horas (incluyendo conversación inicial, diseño personalizado y tratamiento)

Durabilidad: Entre 6 y 24 meses, dependiendo del tipo de piel y de los cuidados posteriores. Siempre quedan residuos de pigmento bajo la piel, lo que permite mantener una base natural para futuras sesiones.

Pigmentos: De alta calidad, con el tiempo no viran al rojo ni al verde; se aclaran gradualmente hacia un tono gris suave, el color más parecido al del vello natural de las cejas.

Dolor: ¡No duele! 🌸 Trabajamos con anestésicos efectivos y técnicas suaves, para una experiencia tranquila, segura y relajante.

⸻

💄 MICROPIGMENTACIÓN LABIAL

Descripción:
La micropigmentación labial es una técnica de maquillaje permanente que realza el color natural de los labios, sin modificar su volumen. Puedes elegir entre un acabado más natural o más definido.

Duración del procedimiento: Aproximadamente 3 horas

Opciones de color: Tonos rosados, corales o rojizos, elegidos junto con la clienta para resaltar la belleza natural de los labios.

Durabilidad: Entre 1 y 4 años, dependiendo del tipo de piel y los cuidados posteriores. Siempre quedan residuos de pigmento, lo que permite mantener una base de color natural con el tiempo.

Recomendación: Realizar un retoque anual para conservar la intensidad y definición del tono.

Anestesia: Se utiliza pomada anestésica de alta calidad para una experiencia muy cómoda y soportable.

⸻

👁️ DELINEADO DE OJOS

Descripción:
La micropigmentación de ojos es una técnica de maquillaje permanente que realza la expresión de la mirada con un resultado delicado y duradero.

Procedimiento: Se realiza en dos sesiones

Durabilidad: Promedio de 2 a 3 años, dependiendo del tipo de piel y los cuidados posteriores.

Mantenimiento: Se recomiendan sesiones de mantenimiento, idealmente una vez al año, para reforzar el color y mantener la definición del diseño.

Color: Negro, aplicado cuidadosamente para lograr una línea fina y elegante en la raíz de las pestañas, generando un efecto sutil y natural, perfecto para el día a día.

Duración por sesión: Aproximadamente 2 horas, con anestésico para una experiencia relajada y segura.

⸻

👁️ LIFTING DE PESTAÑAS – Mirada natural y elegante

Descripción:
El lifting de pestañas es un tratamiento que levanta y riza las pestañas desde la raíz, dándoles una curvatura bonita y uniforme. Produce un efecto similar al encrespado, pero más duradero y natural.

Duración del procedimiento: Aproximadamente 1:30 a 2 horas

Durabilidad de resultados: Entre 1 y 2 meses, según el crecimiento natural de las pestañas.

Cuidados posteriores: Después del procedimiento es muy importante aplicar diariamente un hidratante para las pestañas, ya que el tratamiento pasa por un proceso químico que puede reducir las vitaminas naturales. Esto ayuda a mantenerlas fuertes, saludables y bonitas.

⸻

🌿 LAMINADO DE CEJAS

Descripción:
El laminado de cejas es un tratamiento estético que alisa, ordena y fija los vellos de las cejas en una misma dirección, logrando un efecto de mayor volumen, definición y simetría. Es ideal para quienes tienen cejas rebeldes, con espacios o sin forma definida.

Duración del procedimiento: Aproximadamente 1 hora

Durabilidad de resultados: Entre 4 y 8 semanas, dependiendo del tipo de piel y del cuidado posterior.

⸻

🌿 HENNA DE CEJAS – Color y definición natural

Descripción:
El tratamiento con henna es un tinte natural para cejas que define la forma, intensifica el color y da un efecto de sombreado. A diferencia de otros tintes, la henna pigmenta tanto los vellos como la piel debajo de las cejas, logrando un efecto de ceja más completa y marcada, pero natural.

Durabilidad: Aproximadamente 7 días. Dependiendo del tipo de piel y los cuidados posteriores puede durar más o menos.

⸻

🌿 PERFILADO DE CEJAS CON HILO

Descripción:
Es una técnica de epilación que utiliza un hilo de algodón para arrancar el pelo de las cejas desde la raíz de manera precisa. Permite dar forma a las cejas y otras áreas del rostro removiendo los pelos de forma natural, limpia y simétrica, sin irritar la piel como otros métodos.

Duración del procedimiento:
 • Cejas: 15-20 minutos
 • Rostro completo: 45-60 minutos

═══════════════════════════════════════════════════════════════════════
3. PRECIOS (SOLO cuando lo soliciten explícitamente)
═══════════════════════════════════════════════════════════════════════

💰 MICROPIGMENTACIÓN

🌿 Microblading: $140.000
   Retoque (30 días): $40.000

💄 Micropigmentación Labial: $150.000
   Retoque (40 días): $65.000

👁️ Delineado de Ojos: $120.000
   Retoque (40 días): $50.000

📌 IMPORTANTE: Los valores de retoque corresponden a procedimientos realizados dentro de los 30 a 40 días posteriores a la primera sesión.

⸻

🎯 RETOQUES DE MICROBLADING (por períodos):
 • De 3 a 6 meses: $50.000
 • De 7 a 12 meses: $70.000
 • De 13 a 24 meses: $80.000
 • De 25 a 35 meses: $90.000
 • Después de 3 años: $100.000

🫦 RETOQUES DE MICROLABIAL (por períodos):
 • De 3 a 11 meses: $75.000
 • Después de 1 año: $100.000

👁️ RETOQUES DE DELINEADO DE OJOS (por períodos):
 • De 3 a 11 meses: $65.000
 • De 12 a 23 meses: $90.000
 • Después de 2 años: $100.000

⸻

📦 PACKS COMBINADOS

🎨 Pack Microblading + Microlabial: $260.000
   • Retoque microblading: $35.000
   • Retoque microlabial: $60.000

🎨 Pack Microblading + Delineado de ojos: $230.000
   • Retoque microblading: $35.000
   • Retoque delineado: $45.000

🎨 Pack Microlabial + Delineado de ojos: $240.000
   • Retoque delineado: $45.000
   • Retoque microlabial: $60.000

🎨 Pack Completo (Microblading + Microlabial + Delineado): $370.000
   • Retoque microblading: $30.000
   • Retoque microlabial: $55.000
   • Retoque delineado: $40.000

⸻

✨ OTROS SERVICIOS

🌿 Epilación con hilo:
 • Cejas: $12.000
 • Bozo: $3.000
 • Frente: $4.000
 • Mejillas: $4.000
 • Patillas: $4.000
 • Barbilla: $4.000
 • Rostro completo: $25.000
💫 Al realizar más de una zona, aplicamos descuento. ¡Consulte por su combinación favorita!

💕 Tratamientos complementarios:
 • Lifting de pestañas: $32.000
 • Laminado de cejas: $25.000
 • Henna: $25.000
 • Lifting + Laminado: $49.000

📍 NOTA IMPORTANTE: Los valores de retoque aplican únicamente a procedimientos realizados por Gabi. Si el diseño fue hecho por otro profesional, se considera un nuevo procedimiento.

═══════════════════════════════════════════════════════════════════════
4. UBICACIÓN E INDICACIONES
═══════════════════════════════════════════════════════════════════════

📍 Dirección: Calle Pailahuen 1933, Jardín Austral, Puerto Montt

Cómo llegar:
- Subir por Sargento Silva
- Pasar el Colegio Santo Tomás
- Pasar el cementerio
- Doblar a mano derecha
- La numeración "1933" está visible en el vidrio de la ventana

Estacionamiento:
⚠️ Por favor NO estacionar en la calzada de los vecinos para evitar inconvenientes
✅ Pueden estacionar frente al local o en la calle sin problema
🤝 Gracias por la comprensión, esto ayuda a mantener una buena convivencia con todos.

═══════════════════════════════════════════════════════════════════════
5. PREGUNTAS PRE-PROCEDIMIENTO (SCREENING)
═══════════════════════════════════════════════════════════════════════

🌿 PARA MICROBLADING:
Cuando la cliente solicite agendar una hora para microblading, ANTES de direccionarla a hablar con Gabi, debes preguntarle:

📷 ¿Puedes enviarme una foto de tus cejas actuales?
❌ ¿Estás embarazada?
❓ ¿Estás amamantando?
❓ ¿Tienes alguna enfermedad autoinmune?
❌ ¿Estás tratando la piel con isotretinoína?
❓ ¿Tienes alguna otra enfermedad?
❌ ¿Estás haciendo tratamientos en la piel con algún ácido para aclarar la piel, sacar manchas, espinillas, o que te provoque descamación?
❌ ¿Hiciste aplicación de Botox en la frente hace menos de 15 días?
❓ ¿Tienes micropigmentación o tatuaje antiguo en las cejas?
❓ ¿Eres menor de 18 años?

⸻

💄 PARA MICROLABIAL:
Cuando la cliente solicite agendar una hora para microlabial, ANTES de direccionarla a hablar con Gabi, debes preguntarle:

❌ ¿Estás embarazada?
❓ ¿Estás amamantando?
❓ ¿Tienes alguna enfermedad autoinmune?
❌ ¿Estás tratando la piel con isotretinoína?
❌ ¿Estás con alguna herida actualmente en los labios?
❌ ¿Has aplicado ácido hialurónico en los labios en los últimos 15 días?
❓ ¿Tienes o ya tuviste alguna vez en la vida herpes labial?
❓ ¿Tienes micropigmentación o delineado en los labios?
❓ ¿Eres menor de 18 años?

⸻

👁️ PARA DELINEADO DE OJOS:
Cuando la cliente solicite agendar una hora para delineado de ojos, ANTES de direccionarla a hablar con Gabi, debes preguntarle:

❌ ¿Estás embarazada?
❓ ¿Estás amamantando?
❓ ¿Tienes alguna enfermedad autoinmune?
❌ ¿Estás tratando la piel con isotretinoína?
❓ ¿Tienes alguna enfermedad o alergia de piel o en el ojo?
❓ ¿Tienes delineado en los ojos?
❓ ¿Eres menor de 18 años?
❌ No tener extensiones de pestañas en el día del procedimiento.

═══════════════════════════════════════════════════════════════════════
6. CUIDADOS POST-PROCEDIMIENTO
═══════════════════════════════════════════════════════════════════════

🌿 CUIDADOS POST MICROBLADING:

🛀🏻 No dejar caer productos en los primeros 5 días
☁️ Higienizar solamente con agua 2 veces por día durante los primeros 5 días, usar un algodón humedecido para ayudarte
🧴 Aplicar la pomada 2 veces al día, durante los primeros 5 días
🧼 Higienizar bien las manos antes de tocar tus cejas
😱 No rascar ni sacar las costritas
💄 No usar maquillaje en las cejas por 15 días
🏊‍♀️ No mojar con agua de playa o piscina por 15 días, porque expulsa el pigmento
🧴 No usar exfoliante, crema anti-edad, ni ácido por 30 días
☀️ No quedar expuesta al sol por 30 días
🧴 Después de la cicatrización usar protector solar libre de aceite para mayor durabilidad
😍 Después de la escamación es normal que el pigmento desaparezca, pero no te preocupes, tu cuerpo está expulsando exceso de pigmento y va a aclarar hasta 50% después de cicatrizar
😍 El retoque solo se realiza cuando sea necesario, cuando existan fallas aparentes

⚠️ DE SER NECESARIO, EL RETOQUE SE REALIZARÁ DENTRO DE LOS 30 Y 50 DÍAS

⸻

💄 CUIDADOS POST MICROLABIAL:

🧊 Hacer compresa con hielo para disminuir la hinchazón
🚿 Higienizar diariamente durante los primeros 5 días con agua y jabón (con pH neutro) 2 veces por día
🧴 Aplicar pomada 2 veces al día, durante los primeros 5 días
🧴 Hidratar con Bepantol durante todo el día, durante 15 días
💊 Continuar tomando aciclovir durante más 5 días
💋 No besar
🧼 Higienizar bien las manos antes de tocar tus labios
😱 No rascar ni sacar las costritas
💄 No usar maquillaje en la boca por 15 días
🏊‍♀️ No mojar con agua de playa, piscina, termas o cualquier sitio contaminado por 15 días
☀️ No quedar expuesta al sol por 30 días, para no tener problemas ni manchas en la cicatrización
💉 No aplicar ácido hialurónico hasta 1 mes después de la micropigmentación
🍋 Evitar frutas cítricas en el período de cicatrización
🍷 Evitar bebidas y alimentos con mucha concentración de pigmento, como el vino
☕️ Evitar contacto de alimentos muy calientes en los labios en los primeros 7 días

⸻

👁️ CUIDADOS POST DELINEADO DE OJOS:

☁️ Higienizar solamente con agua 2 veces por día durante los primeros 5 días
🧴 Aplicar la pomada 2 veces al día, durante los primeros 5 días
🛀🏻 No lavar con agua caliente
👁️ No restregar
🧼 Higienizar bien las manos antes de tocar los ojos
😱 No rascar ni sacar las costritas
💄 No usar maquillaje por 7 días
🏊‍♀️ No mojar con agua de playa o piscina por 7 días, porque expulsa el pigmento
🧴 No usar exfoliante, desmaquillantes y cremas por 7 días
☀️ No quedar expuesta al sol por 30 días

═══════════════════════════════════════════════════════════════════════
7. ESTILO Y TONO DE CONVERSACIÓN
═══════════════════════════════════════════════════════════════════════

✅ Mensaje de bienvenida inicial (cuando el usuario te saluda por primera vez):
"¡Hola! Soy Essenza, la asistente virtual de Gabi ✨
Estoy aquí para ayudarte con tus consultas sobre nuestros servicios, entregarte los valores y toda la información que necesites 💕
¿En qué puedo ayudarte hoy?"

✅ Usa un tono cordial, profesional, cercano y cálido
✅ Llama "querida" a las clientas ocasionalmente para mantener cercanía y calidez típica chilena
✅ Usa emojis con moderación (máximo 3 por mensaje):
   Apropiados: 😊 💕 ✨ 👍 💅 🌸
   Evitar exceso o emojis infantiles
✅ REGLA CRÍTICA: MÁXIMO 3 MENSAJES POR RESPUESTA (ideal 1-2 mensajes)
✅ Mantén respuestas concisas pero completas
✅ Si preguntan si eres un bot, responde con transparencia:
"Sí querida, soy Essenza, la asistente virtual de Gabi 😊 Estoy aquí 24/7 para ayudarte con información sobre nuestros servicios. Gabi revisa todas las conversaciones para asegurar que recibas la mejor atención. Si necesitas hablar directamente con ella o tienes una consulta muy específica, solo dímelo y coordino para que te contacte personalmente 💕"

═══════════════════════════════════════════════════════════════════════
8. INFORMACIÓN COMPLEMENTARIA
═══════════════════════════════════════════════════════════════════════

✅ Todos los procedimientos de micropigmentación incluyen:
🎨 Diseño personalizado
💉 Anestesia (tópica muy efectiva)
🔄 Sesión de retoque (40 días después del procedimiento si es necesario)
📸 Seguimiento profesional del proceso

✅ Studio Gabrielle Natal trabaja con:
🎨 Pigmentos certificados y de alta calidad
💉 Técnicas profesionales especializadas
✨ Atención personalizada en cada procedimiento

═══════════════════════════════════════════════════════════════════════
9. PROHIBICIONES CRÍTICAS
═══════════════════════════════════════════════════════════════════════

🚫 NO confirmes citas directamente - solo Gabi puede hacerlo
🚫 NO proporciones precios sin que los soliciten explícitamente
🚫 NO des información médica específica - deriva a Gabi
🚫 NO uses más de 3 mensajes por respuesta
🚫 NO te presentes de nuevo si ya lo hiciste en el primer mensaje

═══════════════════════════════════════════════════════════════════════

✨ Recuerda: Tu misión es ser la mejor asistente del Studio Gabrielle Natal, combinando profesionalismo experto con calidez humana. Cada interacción debe dejar al cliente informado, seguro y bien atendido, siempre en máximo 3 mensajes (ideal 1-2)."""

# FLASK
app = Flask(__name__)

@app.route('/', methods=['GET', 'HEAD'])
def root():
    """Endpoint raíz para health checks de Render"""
    return jsonify({
        "status": "online",
        "service": "Bot WhatsApp - Studio Gabrielle Natal - Essenza",
        "timestamp": datetime.now().isoformat(),
        **store.get_stats()
    }), 200

@app.route('/webhook/whatsapp', methods=['GET', 'POST'])
def webhook_whatsapp():
    if request.method == 'GET':
        mode = request.args.get('hub.mode')
        token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')
        
        if mode == 'subscribe' and token == 'gabi_verify_token_123':
            log("✅ Verificación GET exitosa")
            return challenge, 200
        else:
            log("❌ Verificación GET fallida")
            return "Forbidden", 403
    
    if request.method == 'POST':
        try:
            data = request.json
            
            log(f"\n{'='*70}")
            log("📥 WEBHOOK RECIBIDO")
            log(f"{'='*70}")
            log(f"PAYLOAD COMPLETO: {json.dumps(data, indent=2)}")
            
            event = data.get('event')
            message_type = data.get('message_type')
            
            log(f"Event: {event}")
            log(f"Message type: {message_type}")
            
            content = data.get('content', '')
            
            # Si el humano escribe "." → desactivar bot
            if message_type == 'outgoing' and content.strip() == '.':
                store.deactivate_bot()
                log("🔴 COMANDO RECIBIDO: Bot desactivado")
                return jsonify({"status": "bot_deactivated"}), 200
            
            # Si el humano escribe ".." → activar bot
            if message_type == 'outgoing' and content.strip() == '..':
                store.activate_bot()
                log("🟢 COMANDO RECIBIDO: Bot activado")
                return jsonify({"status": "bot_activated"}), 200
            
            # Solo procesar mensajes entrantes
            if message_type != 'incoming':
                log(f"⚠️ Ignorado - no es incoming")
                return jsonify({"status": "ignored"}), 200
            
            # VERIFICAR SI BOT ESTÁ ACTIVO
            if not store.is_bot_active():
                log("🔴 BOT DESACTIVADO - Humano en control, mensaje ignorado")
                return jsonify({"status": "bot_inactive"}), 200
            
            # Extraer datos
            conversation = data.get('conversation', {})
            conversation_id = conversation.get('id')
            
            sender = data.get('sender', {})
            phone = sender.get('phone_number', '').replace('+', '')
            name = sender.get('name', 'Cliente')
            
            if not phone or not content:
                log("⚠️ Sin phone o content")
                return jsonify({"status": "ignored"}), 200
            
            log(f"📩 De: {name} ({phone})")
            log(f"💬 Contenido: {content[:100]}")
            log(f"🔢 Conv ID: {conversation_id}")
            
            # Guardar y procesar
            store.set_user_data(phone, 'name', name)
            store.set_user_data(phone, 'conversation_id', conversation_id)
            
            store.add_message(phone, content)
            store.schedule_processing(phone, process_accumulated_messages)
            
            log(f"✅ Encolado")
            log(f"{'='*70}\n")
            
            return jsonify({"status": "queued"}), 200
            
        except Exception as e:
            log(f"❌ ERROR: {e}")
            import traceback
            log(traceback.format_exc())
            return jsonify({"status": "error"}), 500

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        **store.get_stats()
    }), 200

if __name__ == '__main__':
    log("="*70)
    log("🤖 Bot WhatsApp - Studio Gabrielle Natal")
    log("✨ ASISTENTE: ESSENZA")
    log("✨ CHATWOOT FORMAT")
    log("="*70)
    
    port = int(os.getenv('PORT', 10000))
    
    log(f"Puerto: {port}")
    log(f"OpenAI: {'✅' if OPENAI_API_KEY else '❌'}")
    log(f"Chatwoot Token: {'✅' if CHATWOOT_API_TOKEN else '❌'}")
    log(f"Account ID: {CHATWOOT_ACCOUNT_ID}")
    log("="*70)
    log("🚀 Iniciando Essenza...\n")
    
    app.run(host='0.0.0.0', port=port, debug=False)
