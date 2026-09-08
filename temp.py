import openai
from keys.API_keys import ChatgptAPI
from logic.gpt_handler import verwerk_input
from voice.tts_output import TextToSpeech

tts = TextToSpeech()
# Zet hier je eigen OpenAI API key!
openai.api_key = ChatgptAPI
def chat_met_gpt():
    while True:
        user_input = input("Jij: ")
        if user_input.lower() in ['stop', 'exit', 'quit']:
            print("👋 Tot de volgende keer!")
            break


       
        x = verwerk_input(user_input)
        print(x)
        

chat_met_gpt()
