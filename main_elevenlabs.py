import os
import json
import base64
import asyncio
import websockets
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.websockets import WebSocketDisconnect
from twilio.twiml.voice_response import VoiceResponse, Connect, Say, Stream
from dotenv import load_dotenv
from elevenlabs import ElevenLabs
from convert_mp3_bytes_to_law_base64 import convert_mp3_bytes_to_g711ulaw_base64
from prompt import PROMPT

load_dotenv()

client = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))

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

def gerar_audio_com_elevenlabs(texto, voz_escolhida=ELEVENLABS_VOICE_ID):
    response = client.text_to_speech.convert(
        voice_id=voz_escolhida,
        model_id='eleven_multilingual_v2',
        text=texto,
        voice_settings={
            "stability": 0.6,
            "similarity_boost": 0.65,
            "style": 0.65,
            "use_speaker_boost": True
        }
    )
    audio_bytes = b''.join(response) if hasattr(response, '__iter__') else response
    return audio_bytes


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
    connect.stream(url=f'wss://{host}/media-stream')
    response.append(connect)
    return HTMLResponse(content=str(response), media_type="application/xml")

@app.websocket("/media-stream")
async def handle_media_stream(websocket: WebSocket):
    """Handle WebSocket connections between Twilio and OpenAI."""
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
        
        async def receive_from_twilio():
            """Receive audio data from Twilio and send it to the OpenAI Realtime API."""
            nonlocal stream_sid, latest_media_timestamp
            try:
                async for message in websocket.iter_text():
                    data = json.loads(message)
                    if data['event'] == 'media' and openai_ws.open:
                        latest_media_timestamp = int(data['media']['timestamp'])
                        audio_append = {
                            "type": "input_audio_buffer.append",
                            "audio": data['media']['payload']
                        }
                        await openai_ws.send(json.dumps(audio_append))
                        log_debug("", f"Áudio recebido do Twilio (timestamp: {latest_media_timestamp}ms)")
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
            except WebSocketDisconnect:
                log_info("🔌", "Cliente desconectado")
                if openai_ws.open:
                    await openai_ws.close()

        async def send_to_twilio():
            """Receive events from the OpenAI Realtime API, send audio back to Twilio using ElevenLabs."""
            nonlocal stream_sid, last_assistant_item, response_start_timestamp_twilio
            buffer_texto = ""

            try:
                async for openai_message in openai_ws:
                    response = json.loads(openai_message)

                    # Log apenas eventos importantes
                    if response['type'] == 'session.created':
                        log_info("🎭", "Sessão OpenAI criada")
                    elif response['type'] == 'input_audio_buffer.speech_started':
                        log_info("", "Usuário começou a falar")
                    elif response['type'] == 'input_audio_buffer.speech_stopped':
                        log_info("🔇", "Usuário parou de falar")
                    elif response['type'] == 'input_audio_buffer.committed':
                        log_info("📝", "Áudio do usuário processado")
                    elif response['type'] == 'rate_limits.updated':
                        remaining = response.get('rate_limits', [{}])[0].get('remaining', 'N/A')
                        log_debug("⚡", f"Rate limits atualizados - Restante: {remaining}")
                    elif response['type'] == 'error':
                        log_error("❌", f"Erro da API OpenAI: {response}")

                    # Acumula texto parcial vindo do GPT
                    if response.get('type') == 'response.text.delta':
                        buffer_texto += response.get('delta', "")

                    # Quando a resposta for concluída, gere o áudio com ElevenLabs
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
                            log_info("", f"Texto recebido do GPT: '{buffer_texto}'")

                            # Gera o áudio com ElevenLabs
                            log_info("🎵", "Gerando áudio com ElevenLabs...")
                            audio_bytes = gerar_audio_com_elevenlabs(buffer_texto)

                            # Converte MP3 para G711 μ-law base64
                            log_info("🔄", "Convertendo áudio para G711 μ-law...")
                            audio_payload = convert_mp3_bytes_to_g711ulaw_base64(audio_bytes)

                            # Envia áudio para o Twilio
                            log_info("📤", "Enviando áudio para Twilio...")
                            await websocket.send_json({
                                "event": "media",
                                "streamSid": stream_sid,
                                "media": {"payload": audio_payload}
                            })

                            if response_start_timestamp_twilio is None:
                                response_start_timestamp_twilio = latest_media_timestamp
                                if SHOW_TIMING_MATH:
                                    log_debug("⏱️", f"Timestamp inicial definido: {response_start_timestamp_twilio}ms")

                            if response.get('item_id'):
                                last_assistant_item = response['item_id']

                            await send_mark(websocket, stream_sid)
                            log_info("✅", "Áudio enviado com sucesso!")

                            # Limpa o buffer de texto após envio
                            buffer_texto = ""

                    # Interrupção se o usuário começar a falar
                    elif response.get('type') == 'input_audio_buffer.speech_started':
                        if last_assistant_item:
                            log_info("🔄", f"Interrompendo resposta (ID: {last_assistant_item})")
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
                    "text": "Alo quem fala? Voce quer saber sobre o condomínio?"
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
            "turn_detection": {"type": "server_vad"},
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
    #await send_initial_conversation_item(openai_ws)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
