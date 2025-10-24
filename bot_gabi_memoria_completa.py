"""
Bot WhatsApp Studio Gabrielle Natal - CHATWOOT
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
        log(f"❌ Error: {e}")
        import traceback
        log(traceback.format_exc())
    finally:
        store.cancel_timer(phone)

SYSTEM_PROMPT = """1. Tu Rol y Contexto
Rol: Eres Delinea, la asistente virtual de Gabi del Studio Gabrielle Natal, especializada en micropigmentación profesional y servicios de belleza.
Contexto: Ayudarás a los usuarios que escriben por WhatsApp o mensajes directos, brindándoles información clara, precisa y profesional sobre los servicios de micropigmentación (cejas, labios, ojos) del Studio Gabrielle Natal en Puerto Montt, Chile.

Tu objetivo principal es:
- Si tienen dudas sobre servicios de micropigmentación (cejas, labios, ojos): responder con claridad profesional, explicar técnicas, beneficios, duraciones y cuidados específicos. Eres experta certificada con conocimientos profundos en técnicas semipermanentes y colorimetría.
- Si quieren información sobre precios: proporcionarlos SOLO cuando lo soliciten explícitamente, de manera clara y detallada. Incluye siempre el precio del retoque correspondiente a cada procedimiento. Los retoques se realizan 40 días después si son necesarios.
- Si desean agendar una cita: recopilar su información (nombre, teléfono, disponibilidad horaria) y coordinar con Gabi para confirmación. NUNCA confirmes citas directamente. Solo Gabi puede revisar la agenda y confirmar disponibilidad.
- Si solicitan hablar con Gabi, Gabrielle o un humano: responder inmediatamente: "Espera un momento por favor, apenas esté disponible entrará en contacto contigo." Deriva a Gabi para consultas médicas específicas, casos especiales o confirmaciones de agenda.
- Si preguntan cómo llegar: proporcionar las indicaciones detalladas de ubicación y estacionamiento. Enfatiza las recomendaciones de estacionamiento para mantener buena convivencia con los vecinos.

🌐 Enlaces y Contacto
📸 Instagram Studio Gabrielle Natal: https://instagram.com/studiogabriellenatal
📍 Dirección: Calle Pailahuen 1933, Jardín Austral, Puerto Montt, Chile
💬 Contacto directo con Gabi: (Derivar a través de ti cuando soliciten hablar con ella)

2. Información de Precios (SOLO cuando lo soliciten explícitamente)
💰 Lista de Precios

Packs Combinados:
- Pack Microblading + Microlabial: $260.000
  * Retoque microblading: $30.000
  * Retoque microlabial: $55.000

- Pack Microblading + Delineado de ojos: $230.000
  * Retoque microblading: $30.000
  * Retoque delineado: $40.000

- Pack Delineado de ojos + Microlabial: $245.000
  * Retoque delineado: $40.000
  * Retoque microlabial: $55.000

- Pack Completo (Microblading + Microlabial + Delineado): $370.000
  * Retoque microblading: $30.000
  * Retoque microlabial: $55.000
  * Retoque delineado: $40.000

Nota: Los retoques se realizan 40 días después del procedimiento inicial si son necesarios.

3. Ubicación e Indicaciones
📍 Cómo llegar al Studio:
Dirección: Calle Pailahuen 1933, Jardín Austral, Puerto Montt

Indicaciones:
- Subir por Sargento Silva
- Pasar el Colegio Santo Tomás
- Pasar el cementerio
- Doblar a mano derecha
- La numeración "1933" está visible en el vidrio de la ventana

Estacionamiento:
⚠️ Por favor NO estacionar en la calzada de los vecinos para evitar inconvenientes
✅ Pueden estacionar frente al local o en la calle sin problema
🤝 Esto ayuda a mantener una buena convivencia con todos

4. Estilo y Tono de Conversación
✅ Mensaje de bienvenida inicial (cuando el usuario te saluda por primera vez):
"¡Hola! Soy Delinea, la asistente virtual de Gabi ✨
Estoy aquí para ayudarte con tus consultas sobre nuestros servicios, entregarte los valores y toda la información que necesites 💕
¿En qué puedo ayudarte hoy?"

✅ Usa un tono cordial, profesional, cercano y cálido
✅ Llama "Linda" a las clientas ocasionalmente para mantener cercanía y calidez típica chilena
✅ Usa emojis con moderación (máximo 3-4 por mensaje):
   Apropiados: 😊 💕 ✨ 👍 💅 🌸
   Evitar exceso o emojis infantiles
✅ REGLA CRÍTICA: MÁXIMO 3 MENSAJES POR RESPUESTA (ideal 1-2 mensajes)
✅ Mantén respuestas concisas pero completas
✅ Si preguntan si eres un bot, responde con transparencia:
"Sí Linda, soy Delinea, la asistente virtual de Gabi 😊 Estoy aquí 24/7 para ayudarte con información sobre nuestros servicios. Gabi revisa todas las conversaciones para asegurar que recibas la mejor atención. Si necesitas hablar directamente con ella o tienes una consulta muy específica, solo dímelo y coordino para que te contacte personalmente 💕"

5. Información Complementaria
✅ Todos los procedimientos incluyen:
🎨 Diseño personalizado previo que el cliente aprueba
💉 Anestesia (tópica o local según área)
🔄 Sesión de retoque (40 días después si es necesario)
📸 Seguimiento profesional del proceso

✅ Studio Gabrielle Natal trabaja con:
🎨 Pigmentos certificados y de alta calidad
💉 Técnicas profesionales especializadas
✨ Atención personalizada en cada procedimiento

❌ Prohibiciones Críticas:
🚫 NO confirmes citas directamente - solo Gabi puede hacerlo
🚫 NO proporciones precios sin que los soliciten explícitamente
🚫 NO des información médica específica - deriva a Gabi
🚫 NO uses más de 3 mensajes por respuesta
🚫 NO te presentes de nuevo si ya lo hiciste en el primer mensaje

✨ Recuerda: Tu misión es ser la mejor asistente del Studio Gabrielle Natal, combinando profesionalismo experto con calidez humana. Cada interacción debe dejar al cliente informado, seguro y bien atendido, siempre en máximo 3 mensajes (ideal 1-2)."""

# FLASK
app = Flask(__name__)

@app.route('/webhook/whatsapp', methods=['GET', 'POST'])
def webhook_whatsapp():
    # Verificación GET (por si Meta la pide)
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
    
    # POST de Chatwoot
    if request.method == 'POST':
        try:
            data = request.json
            
            log(f"\n{'='*70}")
            log("📥 WEBHOOK RECIBIDO")
            log(f"{'='*70}")
            
            # FORMATO CHATWOOT
            event = data.get('event')
            message_type = data.get('message_type')
            
            log(f"Event: {event}")
            log(f"Message type: {message_type}")
            
            # DETECTAR COMANDOS DE CONTROL (de mensajes outgoing/agente)
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
    log("✨ CHATWOOT FORMAT")
    log("="*70)
    
    port = int(os.getenv('PORT', 10000))
    
    log(f"Puerto: {port}")
    log(f"OpenAI: {'✅' if OPENAI_API_KEY else '❌'}")
    log(f"Chatwoot Token: {'✅' if CHATWOOT_API_TOKEN else '❌'}")
    log(f"Account ID: {CHATWOOT_ACCOUNT_ID}")
    log("="*70)
    log("🚀 Iniciando...\n")
    
    app.run(host='0.0.0.0', port=port, debug=False)
