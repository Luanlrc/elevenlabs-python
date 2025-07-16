import os
import json
import base64
import asyncio
import websockets
import aiohttp
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.websockets import WebSocketDisconnect
from twilio.twiml.voice_response import VoiceResponse, Connect, Say, Stream
from dotenv import load_dotenv
from elevenlabs import stream
from elevenlabs.client import ElevenLabs
from convert_mp3_bytes_to_law_base64 import convert_mp3_bytes_to_g711ulaw_base64
from prompt import PROMPT

load_dotenv()

# Configuração do ElevenLabs
elevenlabs = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))

# Configuration
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
PORT = int(os.getenv('PORT', 5050))
SYSTEM_MESSAGE = PROMPT

VOICE = 'alloy'
LOG_EVENT_TYPES = [
    'error', 'response.content.done', 'rate_limits.updated',
    'response.done', 'input_audio_buffer.committed',
    'input_audio_buffer.speech_stopped', 'input_audio_buffer.speech_started',
    'session.created'
]
SHOW_TIMING_MATH = False
ELEVENLABS_VOICE_ID = "Jvj2FoZHFrWICKQxQXqy"

# Logging configuration
import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

def log_info(emoji, message):
    """Log info message with emoji"""
    logger.info(f"{emoji} {message}")

def log_error(emoji, message):
    """Log error message with emoji"""
    logger.error(f"{emoji} {message}")

def log_debug(emoji, message):
    """Log debug message with emoji"""
    logger.debug(f"{emoji} {message}")

app = FastAPI()

if not OPENAI_API_KEY:
    raise ValueError('Missing the OpenAI API key. Please set it in the .env file.')

# Função otimizada para streaming real de áudio com ElevenLabs 2.7.1
async def stream_audio_to_twilio(texto, websocket, stream_sid, voz_escolhida=ELEVENLABS_VOICE_ID):
    """Stream audio directly from ElevenLabs to Twilio using real streaming"""
    try:
        log_info("🎵", "Iniciando streaming real de áudio com ElevenLabs...")
        
        audio_stream = elevenlabs.text_to_speech.stream(
            text=texto,
            voice_id=voz_escolhida,
            model_id="eleven_multilingual_v2",
            voice_settings={
                "stability": 0.71,  # Aumentado para mais estabilidade
                "similarity_boost": 0.75,  # Aumentado para melhor qualidade
                "style": 0.65,
                "use_speaker_boost": True
            },
            optimize_streaming_latency=2,  # Reduzido para 2 (era 3)
            output_format="mp3_44100_128"
        )
        
        buffer_chunks = []
        chunk_size = 0
        target_chunk_size = 16384  # Aumentado o tamanho do buffer (era 8192)
        
        for chunk in audio_stream:
            if isinstance(chunk, bytes):
                buffer_chunks.append(chunk)
                chunk_size += len(chunk)
                
                # Envia quando atingir o tamanho alvo ou acumular chunks suficientes
                if chunk_size >= target_chunk_size:
                    combined_chunk = b''.join(buffer_chunks)
                    audio_payload = convert_mp3_bytes_to_g711ulaw_base64(combined_chunk)
                    
                    await websocket.send_json({
                        "event": "media",
                        "streamSid": stream_sid,
                        "media": {"payload": audio_payload}
                    })
                    
                    # Adiciona um pequeno delay para sincronização
                    await asyncio.sleep(0.02)  # 20ms de delay para suavizar a transição
                    
                    buffer_chunks = []
                    chunk_size = 0
        
        # Envia os chunks restantes
        if buffer_chunks:
            combined_chunk = b''.join(buffer_chunks)
            audio_payload = convert_mp3_bytes_to_g711ulaw_base64(combined_chunk)
            await websocket.send_json({
                "event": "media",
                "streamSid": stream_sid,
                "media": {"payload": audio_payload}
            })
        
        log_info("✅", "Streaming real de áudio concluído!")
        
    except Exception as e:
        log_error("💥", f"Erro no streaming real de áudio: {e}")
        await fallback_audio_generation(texto, websocket, stream_sid, voz_escolhida)

# Função de fallback usando método tradicional
async def fallback_audio_generation(texto, websocket, stream_sid, voz_escolhida=ELEVENLABS_VOICE_ID):
    """Fallback method using traditional ElevenLabs generation"""
    try:
        log_info("🔄", "Usando método tradicional de geração de áudio...")
        
        # Gera o áudio com ElevenLabs usando a nova API
        audio_bytes = elevenlabs.text_to_speech.convert(
            text=texto,
            voice_id=voz_escolhida,
            model_id="eleven_multilingual_v2",
            voice_settings={
                "stability": 0.6,
                "similarity_boost": 0.65,
                "style": 0.65,
                "use_speaker_boost": True
            }
        )

        # Converte MP3 para G711 μ-law base64
        audio_payload = convert_mp3_bytes_to_g711ulaw_base64(audio_bytes)

        # Envia áudio para o Twilio
        await websocket.send_json({
            "event": "media",
            "streamSid": stream_sid,
            "media": {"payload": audio_payload}
        })
        
        log_info("✅", "Áudio enviado com sucesso (método tradicional)!")
        
    except Exception as e:
        log_error("💥", f"Erro no fallback de áudio: {e}")

# Função legada mantida para compatibilidade (usando nova API)
def gerar_audio_com_elevenlabs(texto, voz_escolhida=ELEVENLABS_VOICE_ID):
    response = elevenlabs.text_to_speech.convert(
        text=texto,
        voice_id=voz_escolhida,
        model_id="eleven_multilingual_v2",
        voice_settings={
            "stability": 0.6,
            "similarity_boost": 0.65,
            "style": 0.65,
            "use_speaker_boost": True
        }
    )
    return response

@app.get("/", response_class=JSONResponse)
async def index_page():
    return {"message": "Twilio Media Stream Server is running!"}

@app.api_route("/incoming-call", methods=["GET", "POST"])
async def handle_incoming_call(request: Request):
    """Handle incoming call and return TwiML response to connect to Media Stream."""
    response = VoiceResponse()
    # <Say> punctuation to improve text-to-speech flow
    response.say("Please wait while we connect your call to the A. I. voice assistant, powered by Twilio and the Open-A.I. Realtime API")
    response.pause(length=1)
    response.say("O.K. you can start talking!")
    host = request.url.hostname
    connect = Connect()
    # Configuração ajustada para melhor qualidade de áudio
    connect.stream(
        url=f'wss://{host}/media-stream',
        track='inbound_track',  # Captura apenas áudio do usuário
        max_duration=60,  # Aumentado para 60 segundos
        max_connections=1
    )
    response.append(connect)
    return HTMLResponse(content=str(response), media_type="application/xml")

@app.websocket("/media-stream")
async def handle_media_stream(websocket: WebSocket):
    """Handle WebSocket connections between Twilio and OpenAI with optimized streaming."""
    log_info("🔌", "Cliente conectado ao WebSocket")
    await websocket.accept()

    async with websockets.connect(
        'wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-10-01',
        extra_headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "OpenAI-Beta": "realtime=v1"
        }
    ) as openai_ws:
        log_info("", "Conectado à API OpenAI Realtime")
        await initialize_session(openai_ws)

        # Connection specific state
        stream_sid = None
        latest_media_timestamp = 0
        last_assistant_item = None
        mark_queue = []
        response_start_timestamp_twilio = None
        
        # Estados para controle de fala do usuário
        user_speaking = False
        last_speech_event_time = 0
        speech_debounce_delay = 1.0  # 1 segundo de delay entre detecções
        
        async def receive_from_twilio():
            """Receive audio data from Twilio and send it to the OpenAI Realtime API."""
            nonlocal stream_sid, latest_media_timestamp
            try:
                async for message in websocket.iter_text():
                    data = json.loads(message)
                    log_debug("📨", f"Evento recebido do Twilio: {data['event']}")
                    
                    if data['event'] == 'media' and openai_ws.open:
                        latest_media_timestamp = int(data['media']['timestamp'])
                        audio_append = {
                            "type": "input_audio_buffer.append",
                            "audio": data['media']['payload']
                        }
                        await openai_ws.send(json.dumps(audio_append))
                        log_debug("🎤", f"Áudio recebido do Twilio (timestamp: {latest_media_timestamp}ms)")
                    elif data['event'] == 'start':
                        stream_sid = data['start']['streamSid']
                        log_info("📞", f"Stream iniciado: {stream_sid}")
                        response_start_timestamp_twilio = None
                        latest_media_timestamp = 0
                        last_assistant_item = None
                    elif data['event'] == 'mark':
                        if mark_queue:
                            mark_queue.pop(0)
                            log_debug("✅", "Mark processado")
                    elif data['event'] == 'stop':
                        log_info("🛑", "Stream parado")
                    elif data['event'] == 'error':
                        log_error("❌", f"Erro no stream Twilio: {data}")
            except WebSocketDisconnect:
                log_info("🔌", "Cliente desconectado")
                if openai_ws.open:
                    await openai_ws.close()
            except Exception as e:
                log_error("💥", f"Erro em receive_from_twilio: {e}")

        async def send_to_twilio():
            """Receive events from the OpenAI Realtime API, send audio back to Twilio using ElevenLabs Streaming."""
            nonlocal stream_sid, last_assistant_item, response_start_timestamp_twilio
            nonlocal user_speaking, last_speech_event_time
            
            buffer_texto = ""
            import time

            try:
                async for openai_message in openai_ws:
                    response = json.loads(openai_message)
                    current_time = time.time()

                    # Log apenas eventos importantes
                    if response['type'] == 'session.created':
                        log_info("🎭", "Sessão OpenAI criada")
                    
                    # Captura a transcrição do áudio do usuário
                    elif response['type'] == 'response.create':
                        if 'input' in response and 'content' in response['input']:
                            for content in response['input']['content']:
                                if content.get('type') == 'text' and 'text' in content:
                                    log_info("👤", f"Usuário disse: '{content['text']}'")
            
                    elif response['type'] == 'input_audio_buffer.speech_started':
                        # Implementa debounce para evitar múltiplas detecções
                        if not user_speaking and (current_time - last_speech_event_time) > speech_debounce_delay:
                            user_speaking = True
                            last_speech_event_time = current_time
                            log_info("🎤", "Cliente começou a falar")
                    
                    elif response['type'] == 'input_audio_buffer.speech_stopped':
                        # Só processa se o usuário estava realmente falando
                        if user_speaking:
                            user_speaking = False
                            last_speech_event_time = current_time
                            log_info("🔇", "Cliente parou de falar")
                    
                    elif response['type'] == 'input_audio_buffer.committed':
                        # Só loga se houve uma sessão de fala válida
                        if not user_speaking and (current_time - last_speech_event_time) < 2.0:
                            log_info("📝", "Áudio do cliente processado")
                    
                    elif response['type'] == 'rate_limits.updated':
                        remaining = response.get('rate_limits', [{}])[0].get('remaining', 'N/A')
                        log_debug("⚡", f"Rate limits atualizados - Restante: {remaining}")
                    elif response['type'] == 'error':
                        log_error("❌", f"Erro da API OpenAI: {response}")

                    # Acumula texto parcial vindo do GPT
                    if response.get('type') == 'response.text.delta':
                        buffer_texto += response.get('delta', "")

                    # Quando a resposta for concluída, use streaming real do ElevenLabs
                    elif response.get('type') == 'response.done':
                        # Verifica se há conteúdo de áudio na resposta
                        if response.get('response', {}).get('output'):
                            for output_item in response['response']['output']:
                                if output_item.get('content'):
                                    for content in output_item['content']:
                                        if content.get('type') == 'audio' and content.get('transcript'):
                                            buffer_texto = content['transcript']
                                            break
                        
                        if buffer_texto:
                            log_info("💬", f"IA responde: '{buffer_texto}'")

                            # Usa streaming real do ElevenLabs para reduzir latência
                            await stream_audio_to_twilio(buffer_texto, websocket, stream_sid)

                            if response_start_timestamp_twilio is None:
                                response_start_timestamp_twilio = latest_media_timestamp
                                if SHOW_TIMING_MATH:
                                    log_debug("⏱️", f"Timestamp inicial definido: {response_start_timestamp_twilio}ms")

                            if response.get('item_id'):
                                last_assistant_item = response['item_id']

                            await send_mark(websocket, stream_sid)
                            log_info("✅", "Áudio da IA enviado!")

                            # Limpa o buffer de texto após envio
                            buffer_texto = ""

                    # Interrupção se o usuário começar a falar
                    elif response.get('type') == 'input_audio_buffer.speech_started':
                        if last_assistant_item and user_speaking:
                            log_info("🔄", f"Cliente interrompeu a IA (ID: {last_assistant_item})")
                            await handle_speech_started_event()

            except Exception as e:
                log_error("💥", f"Erro em send_to_twilio: {e}")

        async def handle_speech_started_event():
            """Handle interruption when the caller's speech starts."""
            nonlocal response_start_timestamp_twilio, last_assistant_item
            log_info("🔄", "Processando interrupção de fala")
            if mark_queue and response_start_timestamp_twilio is not None:
                elapsed_time = latest_media_timestamp - response_start_timestamp_twilio
                if SHOW_TIMING_MATH:
                    log_debug("⏱️", f"Tempo decorrido para truncamento: {elapsed_time}ms")

                if last_assistant_item:
                    if SHOW_TIMING_MATH:
                        log_debug("✂️", f"Truncando item ID: {last_assistant_item}")

                    truncate_event = {
                        "type": "conversation.item.truncate",
                        "item_id": last_assistant_item,
                        "content_index": 0,
                        "audio_end_ms": elapsed_time
                    }
                    await openai_ws.send(json.dumps(truncate_event))

                await websocket.send_json({
                    "event": "clear",
                    "streamSid": stream_sid
                })

                mark_queue.clear()
                last_assistant_item = None
                response_start_timestamp_twilio = None
                log_info("🧹", "Estado limpo após interrupção")

        async def send_mark(connection, stream_sid):
            if stream_sid:
                mark_event = {
                    "event": "mark",
                    "streamSid": stream_sid,
                    "mark": {"name": "responsePart"}
                }
                await connection.send_json(mark_event)
                mark_queue.append('responsePart')
                log_debug("📍", "Mark enviado")

        await asyncio.gather(receive_from_twilio(), send_to_twilio())

async def send_initial_conversation_item(openai_ws):
    """Send initial conversation item if AI talks first."""
    log_info("🎬", "Enviando mensagem inicial da IA")
    initial_conversation_item = {
        "type": "conversation.item.create",
        "item": {
            "type": "message",
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": "Alo quem fala?"
                }
            ]
        }
    }
    await openai_ws.send(json.dumps(initial_conversation_item))
    await openai_ws.send(json.dumps({"type": "response.create"}))
    log_info("✅", "Mensagem inicial enviada")

async def initialize_session(openai_ws):
    """Control initial session with OpenAI."""
    log_info("⚙️", "Inicializando sessão OpenAI...")
    session_update = {
        "type": "session.update",
        "session": {
            "turn_detection": {
                "type": "server_vad",
                "threshold": 0.6,  # Aumenta threshold para reduzir falsos positivos
                "prefix_padding_ms": 300,  # Padding antes da fala
                "silence_duration_ms": 500  # Tempo de silêncio para detectar fim da fala
            },
            "input_audio_format": "g711_ulaw",
            "output_audio_format": "g711_ulaw",
            "voice": VOICE,
            "instructions": SYSTEM_MESSAGE,
            "modalities": ["text", "audio"],
            "temperature": 0.8,
            "model": "gpt-4o",
        }
    }
    log_debug("📋", f"Configuração da sessão: {json.dumps(session_update)}")
    await openai_ws.send(json.dumps(session_update))

    # Descomente a próxima linha para que a IA fale primeiro
    await send_initial_conversation_item(openai_ws)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
