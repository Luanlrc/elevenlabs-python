from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from twilio.twiml.voice_response import VoiceResponse, Connect, Start, Say
from elevenlabs import ElevenLabs
import uvicorn
import logging
import json
import base64
from typing import Dict
import asyncio
import os
from dotenv import load_dotenv
import time
import websockets
from convert_mp3_bytes_to_law_base64 import convert_mp3_bytes_to_g711ulaw_base64

# Carrega variáveis de ambiente
load_dotenv()

# Configurações
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
VOICE_ID = os.getenv("VOICE_ID")

# Inicializa o cliente ElevenLabs
elevenlabs_client = ElevenLabs(api_key=ELEVENLABS_API_KEY)

# Configuração de logging
logging.basicConfig(
    level=logging.INFO,  # Mudando para INFO por padrão
    format='%(asctime)s | %(levelname)-7s | %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

# Desabilita logs desnecessários
logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)
logging.getLogger("twilio").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)

def log_separator(message=""):
    """Cria uma linha separadora nos logs"""
    logger.info("="*30 + f" {message} " + "="*30 if message else "="*70)

def log_debug(emoji, message):
    """Log debug com emoji"""
    if logger.level <= logging.DEBUG:
        logger.debug(f"{emoji} {message}")

def log_info(emoji, message):
    """Log info com emoji"""
    logger.info(f"{emoji} {message}")

def log_warning(emoji, message):
    """Log warning com emoji"""
    logger.warning(f"{emoji} {message}")

def log_error(emoji, message):
    """Log error com emoji"""
    logger.error(f"{emoji} {message}")

app = FastAPI()

# Configuração CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Armazena as conexões ativas
connections: Dict[str, WebSocket] = {}

# URL do WebSocket
WEBSOCKET_URL = os.getenv("WEBSOCKET_URL")
BASE_URL = os.getenv("BASE_URL")

@app.get("/incoming-call")
@app.post("/incoming-call")
async def handle_incoming_call(request: Request):
    """Endpoint para fornecer o TwiML inicial para o Twilio"""
    log_separator("NOVA REQUISIÇÃO TWIML")
    log_info("📞", f"Recebida requisição {request.method} para TwiML")
    
    # Log dos headers da requisição
    if logger.level <= logging.DEBUG:
        log_debug("📋", "Headers da requisição:")
        for header, value in request.headers.items():
            log_debug("", f"   {header}: {value}")
    
    # Log dos parâmetros da requisição
    if request.method == "POST":
        form_data = await request.form()
        if logger.level <= logging.DEBUG:
            log_debug("📝", "Dados do formulário:")
            for key, value in form_data.items():
                log_debug("", f"   {key}: {value}")
    else:
        query_params = dict(request.query_params)
        if logger.level <= logging.DEBUG:
            log_debug("❓", "Query params:")
            for key, value in query_params.items():
                log_debug("", f"   {key}: {value}")
    
    response = VoiceResponse()
    
    # Adiciona mensagem inicial
    response.say("Por favor, aguarde enquanto conectamos sua chamada ao assistente virtual.", voice='alice', language='pt-BR')
    response.pause(length=1)
    response.say("Pronto, pode começar a falar!", voice='alice', language='pt-BR')
    
    # Configura os parâmetros do stream
    connect = Connect()
    connect.stream(
        url=f"{BASE_URL}/stream",
        track="inbound_track",  # Captura apenas áudio do usuário
        max_duration=60,  # 1 minuto
        max_connections=1
    )
    
    # Adiciona o stream à resposta
    response.append(connect)
    
    twiml = str(response)
    log_info("📜", "TwiML gerado:")
    log_debug("", f"   {twiml}")
    log_separator("FIM REQUISIÇÃO TWIML")
    
    return Response(content=twiml, media_type="application/xml")

@app.websocket("/stream")
async def websocket_endpoint(websocket: WebSocket):
    log_separator("NOVA CONEXÃO WEBSOCKET")
    log_info("🔌", "Tentando aceitar conexão WebSocket...")
    await websocket.accept()
    log_info("✅", "Conexão WebSocket aceita com sucesso")
    connection_id = None
    openai_ws = None
    
    try:
        log_info("🌟", "Nova conexão WebSocket estabelecida")
        
        # Conecta ao WebSocket da OpenAI
        openai_ws = await websockets.connect(
            'wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-10-01',
            extra_headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "OpenAI-Beta": "realtime=v1"
            }
        )
        log_info("🤖", "Conectado à API OpenAI Realtime")
        
        # Inicializa a sessão OpenAI
        await initialize_session(openai_ws)
        
        while True:
            log_debug("👂", "Aguardando mensagem...")
            try:
                message = await websocket.receive_text()
                data = json.loads(message)
                
                if "event" in data:
                    event_type = data["event"]
                    log_info("📥", f"Evento recebido: {event_type}")
                    log_debug("", f"   Dados: {message[:200]}...")
                    
                    if event_type == "start":
                        connection_id = data.get("streamSid")
                        connections[connection_id] = websocket
                        log_info("🆔", f"Conexão registrada - ID: {connection_id}")
                        
                        # Aguarda um pouco antes de enviar a mensagem inicial
                        await asyncio.sleep(2)
                        
                        # Envia mensagem inicial
                        log_info("🗣️", "Enviando mensagem inicial...")
                        await stream_audio_to_twilio(
                            "Olá! Sou seu assistente virtual. Como posso ajudar?",
                            websocket,
                            connection_id
                        )
                    
                    elif event_type == "media":
                        if "media" in data:
                            audio_payload = data["media"]["payload"]
                            timestamp = data["media"].get("timestamp", 0)
                            
                            # Envia o áudio para a OpenAI
                            if openai_ws and openai_ws.open:
                                await openai_ws.send(json.dumps({
                                    "type": "input_audio_buffer.append",
                                    "audio": audio_payload
                                }))
                                log_debug("🎤", f"Áudio enviado para OpenAI (timestamp: {timestamp}ms)")
                            
                            # Processa a resposta da OpenAI
                            if openai_ws:
                                response = await openai_ws.recv()
                                response_data = json.loads(response)
                                
                                if response_data.get("type") == "response.content":
                                    content = response_data.get("content", "")
                                    if content:
                                        await stream_audio_to_twilio(
                                            content,
                                            websocket,
                                            connection_id
                                        )
                    
                    elif event_type == "stop":
                        log_info("🛑", "Evento de parada recebido")
                        break
                    
                    elif event_type == "error":
                        log_error("❌", f"Erro recebido do cliente: {data.get('error', 'Sem detalhes')}")
                
            except WebSocketDisconnect:
                log_warning("⚠️", "Cliente desconectou durante recebimento de mensagem")
                break
            except Exception as e:
                log_error("💥", f"Erro ao processar mensagem: {e}")
                break
            
    except WebSocketDisconnect:
        log_warning("⚠️", "Cliente desconectou")
    except Exception as e:
        log_error("💥", f"Erro no WebSocket: {e}")
    finally:
        if connection_id and connection_id in connections:
            del connections[connection_id]
        if openai_ws:
            await openai_ws.close()
        log_info("🔌", "Conexão WebSocket encerrada")
        log_separator("FIM CONEXÃO WEBSOCKET")

async def initialize_session(openai_ws):
    """Inicializa a sessão com a OpenAI"""
    await openai_ws.send(json.dumps({
        "type": "session.create",
        "settings": {
            "language": "pt-BR",
            "temperature": 0.7,
            "system_message": """Você é um assistente virtual amigável e prestativo.
            Mantenha suas respostas curtas e diretas para melhor experiência de voz.
            Fale em português do Brasil de forma natural."""
        }
    }))
    
    response = await openai_ws.recv()
    log_info("🔧", f"Sessão OpenAI inicializada: {response}")

async def stream_audio_to_twilio(texto: str, websocket: WebSocket, stream_sid: str):
    """Stream audio from ElevenLabs to Twilio"""
    try:
        log_info("🎵", f"Iniciando streaming de áudio: '{texto}'")
        
        audio_stream = elevenlabs_client.text_to_speech.stream(
            text=texto,
            voice_id=VOICE_ID,
            model_id="eleven_multilingual_v2",
            voice_settings={
                "stability": 0.71,
                "similarity_boost": 0.75,
                "style": 0.65,
                "use_speaker_boost": True
            },
            optimize_streaming_latency=2,
            output_format="mp3_44100_128"
        )
        
        buffer_chunks = []
        chunk_size = 0
        target_chunk_size = 16384
        chunks_enviados = 0
        
        for chunk in audio_stream:
            if isinstance(chunk, bytes):
                buffer_chunks.append(chunk)
                chunk_size += len(chunk)
                
                if chunk_size >= target_chunk_size:
                    combined_chunk = b''.join(buffer_chunks)
                    audio_payload = convert_mp3_bytes_to_g711ulaw_base64(combined_chunk)
                    
                    try:
                        await websocket.send_json({
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {"payload": audio_payload}
                        })
                        chunks_enviados += 1
                        log_debug("📤", f"Chunk #{chunks_enviados} enviado: {len(combined_chunk)} bytes")
                    except Exception as e:
                        log_error("❌", f"Erro ao enviar chunk de áudio: {e}")
                        raise
                    
                    await asyncio.sleep(0.02)  # 20ms delay para suavizar
                    
                    buffer_chunks = []
                    chunk_size = 0
        
        # Envia chunks restantes
        if buffer_chunks:
            combined_chunk = b''.join(buffer_chunks)
            audio_payload = convert_mp3_bytes_to_g711ulaw_base64(combined_chunk)
            try:
                await websocket.send_json({
                    "event": "media",
                    "streamSid": stream_sid,
                    "media": {"payload": audio_payload}
                })
                chunks_enviados += 1
                log_info("📤", f"Chunk final #{chunks_enviados} enviado: {len(combined_chunk)} bytes")
            except Exception as e:
                log_error("❌", f"Erro ao enviar chunks restantes: {e}")
                raise
        
        log_info("✅", f"Streaming de áudio concluído! Total de chunks: {chunks_enviados}")
        
    except Exception as e:
        log_error("💥", f"Erro no streaming de áudio: {e}")
        log_warning("⚠️", "Tentando método de fallback...")
        await fallback_audio_generation(texto, websocket, stream_sid)

async def fallback_audio_generation(texto: str, websocket: WebSocket, stream_sid: str):
    """Método de fallback usando geração tradicional"""
    try:
        log_info("🔄", "Usando método tradicional de geração de áudio...")
        
        audio_bytes = elevenlabs_client.text_to_speech.convert(
            text=texto,
            voice_id=VOICE_ID,
            model_id="eleven_multilingual_v2",
            voice_settings={
                "stability": 0.6,
                "similarity_boost": 0.65,
                "style": 0.65,
                "use_speaker_boost": True
            }
        )
        
        audio_payload = convert_mp3_bytes_to_g711ulaw_base64(audio_bytes)
        
        try:
            await websocket.send_json({
                "event": "media",
                "streamSid": stream_sid,
                "media": {"payload": audio_payload}
            })
            log_info("✅", "Áudio enviado com sucesso (método tradicional)!")
        except Exception as e:
            log_error("❌", f"Erro ao enviar áudio (método tradicional): {e}")
            raise
        
    except Exception as e:
        log_error("💥", f"Erro no fallback de áudio: {e}")

if __name__ == "__main__":
    log_info("🚀", "Iniciando servidor WebSocket...")
    uvicorn.run(app, host="0.0.0.0", port=8000) 