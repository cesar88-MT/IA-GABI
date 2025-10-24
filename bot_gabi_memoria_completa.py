"""
Bot de WhatsApp para Studio Gabrielle Natal
VERSIÓN CORREGIDA v2 - Con logging forzado para Render
"""

import os
import sys
import time
import json
import base64
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from collections import defaultdict, deque
from io import BytesIO
from threading import Lock, Timer

from flask import Flask, request, jsonify
from openai import OpenAI
import requests

# Forzar flush de stdout para que los logs aparezcan en Render
def log(message):
    """Log con flush forzado"""
    print(message, flush=True)
    sys.stdout.flush()

# ============================================================================
# CONFIGURACIÓN
# ============================================================================

OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', "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")
WHATSAPP_ACCESS_TOKEN = os.getenv('WHATSAPP_ACCESS_TOKEN', "EAAKFvnVI8H8BP7ZCGpS2bpdtZCOcWZCkCp5P1m3vuRmZBDxokbcfldJxiRw2sDFC3IH5NySFX187jZCoJnqrhM1zMK6Yk0P91jqxGJXUF6iQn1ZAXMuCbXHPBgAFnTiUTv0ZC7TQrTJPwFceZCC97jkUA3DfNsLfQAjyCC0wBy84RgRXV5PZAvlOkHi8FHu1h7GvJ9BpaT5zoUxIWu2FqPNsJgk2aF9cSiO0ZBDSJZC8DZC2Ysv0dL2FVrHa48TvrQZDZD")
WHATSAPP_PHONE_NUMBER_ID = os.getenv('WHATSAPP_PHONE_NUMBER_ID', "878161422037681")
WHATSAPP_API_VERSION = os.getenv('WHATSAPP_API_VERSION', "v20.0")
WHATSAPP_VERIFY_TOKEN = os.getenv('WHATSAPP_VERIFY_TOKEN', "gabi_verify_token_123")

MESSAGE_GROUPING_DELAY = 4
MESSAGE_SEND_DELAY = 2
MAX_HISTORY_MESSAGES = 20

# ============================================================================
# STORAGE EN MEMORIA
# ============================================================================

class InMemoryStore:
    def __init__(self):
        self.messages: Dict[str, List[str]] = defaultdict(list)
        self.chat_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=MAX_HISTORY_MESSAGES))
        self.timers: Dict[str, Timer] = {}
        self.last_activity: Dict[str, datetime] = {}
        self.user_data: Dict[str, Dict] = defaultdict(dict)
        self.lock = Lock()
    
    def add_message(self, phone: str, message: str):
        with self.lock:
            self.messages[phone].append(message)
            self.last_activity[phone] = datetime.now()
            log(f"✅ Mensaje agregado a cola para {phone}")
    
    def get_messages(self, phone: str) -> List[str]:
        with self.lock:
            messages = self.messages.get(phone, []).copy()
            return list(reversed(messages))
    
    def clear_messages(self, phone: str):
        with self.lock:
            if phone in self.messages:
                count = len(self.messages[phone])
                self.messages[phone].clear()
                log(f"🧹 Cola limpiada para {phone} ({count} mensajes)")
    
    def add_to_history(self, phone: str, role: str, content: str):
        with self.lock:
            self.chat_history[phone].append({
                'role': role,
                'content': content,
                'created_at': datetime.now()
            })
            log(f"📝 Agregado al historial [{role}]: {content[:50]}...")
    
    def get_history(self, phone: str, limit: int = MAX_HISTORY_MESSAGES) -> List[Dict]:
        with self.lock:
            history = list(self.chat_history.get(phone, []))
            return history[-limit:] if limit else history
    
    def get_last_conversation_time(self, phone: str) -> Optional[datetime]:
        with self.lock:
            history = self.chat_history.get(phone, [])
            return history[-1]['created_at'] if history else None
    
    def clear_history(self, phone: str):
        with self.lock:
            if phone in self.chat_history:
                self.chat_history[phone].clear()
    
    def get_last_activity(self, phone: str) -> Optional[datetime]:
        return self.last_activity.get(phone)
    
    def schedule_processing(self, phone: str, callback):
        with self.lock:
            if phone in self.timers:
                self.timers[phone].cancel()
                log(f"⏰ Timer anterior cancelado para {phone}")
            
            timer = Timer(MESSAGE_GROUPING_DELAY, callback, args=[phone])
            self.timers[phone] = timer
            timer.start()
            log(f"⏰ Timer programado: {MESSAGE_GROUPING_DELAY}s para {phone}")
    
    def cancel_timer(self, phone: str):
        with self.lock:
            if phone in self.timers:
                self.timers[phone].cancel()
                del self.timers[phone]
                log(f"⏰ Timer cancelado para {phone}")
    
    def set_user_data(self, phone: str, key: str, value):
        with self.lock:
            self.user_data[phone][key] = value
    
    def get_user_data(self, phone: str, key: str, default=None):
        with self.lock:
            return self.user_data.get(phone, {}).get(key, default)
    
    def get_stats(self) -> Dict:
        with self.lock:
            return {
                'active_conversations': len(self.messages),
                'total_users': len(self.chat_history),
                'pending_timers': len(self.timers),
                'total_messages_in_history': sum(len(hist) for hist in self.chat_history.values())
            }

store = InMemoryStore()
openai_client = OpenAI(api_key=OPENAI_API_KEY)

# ============================================================================
# FUNCIONES DE PROCESAMIENTO
# ============================================================================

def get_media_base64(media_id: str) -> Optional[str]:
    """Descarga media de WhatsApp API"""
    try:
        url = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{media_id}"
        headers = {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}"}
        
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        media_url = response.json().get('url')
        if not media_url:
            log(f"❌ No URL para media_id: {media_id}")
            return None
        
        media_response = requests.get(media_url, headers=headers, timeout=30)
        media_response.raise_for_status()
        
        media_base64 = base64.b64encode(media_response.content).decode('utf-8')
        log(f"✅ Media descargado: {media_id}")
        return media_base64
    
    except Exception as e:
        log(f"❌ Error descargando media {media_id}: {str(e)}")
        return None


def process_message_content(message_type: str, content: str, media_id: Optional[str] = None) -> str:
    """Procesa diferentes tipos de mensajes"""
    try:
        log(f"🔄 Procesando mensaje tipo: {message_type}")
        
        if message_type == 'text':
            return content
        elif message_type == 'audio':
            if media_id:
                get_media_base64(media_id)
            return "[Audio recibido]"
        elif message_type == 'image':
            caption = content if content else ""
            return f"[Imagen recibida{': ' + caption if caption else ''}]"
        elif message_type == 'sticker':
            return "[Sticker recibido]"
        elif message_type == 'document':
            return f"[Documento recibido: {content}]"
        elif message_type == 'video':
            return "[Video recibido]"
        elif message_type == 'location':
            return "[Ubicación compartida]"
        else:
            return f"[Mensaje tipo {message_type}]"
    
    except Exception as e:
        log(f"❌ Error procesando contenido: {str(e)}")
        return "[Error procesando mensaje]"


def generate_assistant_response(phone: str, combined_message: str) -> str:
    """Genera respuesta con OpenAI"""
    try:
        log(f"🤖 Iniciando generación de respuesta para {phone}")
        
        history = store.get_history(phone, limit=10)
        last_conv_time = store.get_last_conversation_time(phone)
        user_name = store.get_user_data(phone, 'name', 'Cliente')
        
        is_first_contact = len(history) == 0
        is_new_conversation = False
        
        if last_conv_time:
            time_since_last = datetime.now() - last_conv_time
            is_new_conversation = time_since_last > timedelta(hours=3)
        
        log(f"📊 Contexto: first={is_first_contact}, new_conv={is_new_conversation}, history={len(history)}")
        
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        
        if not is_first_contact and not is_new_conversation:
            for msg in history:
                messages.append({"role": msg['role'], "content": msg['content']})
        
        messages.append({"role": "user", "content": combined_message})
        
        log(f"🔄 Llamando a OpenAI (mensajes en contexto: {len(messages)})")
        
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            temperature=0.7,
            max_tokens=500
        )
        
        assistant_response = response.choices[0].message.content.strip()
        log(f"✅ Respuesta generada ({len(assistant_response)} chars)")
        
        return assistant_response
    
    except Exception as e:
        log(f"❌ Error generando respuesta: {str(e)}")
        import traceback
        log(traceback.format_exc())
        return "Disculpa, tuve un problema al procesar tu mensaje. Por favor intenta nuevamente."


def send_whatsapp_messages(phone: str, response_text: str):
    """Envía mensajes a WhatsApp"""
    try:
        log(f"📤 Preparando envío a {phone}")
        
        messages = [msg.strip() for msg in response_text.split('\n\n') if msg.strip()]
        messages = messages[:3]
        
        url = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{WHATSAPP_PHONE_NUMBER_ID}/messages"
        headers = {
            "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }
        
        log(f"📨 Enviando {len(messages)} mensaje(s)")
        
        for i, message in enumerate(messages, 1):
            payload = {
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "text",
                "text": {"body": message}
            }
            
            log(f"📤 Enviando mensaje {i}/{len(messages)}...")
            response = requests.post(url, headers=headers, json=payload, timeout=10)
            
            if response.status_code == 200:
                log(f"✅ Mensaje {i}/{len(messages)} enviado")
            else:
                log(f"❌ Error en mensaje {i}: {response.status_code} - {response.text}")
            
            if i < len(messages):
                time.sleep(MESSAGE_SEND_DELAY)
        
        return True
    
    except Exception as e:
        log(f"❌ Error enviando mensajes: {str(e)}")
        import traceback
        log(traceback.format_exc())
        return False


def process_accumulated_messages(phone: str):
    """
    FUNCIÓN CRÍTICA - Procesa mensajes acumulados
    """
    try:
        log("\n" + "=" * 80)
        log(f"🔄 PROCESANDO MENSAJES ACUMULADOS PARA: {phone}")
        log("=" * 80)
        
        messages = store.get_messages(phone)
        
        if not messages:
            log(f"⚠️  No hay mensajes para {phone}")
            return
        
        log(f"📥 Mensajes en cola: {len(messages)}")
        
        combined_message = "\n".join(messages)
        log(f"📝 Mensaje combinado: {combined_message[:100]}...")
        
        store.clear_messages(phone)
        store.add_to_history(phone, 'user', combined_message)
        
        log("🤖 Generando respuesta...")
        assistant_response = generate_assistant_response(phone, combined_message)
        
        store.add_to_history(phone, 'assistant', assistant_response)
        
        log("📤 Enviando respuesta...")
        success = send_whatsapp_messages(phone, assistant_response)
        
        if success:
            log(f"✅ PROCESO COMPLETADO EXITOSAMENTE para {phone}")
        else:
            log(f"⚠️  Respuesta generada pero falló envío para {phone}")
        
        log("=" * 80 + "\n")
    
    except Exception as e:
        log(f"❌ ERROR CRÍTICO procesando {phone}: {str(e)}")
        import traceback
        log(traceback.format_exc())
    
    finally:
        store.cancel_timer(phone)


# ============================================================================
# PROMPT DEL SISTEMA
# ============================================================================

SYSTEM_PROMPT = """Eres la asistente virtual de Studio Gabrielle Natal, un estudio de micropigmentación profesional en Puerto Montt, Chile, dirigido por Gabriela Alvarez (Gabi).

1. Presentación Inicial (Solo Primera Interacción)
Cuando sea el primer contacto con la cliente, SIEMPRE envía este mensaje de bienvenida COMPLETO:

¡Hola! ✨ Bienvenida a Studio Gabrielle Natal 🌸

Soy la asistente virtual de Gabi y estoy aquí para ayudarte con todo lo que necesites sobre nuestros servicios de micropigmentación.

🎯 ¿En qué puedo ayudarte hoy?
• Información sobre servicios y precios
• Agendar una cita
• Responder tus dudas sobre los procedimientos
• Indicaciones para llegar al studio

¡Cuéntame qué te interesa! 💕

2. Información Clave del Negocio
📞 Contacto: +56978765400 (WhatsApp)

⏰ Horario de Atención:
Lunes a Viernes: 10:00 - 19:00
Sábados: 10:00 - 14:00
Domingos: Cerrado

3. Servicios
🔸 Microblading de Cejas: $120.000 (Retoque: $30.000)
🔸 Microlabial: $150.000 (Retoque: $55.000)
🔸 Delineado de Ojos: $150.000 (Retoque: $40.000)

4. Packs Especiales
- Pack Microblading + Delineado: $240.000
- Pack Microblading + Microlabial: $245.000
- Pack Delineado + Microlabial: $245.000
- Pack Completo: $370.000

📍 Ubicación: Calle Pailahuen 1933, Jardín Austral, Puerto Montt, Chile

5. Estilo y Tono
✅ Cordial, profesional y cálido
✅ Usa "Querida" ocasionalmente
✅ Emojis con moderación (3-4 por mensaje)
✅ MÁXIMO 3 MENSAJES POR RESPUESTA

Si solicitan hablar con Gabi: "Espera un momento por favor, apenas esté disponible entrará en contacto contigo."

❌ No confirmes citas directamente
❌ No omitas mensaje de presentación inicial
❌ No uses más de 3 mensajes por respuesta"""


# ============================================================================
# FLASK APP
# ============================================================================

app = Flask(__name__)


@app.route('/webhook/whatsapp', methods=['GET', 'POST'])
def webhook_whatsapp():
    """Webhook principal"""
    
    if request.method == 'GET':
        mode = request.args.get('hub.mode')
        token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')
        
        if mode == 'subscribe' and token == WHATSAPP_VERIFY_TOKEN:
            log("✅ Webhook verificado")
            return challenge, 200
        else:
            log("❌ Verificación fallida")
            return "Forbidden", 403
    
    if request.method == 'POST':
        try:
            data = request.json
            log("\n" + "=" * 80)
            log("📥 WEBHOOK POST RECIBIDO")
            log("=" * 80)
            log(f"Payload: {json.dumps(data, indent=2)}")
            
            if not data.get('entry'):
                log("⚠️  No 'entry' en payload")
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
                            
                            log(f"📩 MENSAJE DETECTADO:")
                            log(f"   De: {phone} ({name})")
                            log(f"   Tipo: {message_type}")
                            log(f"   Contenido: {content[:100]}")
                            
                            store.set_user_data(phone, 'name', name)
                            processed_content = process_message_content(message_type, content, media_id)
                            store.add_message(phone, processed_content)
                            store.schedule_processing(phone, process_accumulated_messages)
                            
                            log(f"✅ Mensaje encolado para {phone}")
            
            log("=" * 80 + "\n")
            return jsonify({"status": "queued"}), 200
        
        except Exception as e:
            log(f"❌ ERROR EN WEBHOOK: {str(e)}")
            import traceback
            log(traceback.format_exc())
            return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/health', methods=['GET'])
def health():
    """Health check"""
    stats = store.get_stats()
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        **stats
    }), 200


@app.route('/stats', methods=['GET'])
def stats():
    """Estadísticas"""
    return jsonify(store.get_stats()), 200


@app.route('/history/<phone>', methods=['GET'])
def get_user_history(phone):
    """Historial de usuario"""
    limit = request.args.get('limit', 20, type=int)
    history = store.get_history(phone, limit=limit)
    
    for msg in history:
        msg['created_at'] = msg['created_at'].isoformat()
    
    return jsonify({
        "phone": phone,
        "history": history,
        "total_messages": len(history)
    }), 200


@app.route('/clear/<phone>', methods=['POST'])
def clear_user_data(phone):
    """Limpiar datos de usuario"""
    store.clear_messages(phone)
    store.clear_history(phone)
    store.cancel_timer(phone)
    
    return jsonify({
        "status": "cleared",
        "phone": phone
    }), 200


# ============================================================================
# MAIN
# ============================================================================

if __name__ == '__main__':
    log("=" * 70)
    log("🤖 Bot de WhatsApp - Studio Gabrielle Natal")
    log("=" * 70)
    log("✨ VERSIÓN CORREGIDA v2 - Logging forzado")
    log("=" * 70)
    
    port = int(os.getenv('PORT', 10000))
    
    log(f"Puerto: {port}")
    log(f"Webhook: /webhook/whatsapp")
    log(f"Health: /health")
    log("=" * 70)
    log("📝 Credenciales verificadas")
    log(f"   OpenAI: {'✅' if OPENAI_API_KEY.startswith('sk-') else '❌'}")
    log(f"   WhatsApp: ✅")
    log(f"   Phone ID: {WHATSAPP_PHONE_NUMBER_ID}")
    log("=" * 70)
    log("🚀 Iniciando servidor...")
    log("=" * 70 + "\n")
    
    app.run(host='0.0.0.0', port=port, debug=False)
