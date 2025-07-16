from elevenlabs import ElevenLabs
from elevenlabs.conversational_ai.conversation import AudioInterface, ConversationInitiationData, Conversation
from elevenlabs.conversational_ai.default_audio_interface import DefaultAudioInterface
import time
import os
from dotenv import load_dotenv

load_dotenv()

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
AGENT_ID = os.getenv("AGENT_ID")

# Inicializa o cliente ElevenLabs com sua chave API
client = ElevenLabs(
    api_key=ELEVENLABS_API_KEY
)

# Callbacks para processar diferentes eventos da conversa
def on_agent_response(response: str):
    print(f"\nAgente: {response}")

def on_user_transcript(transcript: str):
    print(f"\nVocê disse: {transcript}")

def on_agent_response_correction(original: str, correction: str):
    print(f"\nCorreção do agente:")
    print(f"Original: {original}")
    print(f"Corrigido: {correction}")

def main():
    agent_id = AGENT_ID
    config = ConversationInitiationData()
    audio_interface = DefaultAudioInterface()
    
    # Cria uma nova instância de Conversation
    conversation = Conversation(
        client=client,
        agent_id=agent_id,
        requires_auth=True,  # A maioria dos agentes requer autenticação
        audio_interface=audio_interface,
        config=config,
        callback_agent_response=on_agent_response,
        callback_user_transcript=on_user_transcript,
        callback_agent_response_correction=on_agent_response_correction
    )
    
    try:
        print("Iniciando conversa... (Pressione Ctrl+C para sair)")
        conversation.start_session()
        
        while True:
            time.sleep(0.1)  # Evita uso intensivo da CPU
            
    except KeyboardInterrupt:
        print("\nEncerrando conversa...")
    finally:
        conversation.end_session()
        conversation.wait_for_session_end()

if __name__ == "__main__":
    main() 