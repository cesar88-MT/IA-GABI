"""
Bot de WhatsApp para Studio Gabrielle Natal
Versión 100% en Memoria (Sin Redis, Sin PostgreSQL)
Integración directa con WhatsApp Business API de Meta
VERSIÓN CORREGIDA - Con todas las funciones necesarias
"""

import os
import time
import json
import base64
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from collections import defaultdict, deque
from io import BytesIO
from threading import Lock, Timer

from flask import Flask, request, jsonify
from openai import OpenAI
import requests

# ============================================================================
# CONFIGURACIÓN - CREDENCIALES DE WHATSAPP Y OPENAI
# ============================================================================

# OpenAI
OPENAI_API_KEY = "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"

# WhatsApp Business API
WHATSAPP_ACCESS_TOKEN = "EAAKFvnVI8H8BP7ZCGpS2bpdtZCOcWZCkCp5P1m3vuRmZBDxokbcfldJxiRw2sDFC3IH5NySFX187jZCoJnqrhM1zMK6Yk0P91jqxGJXUF6iQn1ZAXMuCbXHPBgAFnTiUTv0ZC7TQrTJPwFceZCC97jkUA3DfNsLfQAjyCC0wBy84RgRXV5PZAvlOkHi8FHu1h7GvJ9BpaT5zoUxIWu2FqPNsJgk2aF9cSiO0ZBDSJZC8DZC2Ysv0dL2FVrHa48TvrQZDZD"
WHATSAPP_PHONE_NUMBER_ID = "878161422037681"
WHATSAPP_BUSINESS_ACCOUNT_ID = "2318712901907194"
WHATSAPP_API_VERSION = "v20.0"
WHATSAPP_VERIFY_TOKEN = "gabi_verify_token_123"

# Configuración del bot
MESSAGE_GROUPING_DELAY = 4  # segundos para agrupar mensajes
MESSAGE_SEND_DELAY = 2  # segundos entre envío de mensajes
MAX_HISTORY_MESSAGES = 20  # mensajes máximos en historial por usuario


# ============================================================================
# ALMACENAMIENTO EN MEMORIA (Reemplaza Redis + PostgreSQL)
# ============================================================================

class InMemoryStore:
    """
    Almacenamiento completo en memoria RAM
    Reemplaza tanto Redis como PostgreSQL
    """
    
    def __init__(self):
        # Cola de mensajes (reemplaza Redis)
        self.messages: Dict[str, List[str]] = defaultdict(list)
        
        # Historial conversacional (reemplaza PostgreSQL)
        self.chat_history: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=MAX_HISTORY_MESSAGES)
        )
        
        # Timers de procesamiento
        self.timers: Dict[str, Timer] = {}
        
        # Última actividad
        self.last_activity: Dict[str, datetime] = {}
        
        # Datos adicionales de usuarios
        self.user_data: Dict[str, Dict] = defaultdict(dict)
        
        # Thread safety
        self.lock = Lock()
    
    # ========================================
    # MENSAJES EN COLA (Reemplaza Redis)
    # ========================================
    
    def add_message(self, phone: str, message: str):
        """Agrega un mensaje a la cola del usuario"""
        with self.lock:
            self.messages[phone].append(message)
            self.last_activity[phone] = datetime.now()
    
    def get_messages(self, phone: str) -> List[str]:
        """Obtiene todos los mensajes acumulados de un usuario"""
        with self.lock:
            messages = self.messages.get(phone, []).copy()
            return list(reversed(messages))  # Invertir como en Redis
    
    def clear_messages(self, phone: str):
        """Limpia los mensajes de un usuario"""
        with self.lock:
            if phone in self.messages:
                self.messages[phone].clear()
    
    # ========================================
    # HISTORIAL CONVERSACIONAL (Reemplaza PostgreSQL)
    # ========================================
    
    def add_to_history(self, phone: str, role: str, content: str):
        """Agrega un mensaje al historial conversacional"""
        with self.lock:
            self.chat_history[phone].append({
                'role': role,
                'content': content,
                'created_at': datetime.now()
            })
    
    def get_history(self, phone: str, limit: int = MAX_HISTORY_MESSAGES) -> List[Dict]:
        """Obtiene el historial de conversación"""
        with self.lock:
            history = list(self.chat_history.get(phone, []))
            return history[-limit:] if limit else history
    
    def get_last_conversation_time(self, phone: str) -> Optional[datetime]:
        """Obtiene la fecha de la última conversación"""
        with self.lock:
            history = self.chat_history.get(phone, [])
            if history:
                return history[-1]['created_at']
            return None
    
    def clear_history(self, phone: str):
        """Limpia el historial de un usuario"""
        with self.lock:
            if phone in self.chat_history:
                self.chat_history[phone].clear()
    
    # ========================================
    # TIMERS Y ACTIVIDAD
    # ========================================
    
    def get_last_activity(self, phone: str) -> Optional[datetime]:
        """Obtiene la última actividad del usuario"""
        return self.last_activity.get(phone)
    
    def schedule_processing(self, phone: str, callback):
        """Programa el procesamiento de mensajes después del delay"""
        with self.lock:
            # Cancelar timer anterior si existe
            if phone in self.timers:
                self.timers[phone].cancel()
            
            # Crear nuevo timer
            timer = Timer(MESSAGE_GROUPING_DELAY, callback, args=[phone])
            self.timers[phone] = timer
            timer.start()
    
    def cancel_timer(self, phone: str):
        """Cancela el timer de un usuario"""
        with self.lock:
            if phone in self.timers:
                self.timers[phone].cancel()
                del self.timers[phone]
    
    # ========================================
    # DATOS ADICIONALES
    # ========================================
    
    def set_user_data(self, phone: str, key: str, value):
        """Guarda datos adicionales del usuario"""
        with self.lock:
            self.user_data[phone][key] = value
    
    def get_user_data(self, phone: str, key: str, default=None):
        """Obtiene datos adicionales del usuario"""
        with self.lock:
            return self.user_data.get(phone, {}).get(key, default)
    
    # ========================================
    # ESTADÍSTICAS
    # ========================================
    
    def get_stats(self) -> Dict:
        """Obtiene estadísticas del almacenamiento"""
        with self.lock:
            return {
                'active_conversations': len(self.messages),
                'total_users': len(self.chat_history),
                'pending_timers': len(self.timers),
                'total_messages_in_history': sum(
                    len(hist) for hist in self.chat_history.values()
                )
            }


# Instancia global del almacenamiento
store = InMemoryStore()


# ============================================================================
# CLIENTES API
# ============================================================================

# Cliente OpenAI
openai_client = OpenAI(api_key=OPENAI_API_KEY)


# ============================================================================
# FUNCIONES DE PROCESAMIENTO DE MENSAJES - CORREGIDAS Y COMPLETAS
# ============================================================================

def get_media_base64(media_id: str) -> Optional[str]:
    """Descarga media de WhatsApp API y retorna base64"""
    try:
        # Paso 1: Obtener URL del media
        url = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{media_id}"
        headers = {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}"}
        
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        media_url = response.json().get('url')
        if not media_url:
            print(f"❌ No se obtuvo URL para media_id: {media_id}")
            return None
        
        # Paso 2: Descargar el archivo
        media_response = requests.get(media_url, headers=headers, timeout=30)
        media_response.raise_for_status()
        
        # Convertir a base64
        media_base64 = base64.b64encode(media_response.content).decode('utf-8')
        print(f"✅ Media descargado: {media_id} ({len(media_base64)} bytes)")
        
        return media_base64
    
    except Exception as e:
        print(f"❌ Error descargando media {media_id}: {str(e)}")
        return None


def process_message_content(message_type: str, content: str, media_id: Optional[str] = None) -> str:
    """
    Procesa el contenido de un mensaje según su tipo
    FUNCIÓN CRÍTICA AGREGADA
    """
    try:
        if message_type == 'text':
            return content
        
        elif message_type == 'audio':
            if media_id:
                audio_base64 = get_media_base64(media_id)
                if audio_base64:
                    return f"[Audio recibido - ID: {media_id}]"
            return "[Audio no disponible]"
        
        elif message_type == 'image':
            if media_id:
                image_base64 = get_media_base64(media_id)
                if image_base64:
                    caption = content if content else ""
                    return f"[Imagen recibida{': ' + caption if caption else ''}]"
            return "[Imagen no disponible]"
        
        elif message_type == 'sticker':
            return "[Sticker recibido]"
        
        elif message_type == 'document':
            return f"[Documento recibido: {content}]"
        
        elif message_type == 'video':
            return "[Video recibido]"
        
        elif message_type == 'location':
            return "[Ubicación compartida]"
        
        else:
            return f"[Mensaje tipo {message_type} no soportado]"
    
    except Exception as e:
        print(f"❌ Error procesando contenido: {str(e)}")
        return "[Error procesando mensaje]"


def generate_assistant_response(phone: str, combined_message: str) -> str:
    """
    Genera respuesta usando OpenAI GPT-4
    FUNCIÓN CRÍTICA AGREGADA
    """
    try:
        # Obtener historial
        history = store.get_history(phone, limit=10)
        last_conversation_time = store.get_last_conversation_time(phone)
        user_name = store.get_user_data(phone, 'name', 'Cliente')
        
        # Determinar si es primera interacción o conversación activa
        is_first_contact = len(history) == 0
        is_new_conversation = False
        
        if last_conversation_time:
            time_since_last = datetime.now() - last_conversation_time
            is_new_conversation = time_since_last > timedelta(hours=3)
        
        # Construir mensajes para OpenAI
        messages = [
            {
                "role": "system",
                "content": SYSTEM_PROMPT
            }
        ]
        
        # Agregar contexto de conversación previa
        if not is_first_contact and not is_new_conversation:
            for msg in history:
                messages.append({
                    "role": msg['role'],
                    "content": msg['content']
                })
        
        # Agregar mensaje actual
        messages.append({
            "role": "user",
            "content": combined_message
        })
        
        print(f"🤖 Generando respuesta para {phone} (mensajes en contexto: {len(messages)})")
        
        # Llamar a OpenAI
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            temperature=0.7,
            max_tokens=500
        )
        
        assistant_response = response.choices[0].message.content.strip()
        print(f"✅ Respuesta generada: {assistant_response[:100]}...")
        
        return assistant_response
    
    except Exception as e:
        print(f"❌ Error generando respuesta: {str(e)}")
        return "Disculpa, tuve un problema al procesar tu mensaje. Por favor intenta nuevamente en un momento."


def send_whatsapp_messages(phone: str, response_text: str):
    """
    Envía mensajes a WhatsApp divididos por saltos de línea dobles
    FUNCIÓN CRÍTICA AGREGADA
    """
    try:
        # Dividir respuesta en mensajes separados (por líneas dobles)
        messages = [msg.strip() for msg in response_text.split('\n\n') if msg.strip()]
        
        # Limitar a máximo 3 mensajes (según instrucciones del bot)
        messages = messages[:3]
        
        url = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{WHATSAPP_PHONE_NUMBER_ID}/messages"
        headers = {
            "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }
        
        print(f"📤 Enviando {len(messages)} mensaje(s) a {phone}")
        
        for i, message in enumerate(messages, 1):
            payload = {
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "text",
                "text": {"body": message}
            }
            
            response = requests.post(url, headers=headers, json=payload, timeout=10)
            response.raise_for_status()
            
            print(f"✅ Mensaje {i}/{len(messages)} enviado correctamente")
            
            # Delay entre mensajes
            if i < len(messages):
                time.sleep(MESSAGE_SEND_DELAY)
        
        return True
    
    except Exception as e:
        print(f"❌ Error enviando mensajes a WhatsApp: {str(e)}")
        if hasattr(e, 'response'):
            print(f"Response: {e.response.text}")
        return False


def process_accumulated_messages(phone: str):
    """
    Procesa todos los mensajes acumulados de un usuario y genera respuesta
    FUNCIÓN CRÍTICA AGREGADA - Esta es la que faltaba y causaba el problema principal
    """
    try:
        print(f"\n{'='*70}")
        print(f"🔄 Procesando mensajes acumulados para: {phone}")
        print(f"{'='*70}")
        
        # Obtener mensajes acumulados
        messages = store.get_messages(phone)
        
        if not messages:
            print(f"⚠️  No hay mensajes para procesar de {phone}")
            return
        
        print(f"📥 Mensajes acumulados: {len(messages)}")
        
        # Combinar mensajes
        combined_message = "\n".join(messages)
        print(f"📝 Mensaje combinado: {combined_message[:200]}...")
        
        # Limpiar cola
        store.clear_messages(phone)
        
        # Guardar en historial como usuario
        store.add_to_history(phone, 'user', combined_message)
        
        # Generar respuesta con OpenAI
        assistant_response = generate_assistant_response(phone, combined_message)
        
        # Guardar respuesta en historial
        store.add_to_history(phone, 'assistant', assistant_response)
        
        # Enviar por WhatsApp
        success = send_whatsapp_messages(phone, assistant_response)
        
        if success:
            print(f"✅ Proceso completado exitosamente para {phone}")
        else:
            print(f"⚠️  Respuesta generada pero falló el envío a {phone}")
        
        print(f"{'='*70}\n")
    
    except Exception as e:
        print(f"❌ Error procesando mensajes de {phone}: {str(e)}")
        import traceback
        traceback.print_exc()
    
    finally:
        # Limpiar timer
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
📞 Contacto:
+56978765400 (WhatsApp)

⏰ Horario de Atención:
Lunes a Viernes: 10:00 - 19:00
Sábados: 10:00 - 14:00
Domingos: Cerrado

3. Servicios
🔸 Microblading de Cejas
Técnica manual pelo a pelo que crea cejas naturales y definidas.
Precio: $120.000
Retoque (40 días después): $30.000
Duración: 1-2 años

Incluye:
- Diseño personalizado según tu rostro
- Primera sesión completa
- Retoque si es necesario (40 días después)

Cuidados post-procedimiento:
- No mojar las cejas 7 días
- Aplicar pomada cicatrizante 2 veces al día
- No exponerse al sol directo
- No usar maquillaje en la zona
- Evitar piscinas y saunas

🔸 Microlabial (Labios)
Técnica que realza el color natural y define el contorno de los labios.
Precio: $150.000
Retoque (40 días después): $55.000
Duración: 1-2 años

🔸 Delineado de Ojos
Delineado permanente que realza la mirada de forma natural.
Precio: $150.000
Retoque (40 días después): $40.000
Duración: 1-2 años

4. Packs Especiales
- Pack Microblading + Delineado: $240.000
  * Retoque microblading: $30.000
  * Retoque delineado: $40.000

- Pack Microblading + Microlabial: $245.000
  * Retoque microblading: $30.000
  * Retoque microlabial: $55.000

- Pack Delineado de ojos + Microlabial: $245.000
  * Retoque delineado: $40.000
  * Retoque microlabial: $55.000

- Pack Completo (Microblading + Microlabial + Delineado): $370.000
  * Retoque microblading: $30.000
  * Retoque microlabial: $55.000
  * Retoque delineado: $40.000

Nota: Los retoques se realizan 40 días después del procedimiento inicial si son necesarios.

📍 Ubicación:
Dirección: Calle Pailahuen 1933, Jardín Austral, Puerto Montt, Chile

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

🌐 Enlaces y Contacto
📸 Instagram Studio Gabrielle Natal: https://instagram.com/studiogabriellenatal

5. Estilo y Tono de Conversación
✅ Usa un tono cordial, profesional, cercano y cálido
✅ Llama "Querida" a las clientas ocasionalmente para mantener cercanía
✅ Usa emojis con moderación (máximo 3-4 por mensaje)
✅ REGLA CRÍTICA: MÁXIMO 3 MENSAJES POR RESPUESTA (ideal 1-2)
✅ Mantén respuestas concisas pero completas

Si solicitan hablar con Gabi, responde:
"Espera un momento por favor, apenas esté disponible entrará en contacto contigo."

❌ Prohibiciones Críticas
❌ No omitas el mensaje de presentación inicial cuando sea el primer contacto
❌ No confirmes citas directamente (solo Gabi puede hacerlo)
❌ No proporciones precios sin que los soliciten explícitamente
❌ No uses más de 3 mensajes por respuesta
"""


# ============================================================================
# API WEB (FLASK)
# ============================================================================

app = Flask(__name__)


@app.route('/webhook/whatsapp', methods=['GET', 'POST'])
def webhook_whatsapp():
    """Webhook para recibir mensajes de WhatsApp directamente de Meta"""
    
    if request.method == 'GET':
        # Verificación del webhook
        mode = request.args.get('hub.mode')
        token = request.args.get('hub.verify_token')
        challenge = request.args.get('hub.challenge')
        
        if mode == 'subscribe' and token == WHATSAPP_VERIFY_TOKEN:
            print("✅ Webhook verificado")
            return challenge, 200
        else:
            print("❌ Verificación fallida")
            return "Forbidden", 403
    
    if request.method == 'POST':
        try:
            data = request.json
            print("Payload recibido en webhook:", json.dumps(data, indent=2))
            
            if not data.get('entry'):
                print("No entry in payload")
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
                            
                            print(f"Mensaje recibido: phone={phone}, type={message_type}, content={content[:50]}, media_id={media_id}")
                            
                            # Guardar datos del usuario
                            store.set_user_data(phone, 'name', name)
                            
                            # Procesar contenido
                            processed_content = process_message_content(message_type, content, media_id)
                            print(f"Contenido procesado: {processed_content[:50]}")
                            
                            # Agregar a memoria
                            store.add_message(phone, processed_content)
                            
                            # Programar procesamiento
                            store.schedule_processing(phone, process_accumulated_messages)
                            print(f"Procesamiento programado para {phone}")
            
            return jsonify({"status": "queued"}), 200
        
        except Exception as e:
            print(f"❌ Error en webhook: {str(e)}")
            import traceback
            traceback.print_exc()
            return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/health', methods=['GET'])
def health():
    """Endpoint de salud"""
    stats = store.get_stats()
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        **stats
    }), 200


@app.route('/stats', methods=['GET'])
def stats():
    """Estadísticas del bot"""
    return jsonify(store.get_stats()), 200


@app.route('/history/<phone>', methods=['GET'])
def get_user_history(phone):
    """Obtiene el historial de un usuario"""
    limit = request.args.get('limit', 20, type=int)
    history = store.get_history(phone, limit=limit)
    
    # Convertir datetime a string para JSON
    for msg in history:
        msg['created_at'] = msg['created_at'].isoformat()
    
    return jsonify({
        "phone": phone,
        "history": history,
        "total_messages": len(history)
    }), 200


@app.route('/clear/<phone>', methods=['POST'])
def clear_user_data(phone):
    """Limpia los datos de un usuario"""
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
    print("=" * 70)
    print("🤖 Bot de WhatsApp - Studio Gabrielle Natal")
    print("=" * 70)
    print("✨ Versión 100% en Memoria con WhatsApp Business API Directa")
    print("✨ VERSIÓN CORREGIDA - Todas las funciones implementadas")
    print("=" * 70)
    
    # Detectar puerto de Render o usar 10000 por defecto
    port = int(os.getenv('PORT', 10000))
    
    print(f"Puerto: {port}")
    print(f"Webhook: http://localhost:{port}/webhook/whatsapp")
    print(f"Health: http://localhost:{port}/health")
    print(f"Stats: http://localhost:{port}/stats")
    print("=" * 70)
    print(f"📝 Credenciales:")
    print(f"   ✅ OpenAI (configurada)")
    print(f"   ✅ WhatsApp Token: {WHATSAPP_ACCESS_TOKEN[:20]}...")
    print(f"   ✅ Phone Number ID: {WHATSAPP_PHONE_NUMBER_ID}")
    print(f"   ✅ Verify Token: {WHATSAPP_VERIFY_TOKEN}")
    print("=" * 70)
    
    # Verificar API key de OpenAI
    if OPENAI_API_KEY.startswith("sk-"):
        print("✅ API key de OpenAI configurada")
    else:
        print("⚠️  ADVERTENCIA: API key de OpenAI puede no ser válida")
    
    print("=" * 70)
    print(f"🚀 Iniciando servidor en puerto {port}...")
    app.run(host='0.0.0.0', port=port, debug=False)
