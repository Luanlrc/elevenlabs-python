from elevenlabs import ElevenLabs
from elevenlabs.conversational_ai.conversation import ConversationInitiationData, Conversation
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Connect, Start
from twilio.base.exceptions import TwilioRestException
import os
from dotenv import load_dotenv
import time
import logging
import asyncio
import threading

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

# Carrega variáveis de ambiente
load_dotenv()

# Configurações do ElevenLabs
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
AGENT_ID = os.getenv("AGENT_ID")
VOICE_ID = os.getenv("VOICE_ID")

# Configurações do Twilio
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
FROM_NUMBER = os.getenv("FROM_NUMBER")
TO_NUMBER = os.getenv("TO_NUMBER")

# URLs
WEBSOCKET_URL = os.getenv("WEBSOCKET_URL")
BASE_URL = os.getenv("BASE_URL")

# Inicializa o cliente do Twilio
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Inicializa o cliente ElevenLabs
client = ElevenLabs(api_key=ELEVENLABS_API_KEY)

class TwilioAudioInterface:
    def __init__(self, call):
        self.call = call
        self.audio_queue = asyncio.Queue()
        self.input_callback = None
        self.should_stop = False
        self.loop = asyncio.new_event_loop()
        self.output_task = None
        log_info("🎧", f"Interface de áudio inicializada - Call SID: {call.sid}")

    def start(self, input_callback):
        self.input_callback = input_callback
        self.should_stop = False
        
        # Inicia o loop de eventos em uma thread separada
        def run_event_loop():
            asyncio.set_event_loop(self.loop)
            self.loop.run_forever()
        
        self.loop_thread = threading.Thread(target=run_event_loop, daemon=True)
        self.loop_thread.start()
        
        # Inicia a tarefa de processamento de áudio
        self.output_task = asyncio.run_coroutine_threadsafe(
            self._process_audio_queue(),
            self.loop
        )
        
        log_info("🟢", "Interface de áudio iniciada")

    def stop(self):
        self.should_stop = True
        if self.output_task:
            self.output_task.cancel()
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)
        if hasattr(self, 'loop_thread'):
            self.loop_thread.join()
        log_info("🔴", "Interface de áudio encerrada")

    async def _process_audio_queue(self):
        """Processa a fila de áudio em background"""
        try:
            while not self.should_stop:
                try:
                    # Espera por novo áudio com timeout
                    audio_data = await asyncio.wait_for(self.audio_queue.get(), timeout=0.5)
                    log_debug("🎵", f"Processando áudio da fila: {len(audio_data)} bytes")
                    # Aqui você processaria o áudio...
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    log_error("❌", f"Erro ao processar áudio da fila: {e}")
                    break
        except asyncio.CancelledError:
            log_debug("🛑", "Processamento de áudio cancelado")
        except Exception as e:
            log_error("💥", f"Erro no processamento de áudio: {e}")

    def output(self, audio_data):
        """Envia áudio para a fila de processamento"""
        if not self.should_stop:
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self.audio_queue.put(audio_data),
                    self.loop
                )
                future.result(timeout=1.0)  # Espera até 1 segundo
                log_debug("📤", f"Áudio enfileirado: {len(audio_data)} bytes")
            except Exception as e:
                log_error("❌", f"Erro ao enfileirar áudio: {e}")

    def input(self, audio_data):
        """Processa áudio recebido"""
        if self.input_callback and not self.should_stop:
            try:
                asyncio.run_coroutine_threadsafe(
                    self.input_callback(audio_data),
                    self.loop
                )
                log_debug("✅", "Áudio processado com sucesso")
            except Exception as e:
                log_error("❌", f"Erro ao processar áudio: {e}")

def make_call():
    """Inicia uma chamada telefônica com o agente"""
    log_separator("NOVA CHAMADA")
    log_info("📞", f"Iniciando chamada de {FROM_NUMBER} para {TO_NUMBER}")
    
    try:
        # Verifica se todas as variáveis necessárias estão configuradas
        required_vars = {
            "ELEVENLABS_API_KEY": ELEVENLABS_API_KEY,
            "AGENT_ID": AGENT_ID,
            "VOICE_ID": VOICE_ID,
            "TWILIO_ACCOUNT_SID": TWILIO_ACCOUNT_SID,
            "TWILIO_AUTH_TOKEN": TWILIO_AUTH_TOKEN,
            "FROM_NUMBER": FROM_NUMBER,
            "TO_NUMBER": TO_NUMBER,
            "BASE_URL": BASE_URL
        }
        
        missing_vars = [var for var, value in required_vars.items() if not value]
        if missing_vars:
            raise ValueError(f"Variáveis de ambiente faltando: {', '.join(missing_vars)}")

        log_info("✅", "Todas as variáveis de ambiente configuradas")
        log_debug("📡", f"URL base: {BASE_URL}")

        # Cria a chamada usando a URL do TwiML
        log_info("📤", "Solicitando criação da chamada ao Twilio...")
        call = twilio_client.calls.create(
            to=TO_NUMBER,
            from_=FROM_NUMBER,
            url=f"{BASE_URL}/incoming-call"
        )
        log_info("✅", f"Chamada criada - SID: {call.sid}")
        
        audio_interface = TwilioAudioInterface(call)
        
        def on_agent_response(response: str):
            log_info("🤖", f"Agente disse: {response}")

        def on_user_transcript(transcript: str):
            log_info("👤", f"Usuário disse: {transcript}")

        def on_agent_response_correction(correction: str):
            log_info("🔄", f"Correção do agente: {correction}")

        def on_latency_measurement(latency_ms: int):
            log_debug("⚡", f"Latência: {latency_ms}ms")

        conversation = Conversation(
            client=client,
            agent_id=AGENT_ID,
            requires_auth=True,
            audio_interface=audio_interface,
            config=ConversationInitiationData(),
            callback_agent_response=on_agent_response,
            callback_user_transcript=on_user_transcript,
            callback_agent_response_correction=on_agent_response_correction,
            callback_latency_measurement=on_latency_measurement
        )

        log_info("🎯", "Iniciando sessão de conversa...")
        conversation.start_session()

        # Monitora o status da chamada
        while True:
            try:
                call_status = twilio_client.calls(call.sid).fetch().status
                log_debug("📊", f"Status da chamada: {call_status}")
                
                if call_status in ['completed', 'failed', 'busy', 'no-answer', 'canceled']:
                    log_info("🔚", f"Chamada finalizada - Status: {call_status}")
                    break
                    
                time.sleep(1)  # Intervalo entre verificações
                
            except TwilioRestException as e:
                log_error("❌", f"Erro ao verificar status da chamada: {e}")
                break
                
        conversation.end_session()
        log_separator("FIM DA CHAMADA")
        
    except Exception as e:
        log_error("💥", f"Erro ao fazer chamada: {e}")
        raise

if __name__ == "__main__":
    try:
        make_call()
    except KeyboardInterrupt:
        log_info("🛑", "Programa interrompido pelo usuário")
    except Exception as e:
        log_error("💥", f"Erro fatal: {e}")
        raise 