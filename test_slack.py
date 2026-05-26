import os
from slack_sdk import WebClient
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("SLACK_BOT_TOKEN")
CHANNEL = os.getenv("SLACK_CHANNEL_ID")
MY_USER_ID = os.getenv("MY_SLACK_USER_ID")

client = WebClient(token=TOKEN)

result = client.chat_postMessage(
    channel=CHANNEL,
    text=f"테스트: <@{MY_USER_ID}> 멘션 테스트!"
)

print("성공:", result["ts"])
