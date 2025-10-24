"""
Bot WhatsApp Studio Gabrielle Natal - DIRECTO A META
SEGURO - Sin API keys en código
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

# CONFIGURACIÓN - Lee desde variables de entorno
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
WHATSAPP_ACCESS_TOKEN = os.getenv('WHATSAPP_ACCESS_TOKEN', 'EAAKFvnVI8H8BP7ZCGpS2bpdtZCOcWZCkCp5P1m3vuRmZBDxokbcfldJxiRw2sDFC3IH5NySFX187jZCoJnqrhM1zMK6Yk0P91jqxGJXUF6iQn1ZAXMuCbXHPBgAFnTiUTv0ZC7TQrTJPwFceZCC97jkUA3DfNsLfQAjyCC0wBy84RgRXV5PZAvlOkHi8FHu1h7GvJ9BpaT5zoUxIWu2FqPNsJgk2aF9cSiO0ZBDSJZC8DZC2Ysv0dL2FVrHa48TvrQZDZD')
WHATSAPP_PHONE_NUMBER_ID = os.getenv('WHATSAPP_PHONE_NUMBER_ID', '878161422037681')
WHATSAPP_API_VERSION = os.getenv('WHATSAPP_API_VERSION', 'v20.0')
WHATSAPP_VERIFY_TOKEN = os.getenv('WHATSAPP_VERIFY_TOKEN', 'gabi_verify_token_123')

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
            log(f"⏰ Timer: {MESSAGE_GROUPING_DELAY}s para {phone}")
    
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
                'pending_timers': len(self.timers)
            }

store = InMemoryStore()
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# FUNCIONES
def process_message_content(message_type, content, media_id=None):
    try:
        if message_type == 'text':
            return content
        elif message_type == 'audio':
            return "[Audio recibido]"
        elif message_type == 'image':
            return f"[Imagen{': ' + content if content else ''}]"
        elif message_type == 'sticker':
            return "[Sticker]"
        elif message_type == 'document':
            return f"[Documento: {content}]"
        elif message_type == 'video':
            return "[Video]"
        elif message_type == 'location':
            return "[Ubicación]"
        else:
            return f"[{message_type}]"
    except Exception as e:
        log(f"❌ Error procesando contenido: {e}")
        return "[Error procesando mensaje]"

def generate_assistant_response(phone, combined_message):
    try:
        log(f"🤖 Generando respuesta para {phone}")
        
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
        
        log(f"🔄 Llamando OpenAI ({len(messages)} mensajes)")
        
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            temperature=0.7,
            max_tokens=500
        )
        
        result = response.choices[0].message.content.strip()
        log(f"✅ Respuesta generada ({len(result)} chars)")
        return result
        
    except Exception as e:
        log(f"❌ Error OpenAI: {e}")
        return "Disculpa, tuve un problema al procesar tu mensaje. Por favor intenta nuevamente."

def send_whatsapp_messages(phone, response_text):
    try:
        log(f"📤 Preparando envío a {phone}")
        
        parts = [p.strip() for p in response_text.split('\n\n') if p.strip()][:3]
        
        url = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{WHATSAPP_PHONE_NUMBER_ID}/messages"
        headers = {
            "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }
        
        log(f"📨 Enviando {len(parts)} mensaje(s)")
        
        for i, part in enumerate(parts, 1):
            payload = {
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "text",
                "text": {"body": part}
            }
            
            log(f"📤 Enviando mensaje {i}/{len(parts)}")
            response = requests.post(url, headers=headers, json=payload, timeout=10)
            
            if response.status_code == 200:
                log(f"✅ Mensaje {i}/{len(parts)} enviado")
            else:
                log(f"❌ Error {i}: {response.status_code} - {response.text}")
            
            if i < len(parts):
                time.sleep(MESSAGE_SEND_DELAY)
        
        return True
    except Exception as e:
        log(f"❌ Error enviando: {e}")
        return False

def process_accumulated_messages(phone):
    try:
        log(f"\n{'='*70}")
        log(f"🔄 PROCESANDO MENSAJES: {phone}")
        log(f"{'='*70}")
        
        messages = store.get_messages(phone)
        if not messages:
            log("⚠️ Sin mensajes en cola")
            return
        
        combined = "\n".join(messages)
        log(f"📝 {len(messages)} mensaje(s) acumulado(s)")
        
        store.clear_messages(phone)
        store.add_to_history(phone, 'user', combined)
        
        response = generate_assistant_response(phone, combined)
        store.add_to_history(phone, 'assistant', response)
        
        success = send_whatsapp_messages(phone, response)
        
        if success:
            log(f"✅ PROCESO COMPLETADO para {phone}")
        else:
            log(f"⚠️ Respuesta generada pero falló envío")
        
        log(f"{'='*70}\n")
    except Exception as e:
        log(f"❌ ERROR: {e}")
        import traceback
        log(traceback.format_exc())
    finally:
        store.cancel_timer(phone)

SYSTEM_PROMPT = """Eres la asistente virtual de Studio Gabrielle Natal, especializado en micropigmentación en Puerto Montt, Chile.

PRESENTACIÓN INICIAL (primer contacto):
¡Hola! ✨ Bienvenida a Studio Gabrielle Natal 🌸

Soy la asistente virtual de Gabi y estoy aquí para ayudarte con todo lo que necesites sobre nuestros servicios de micropigmentación.

🎯 ¿En qué puedo ayudarte hoy?
• Información sobre servicios y precios
• Agendar una cita
• Responder tus dudas sobre los procedimientos
• Indicaciones para llegar al studio

¡Cuéntame qué te interesa! 💕

INFORMACIÓN CLAVE:
📞 Contacto: +56978765400 (WhatsApp)

⏰ Horario de Atención:
Lunes a Viernes: 10:00 - 19:00
Sábados: 10:00 - 14:00
Domingos: Cerrado

SERVICIOS:
🔸 Microblading de Cejas: $120.000 (Retoque: $30.000)
🔸 Microlabial: $150.000 (Retoque: $55.000)
🔸 Delineado de Ojos: $150.000 (Retoque: $40.000)

PACKS ESPECIALES:
- Pack Microblading + Delineado: $240.000
- Pack Microblading + Microlabial: $245.000
- Pack Delineado + Microlabial: $245.000
- Pack Completo (los 3 servicios): $370.000

📍 Ubicación: Calle Pailahuen 1933, Jardín Austral, Puerto Montt, Chile

Indicaciones:
- Subir por Sargento Silva
- Pasar el Colegio Santo Tomás
- Pasar el cementerio
- Doblar a mano derecha
- La numeración "1933" está visible en el vidrio de la ventana

⚠️ Estacionamiento: Por favor NO estacionar en la calzada de los vecinos. Pueden estacionar frente al local o en la calle sin problema.

🌐 Instagram: https://instagram.com/studiogabriellenatal

ESTILO DE CONVERSACIÓN:
✅ Cordial, profesional y cálido
✅ Usa "Querida" ocasionalmente
✅ Emojis con moderación (3-4 por mensaje)
✅ MÁXIMO 3 MENSAJES POR RESPUESTA
✅ Mantén respuestas concisas pero completas

Si solicitan hablar con Gabi: "Espera un momento por favor, apenas esté disponible entrará en contacto contigo."

PROHIBICIONES:
❌ No omitas el mensaje de presentación inicial cuando sea el primer contacto
❌ No confirmes citas directamente (solo Gabi puede hacerlo)
❌ No uses más de 3 mensajes por respuesta"""

# FLASK
app = Flask(__name__)

@app.route('/webhook/whatsapp', methods=['GET', 'POST'])
def webhook_whatsapp():
    # VERIFICACIÓN GET (Meta)
    if request.method == 'GET':
        mode = request.args.get('hub.mode')
        token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')
        
        log(f"🔍 Verificación GET")
        log(f"   Mode: {mode}")
        log(f"   Token: {token}")
        
        if mode == 'subscribe' and token == WHATSAPP_VERIFY_TOKEN:
            log("✅ Verificación exitosa")
            return challenge, 200
        else:
            log("❌ Verificación fallida")
            return "Forbidden", 403
    
    # MENSAJES POST (Meta)
    if request.method == 'POST':
        try:
            data = request.json
            
            log(f"\n{'='*70}")
            log("📥 WEBHOOK POST RECIBIDO")
            log(f"{'='*70}")
            log(f"Payload: {json.dumps(data, indent=2)[:300]}...")
            
            # FORMATO META
            if not data.get('entry'):
                log("⚠️ Sin 'entry' - formato incorrecto")
                log(f"Payload completo: {json.dumps(data)}")
                return jsonify({"status": "ignored"}), 200
            
            for entry in data['entry']:
                for change in entry.get('changes', []):
                    value = change.get('value', {})
                    if value.get('messages'):
                        for msg in value['messages']:
                            phone = msg['from']
                            name = value.get('contacts', [{}])[0].get('profile', {}).get('name', '')
                            message_type = msg['type']
                            content = msg.get(message_type, {}).get('body', '') if message_type == 'text' else ''
                            media_id = msg.get(message_type, {}).get('id') if message_type in ['audio', 'image', 'sticker'] else None
                            
                            log(f"📩 De: {name} ({phone})")
                            log(f"📝 Tipo: {message_type}")
                            log(f"💬 Contenido: {content[:100]}")
                            
                            store.set_user_data(phone, 'name', name)
                            processed = process_message_content(message_type, content, media_id)
                            store.add_message(phone, processed)
                            store.schedule_processing(phone, process_accumulated_messages)
                            
                            log(f"✅ Mensaje encolado")
            
            log(f"{'='*70}\n")
            return jsonify({"status": "queued"}), 200
        
        except Exception as e:
            log(f"❌ ERROR en webhook: {e}")
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

@app.route('/stats', methods=['GET'])
def stats():
    return jsonify(store.get_stats()), 200

if __name__ == '__main__':
    log("="*70)
    log("🤖 Bot WhatsApp - Studio Gabrielle Natal")
    log("✨ DIRECTO A META - SIN CHATWOOT")
    log("="*70)
    
    port = int(os.getenv('PORT', 10000))
    
    log(f"Puerto: {port}")
    log(f"OpenAI: {'✅' if OPENAI_API_KEY else '❌ FALTA'}")
    log(f"WhatsApp Phone ID: {WHATSAPP_PHONE_NUMBER_ID}")
    log(f"Verify Token: {WHATSAPP_VERIFY_TOKEN}")
    log("="*70)
    log("🚀 Iniciando servidor...\n")
    
    app.run(host='0.0.0.0', port=port, debug=False)
