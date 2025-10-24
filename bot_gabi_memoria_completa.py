"""
Bot WhatsApp Studio Gabrielle Natal - Compatible con CHATWOOT
"""
import os
import sys
import time
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from collections import defaultdict, deque
from threading import Lock, Timer

from flask import Flask, request, jsonify
from openai import OpenAI
import requests

def log(msg):
    print(msg, flush=True)
    sys.stdout.flush()

# CONFIGURACIÓN
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', "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")
CHATWOOT_API_TOKEN = os.getenv('CHATWOOT_API_TOKEN', 'TU_TOKEN_CHATWOOT')
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
        self.lock = Lock()
    
    def add_message(self, phone, message):
        with self.lock:
            self.messages[phone].append(message)
            self.last_activity[phone] = datetime.now()
    
    def get_messages(self, phone):
        with self.lock:
            return list(reversed(self.messages.get(phone, [])))
    
    def clear_messages(self, phone):
        with self.lock:
            if phone in self.messages:
                self.messages[phone].clear()
    
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
def generate_assistant_response(phone, combined_message):
    try:
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
        
        log(f"🤖 Llamando OpenAI...")
        
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            temperature=0.7,
            max_tokens=500
        )
        
        return response.choices[0].message.content.strip()
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
        
        log(f"📤 Enviando a Chatwoot conv {conversation_id}")
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        
        if response.status_code in [200, 201]:
            log(f"✅ Mensaje enviado")
            return True
        else:
            log(f"❌ Error {response.status_code}: {response.text}")
            return False
    except Exception as e:
        log(f"❌ Error enviando: {e}")
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
        log(f"📝 Mensajes: {len(messages)}")
        
        store.clear_messages(phone)
        store.add_to_history(phone, 'user', combined)
        
        response = generate_assistant_response(phone, combined)
        store.add_to_history(phone, 'assistant', response)
        
        # Obtener conversation_id guardado
        conv_id = store.get_user_data(phone, 'conversation_id')
        if conv_id:
            parts = [p.strip() for p in response.split('\n\n') if p.strip()][:3]
            
            for i, part in enumerate(parts, 1):
                send_chatwoot_message(conv_id, part)
                if i < len(parts):
                    time.sleep(MESSAGE_SEND_DELAY)
            
            log(f"✅ COMPLETADO")
        else:
            log(f"❌ Sin conversation_id para {phone}")
        
        log(f"{'='*70}\n")
    except Exception as e:
        log(f"❌ Error: {e}")
    finally:
        store.cancel_timer(phone)

SYSTEM_PROMPT = """Eres la asistente virtual de Studio Gabrielle Natal, especializado en micropigmentación en Puerto Montt, Chile.

PRESENTACIÓN INICIAL (primer contacto):
¡Hola! ✨ Bienvenida a Studio Gabrielle Natal 🌸

Soy la asistente virtual de Gabi y estoy aquí para ayudarte con todo lo que necesites sobre nuestros servicios de micropigmentación.

🎯 ¿En qué puedo ayudarte hoy?
• Información sobre servicios y precios
• Agendar una cita
• Responder tus dudas
• Indicaciones para llegar

¡Cuéntame qué te interesa! 💕

INFORMACIÓN:
📞 Contacto: +56978765400
⏰ Horarios:
- Lunes a Viernes: 10:00-19:00
- Sábados: 10:00-14:00
- Domingos: Cerrado

SERVICIOS:
🔸 Microblading: $120.000 (Retoque: $30.000)
🔸 Microlabial: $150.000 (Retoque: $55.000)
🔸 Delineado: $150.000 (Retoque: $40.000)

PACKS:
- Microblading + Delineado: $240.000
- Microblading + Microlabial: $245.000
- Delineado + Microlabial: $245.000
- Pack Completo: $370.000

📍 Ubicación: Pailahuen 1933, Jardín Austral, Puerto Montt

ESTILO:
✅ Cordial y cálido
✅ Usa "Querida" ocasionalmente
✅ Máximo 3 mensajes por respuesta
✅ Emojis con moderación

Si piden hablar con Gabi: "Espera un momento, apenas esté disponible entrará en contacto."

❌ No confirmes citas directamente"""

# FLASK
app = Flask(__name__)

@app.route('/webhook/whatsapp', methods=['POST'])
def webhook_whatsapp():
    try:
        data = request.json
        
        log("\n" + "="*70)
        log("📥 WEBHOOK RECIBIDO")
        log("="*70)
        
        # FORMATO CHATWOOT
        event = data.get('event')
        
        if event != 'message_created':
            log(f"⚠️ Evento ignorado: {event}")
            return jsonify({"status": "ignored"}), 200
        
        # Extraer datos del formato Chatwoot
        conversation = data.get('conversation', {})
        conversation_id = conversation.get('id')
        
        messages_list = data.get('messages', [])
        if not messages_list:
            log("⚠️ Sin mensajes")
            return jsonify({"status": "ignored"}), 200
        
        msg = messages_list[0]
        sender = data.get('sender', {})
        
        # Verificar que sea mensaje entrante
        message_type = data.get('message_type')
        if message_type != 'incoming':
            log(f"⚠️ No es incoming: {message_type}")
            return jsonify({"status": "ignored"}), 200
        
        # Extraer información
        phone = sender.get('phone_number', '').replace('+', '')
        name = sender.get('name', 'Cliente')
        content = msg.get('content', '')
        
        log(f"📩 De: {name} ({phone})")
        log(f"📝 Contenido: {content[:100]}")
        log(f"💬 Conversation ID: {conversation_id}")
        
        # Guardar datos
        store.set_user_data(phone, 'name', name)
        store.set_user_data(phone, 'conversation_id', conversation_id)
        
        # Procesar
        store.add_message(phone, content)
        store.schedule_processing(phone, process_accumulated_messages)
        
        log(f"✅ Encolado para {phone}")
        log("="*70 + "\n")
        
        return jsonify({"status": "queued"}), 200
        
    except Exception as e:
        log(f"❌ ERROR: {e}")
        import traceback
        log(traceback.format_exc())
        return jsonify({"status": "error", "message": str(e)}), 500

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
    log("✨ VERSIÓN CHATWOOT COMPATIBLE")
    log("="*70)
    
    port = int(os.getenv('PORT', 10000))
    
    log(f"Puerto: {port}")
    log(f"OpenAI: {'✅' if OPENAI_API_KEY.startswith('sk-') else '❌'}")
    log(f"Chatwoot URL: {CHATWOOT_API_URL}")
    log(f"Account ID: {CHATWOOT_ACCOUNT_ID}")
    log("="*70)
    log("🚀 Iniciando...\n")
    
    app.run(host='0.0.0.0', port=port, debug=False)
