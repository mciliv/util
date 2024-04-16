import os
import sys
import json
import requests
from datetime import datetime
from pathlib import Path

url = "https://api.openai.com/v1/chat/completions"
headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {os.environ["OPENAI_API_KEY"]}"
}


def chat(data):
    data = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": data}],
        "temperature": 0.7
    }

    response = requests.post(url, headers=headers, json=data)
    return response.json()


def write(data, destination=Path.home()):
    filename = data[:16].replace(' ', '_') + str(datetime.now())
    with open(filename, 'w') as f:
        f.write(json.dumps(chat(data), indent=2))


if __name__ == "__main__":
    for path in sys.argv[1:]:
        path = Path(path)
        with open(path, 'r') as f:
            write(f.read(), path.parent)

