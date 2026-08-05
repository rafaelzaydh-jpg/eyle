from openai import OpenAI

client = OpenAI(
    api_key="local-sem-chave",
    base_url="http://127.0.0.1:8080/v1",
)

stream = client.chat.completions.create(
    model="qwen3.8-max",
    messages=[{"role": "user", "content": "Responda apenas: servidor funcionando"}],
    stream=True,
)

for chunk in stream:
    if not chunk.choices:
        continue
    delta = chunk.choices[0].delta
    reasoning = getattr(delta, "reasoning_content", None)
    content = getattr(delta, "content", None)
    if reasoning:
        print(reasoning, end="", flush=True)
    if content:
        print(content, end="", flush=True)
print()
