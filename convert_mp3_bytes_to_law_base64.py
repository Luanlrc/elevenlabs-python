from pydub import AudioSegment
import audioop
from io import BytesIO
import base64

def convert_mp3_bytes_to_g711ulaw_base64(mp3_bytes: bytes) -> str:
    # Carrega MP3
    audio = AudioSegment.from_file(BytesIO(mp3_bytes), format="mp3")

    # Converte para mono, 8000Hz, 16 bits (PCM linear)
    audio = audio.set_channels(1).set_frame_rate(8000).set_sample_width(2)
    pcm_audio = audio.raw_data

    # Converte de PCM linear 16-bit para G711 u-law
    ulaw_audio = audioop.lin2ulaw(pcm_audio, 2)

    # Codifica em base64
    return base64.b64encode(ulaw_audio).decode("utf-8") 