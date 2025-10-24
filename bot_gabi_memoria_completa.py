"""
Bot de WhatsApp para Studio Gabrielle Natal
Versión 100% en Memoria (Sin Redis, Sin PostgreSQL)
Integración directa con WhatsApp Business API de Meta
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

# OpenAI (del JSON: id "PGVzTzuh2HAoTTyS")
# Esta es la ÚNICA que debes reemplazar con tu API key real
OPENAI_API_KEY = "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"

# WhatsApp Business API
WHATSAPP_ACCESS_TOKEN = "EAAKFvnVI8H8BP7ZCGpS2bpdtZCOcWZCkCp5P1m3vuRmZBDxokbcfldJxiRw2sDFC3IH5NySFX187jZCoJnqrhM1zMK6Yk0P91jqxGJXUF6iQn1ZAXMuCbXHPBgAFnTiUTv0ZC7TQrTJPwFceZCC97jkUA3DfNsLfQAjyCC0wBy84RgRXV5PZAvlOkHi8FHu1h7GvJ9BpaT5zoUxIWu2FqPNsJgk2aF9cSiO0ZBDSJZC8DZC2Ysv0dL2FVrHa48TvrQZDZD"
WHATSAPP_PHONE_NUMBER_ID = "878161422037681"  # Identificador del número de teléfono (de la imagen)
WHATSAPP_BUSINESS_ACCOUNT_ID = "2318712901907194"  # Identificador de la cuenta de WhatsApp Business (de la imagen, opcional pero agregado para referencia)
WHATSAPP_API_VERSION = "v20.0"  # Versión actual (verifica en docs de Meta si cambió)
WHATSAPP_VERIFY_TOKEN = "TU_VERIFY_TOKEN_PERSONALIZADO"  # Define uno seguro (ej: cadena aleatoria de 32 chars), configúralo en .env y en el dashboard de Meta > WhatsApp > Configuration > Webhook

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
        # Estructura: {phone: deque([{role, content, timestamp}, ...])}
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
        """
        Agrega un mensaje al historial conversacional
        Reemplaza: INSERT INTO n8n_chat_historial_bot
        """
        with self.lock:
            self.chat_history[phone].append({
                'role': role,
                'content': content,
                'created_at': datetime.now()
            })
    
    def get_history(self, phone: str, limit: int = MAX_HISTORY_MESSAGES) -> List[Dict]:
        """
        Obtiene el historial de conversación
        Reemplaza: SELECT FROM n8n_chat_historial_bot
        """
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
# FUNCIONES DE PROCESAMIENTO DE MENSAJES
# ============================================================================

def get_media_base64(media_id: str) -> Optional[str]:
    """Descarga media de WhatsApp API y retorna base64"""
    try:
        # Obtener URL del media
        url = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{media_id}"
        headers = {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}"}
        response = requests.get(url, headers=headers)
        media_url = response.json().get('url')
        
        if not media_url:
            return None
        
        # Descargar binary
        response = requests.get(media_url, headers=headers)
        if response.status_code != 200:
            return None
        
        # Convertir a base64
        return base64.b64encode(response.content).decode('utf-8')
    except Exception as e:
        print(f"Error descargando media {media_id}: {e}")
        return None

def transcribe_audio(audio_base64: str) -> str:
    """Transcribe audio usando OpenAI Whisper"""
    try:
        audio_data = base64.b64decode(audio_base64)
        audio_file = BytesIO(audio_data)
        audio_file.name = "audio.ogg"
        
        transcript = openai_client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file
        )
        
        return transcript.text
    except Exception as e:
        print(f"Error transcribiendo audio: {e}")
        return "Audio enviado (no se pudo transcribir)"


def analyze_image(image_base64: str, is_sticker: bool = False) -> str:
    """Analiza imagen usando GPT-4o-mini"""
    try:
        prefix = "Sticker enviado" if is_sticker else "Imagen enviada"
        
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"Eres un asistente virtual, analiza esta imagen. Tu respuesta debe comenzar con: '{prefix} ____'"
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_base64}"
                            }
                        }
                    ]
                }
            ],
            max_tokens=300
        )
        
        return response.choices[0].message.content
    except Exception as e:
        print(f"Error analizando imagen: {e}")
        prefix = "Sticker" if is_sticker else "Imagen"
        return f"{prefix} enviado (no se pudo analizar)"


def process_message_content(message_type: str, content: str, media_id: str = None) -> str:
    """Procesa el contenido del mensaje según su tipo"""
    
    base64_data = get_media_base64(media_id) if media_id else None
    
    if message_type == "text":
        return content
    
    elif message_type == "audio" and base64_data:
        return transcribe_audio(base64_data)
    
    elif message_type == "image" and base64_data:
        return analyze_image(base64_data, is_sticker=False)
    
    elif message_type == "sticker" and base64_data:
        return analyze_image(base64_data, is_sticker=True)
    
    else:
        return content or "Mensaje no soportado"


# ============================================================================
# AGENTE IA CON OPENAI
# ============================================================================

def get_context_message(phone: str) -> str:
    """Genera mensaje de contexto sobre la conversación"""
    last_conv_time = store.get_last_conversation_time(phone)
    
    if not last_conv_time:
        return "Nueva conversación"
    
    now = datetime.now()
    time_diff = now - last_conv_time
    minutes = int(time_diff.total_seconds() / 60)
    
    if minutes > 60:
        hours = int(minutes / 60)
        return f"Hace {hours} horas que el usuario no escribía."
    elif minutes > 5:
        return f"El usuario vuelve luego de {minutes} minutos."
    else:
        return "El usuario continúa la conversación activa."


def query_ai_agent(phone: str, user_message: str, name: str = "") -> str:
    """Consulta al agente IA (GPT-4)"""
    
    # Obtener historial
    history = store.get_history(phone, limit=10)
    context_msg = get_context_message(phone)
    
    # Construir mensajes para OpenAI
    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT
        }
    ]
    
    # Agregar historial previo
    for msg in history:
        messages.append({
            "role": msg['role'],
            "content": msg['content']
        })
    
    # Agregar mensaje actual con contexto
    messages.append({
        "role": "user",
        "content": f"""contexto: {context_msg}
mensaje_del_cliente: {user_message}
telefono_del_cliente: {phone}
dia actual: {datetime.now().isoformat()}
dia de la semana: {datetime.now().strftime('%A')}"""
    })
    
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4",
            messages=messages,
            temperature=0.1,
            max_tokens=1000
        )
        
        ai_response = response.choices[0].message.content
        
        # Guardar en historial
        store.add_to_history(phone, "user", user_message)
        store.add_to_history(phone, "assistant", ai_response)
        
        return ai_response
        
    except Exception as e:
        print(f"Error consultando IA: {e}")
        return "Disculpa Querida, tuve un problema técnico. ¿Podrías repetir tu mensaje?"


# ============================================================================
# FORMATEO Y ENVÍO DE MENSAJES
# ============================================================================

def format_message_parts(message: str) -> Dict:
    """Divide el mensaje en partes usando GPT-4o-mini"""
    
    system_prompt = """Tu función principal consiste en crear un JSON que contenga las diferentes partes importantes del mensaje que vayas a recibir

Debes seguir la siguiente estructura de JSON:
{
  "response": {
    "part_1": "Responde con la primera parte de la respuesta.",
    "part_2": "Responde con la segunda parte de la respuesta.",
    "part_3": "Responde con la tercera parte de la respuesta (opcional).",
    "part_4": "Responde con la cuarta parte de la respuesta (opcional).",
    "part_5": "Responde con la quinta parte de la respuesta (opcional)."
  }
}

Debes dividir los mensajes de una manera que suene bien. Analiza qué tan largo es el mensaje y divídelo en las partes necesarias:
- Si el mensaje es breve divídelo en 1 o 2 partes
- Si el mensaje es extenso divídelo en 3 o 4 partes

Las partes que dividas deben tener sentido, no dividas por hacerlo. Piensa 2 veces antes de hacerlo."""
    
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Respuesta a formatear:\n{message}"}
            ],
            temperature=0,
            response_format={"type": "json_object"}
        )
        
        result = json.loads(response.choices[0].message.content)
        return result.get("response", {"part_1": message})
        
    except Exception as e:
        print(f"Error formateando mensaje: {e}")
        return {"part_1": message}


def send_whatsapp_message(phone: str, message: str):
    """Envía mensaje por WhatsApp usando API directa"""
    try:
        url = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{WHATSAPP_PHONE_NUMBER_ID}/messages"
        
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": phone,
            "type": "text",
            "text": {"preview_url": False, "body": message}
        }
        
        headers = {
            "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
            "Content-Type": "application/json"
        }
        
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        if response.status_code != 200:
            print(f"Error enviando mensaje: {response.text}")
        return response.status_code == 200
        
    except Exception as e:
        print(f"Error enviando mensaje WhatsApp: {e}")
        return False


async def send_messages_with_delay(phone: str, parts: Dict):
    """Envía múltiples partes del mensaje con delay"""
    
    # Recopilar partes no vacías
    message_parts = []
    for i in range(1, 6):
        part_key = f"part_{i}"
        if part_key in parts and parts[part_key]:
            message_parts.append(parts[part_key])
    
    if not message_parts:
        return
    
    # Enviar parte por parte con delay
    for i, part in enumerate(message_parts):
        if i > 0:
            await asyncio.sleep(MESSAGE_SEND_DELAY)
        
        send_whatsapp_message(phone, part)


# ============================================================================
# PROCESAMIENTO PRINCIPAL
# ============================================================================

def process_accumulated_messages(phone: str):
    """Procesa todos los mensajes acumulados de un usuario"""
    
    # Obtener mensajes acumulados
    messages = store.get_messages(phone)
    
    if not messages:
        return
    
    # Unir todos los mensajes
    combined_message = "\n".join(messages)
    
    print(f"📨 Procesando mensajes de {phone}: {combined_message[:100]}...")
    
    # Consultar IA
    ai_response = query_ai_agent(phone, combined_message)
    
    # Formatear respuesta en partes
    formatted_parts = format_message_parts(ai_response)
    
    # Enviar mensajes
    asyncio.run(send_messages_with_delay(phone, formatted_parts))
    
    # Limpiar mensajes procesados
    store.clear_messages(phone)
    store.cancel_timer(phone)


# ============================================================================
# SISTEMA PROMPT DEL AGENTE
# ============================================================================

SYSTEM_PROMPT = """🧠 Prompt para Agente: Asistente Especializada en Micropigmentación

⚠️ REGLA CRÍTICA PRIORITARIA - PRIMER MENSAJE
ESTA ES LA REGLA MÁS IMPORTANTE Y DEBE EJECUTARSE SIEMPRE PRIMERO:
Cuando recibas el PRIMER MENSAJE de un cliente (sin importar qué diga, pregunte o cómo se comunique), DEBES RESPONDER ÚNICAMENTE CON ESTE MENSAJE EXACTO:

¡Hola! Soy Delinea, la asistente virtual de Gabi ✨
Estoy aquí para ayudarte con tus consultas sobre nuestros servicios, entregarte los valores y toda la información que necesites 💕
¿En qué puedo ayudarte hoy?

IMPORTANTE:
❌ NO respondas la pregunta del cliente en el primer mensaje
❌ NO agregues información adicional
❌ NO modifiques este texto
❌ NO omitas este mensaje bajo ninguna circunstancia
✅ SOLO envía este mensaje exacto en tu primera interacción

Después del segundo mensaje del cliente, recién ahí comenzarás a responder sus consultas según las instrucciones de este prompt.

1. Tu Rol y Contexto
Rol: Eres Delinea, la asistente virtual de Gabi del Studio Gabrielle Natal, especializada en micropigmentación profesional y servicios de belleza.

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
            print("Payload recibido en webhook:", json.dumps(data, indent=2))  # Log del payload completo para debug
            
            if not data.get('entry'):
                print("No entry in payload")
                return jsonify({"status": "ignored"}), 200
            
            for entry in data['entry']:
                for change in entry.get('changes', []):
                    value = change.get('value', {})
                    if value.get('messages'):
                        for msg in value['messages']:
                            phone = msg['from']  # Número del remitente
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
    if OPENAI_API_KEY == "PEGA_TU_API_KEY_DE_OPENAI_AQUI":
        print("⚠️  ADVERTENCIA: Necesitas configurar tu API key de OpenAI")
        print("   Edita el archivo .env o el código y agrega tu API key")
        print("   Obtén tu API key en: https://platform.openai.com/api-keys")
        print("=" * 70)
    else:
        print("✅ API key de OpenAI configurada")
        print("=" * 70)
    
    if WHATSAPP_PHONE_NUMBER_ID == "TU_PHONE_NUMBER_ID_AQUI":
        print("⚠️  ADVERTENCIA: Configura WHATSAPP_PHONE_NUMBER_ID en .env")
        print("=" * 70)
    
    if WHATSAPP_VERIFY_TOKEN == "TU_VERIFY_TOKEN_PERSONALIZADO":
        print("⚠️  ADVERTENCIA: Configura WHATSAPP_VERIFY_TOKEN en .env y en el dashboard de Meta")
        print("=" * 70)
    
    print(f"🚀 Iniciando servidor en puerto {port}...")
    app.run(host='0.0.0.0', port=port, debug=False)
