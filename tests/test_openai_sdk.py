"""Test script: Uses standard OpenAI SDK — zero AEX awareness."""
from openai import OpenAI

client = OpenAI()  # Uses OPENAI_BASE_URL and OPENAI_API_KEY from env

response = client.chat.completions.create(
    model="gpt-oss-20b",
    messages=[{"role": "user", "content": "What is 2+2? Reply with just the number."}],
    max_tokens=10
)

print(f"Response: {response.choices[0].message.content}")
print(f"Model: {response.model}")
print(f"Tokens: {response.usage.total_tokens}")
