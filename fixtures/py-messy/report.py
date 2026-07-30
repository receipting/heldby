import subprocess
import anthropic

client = anthropic.Anthropic()
META = {"process": "shell-report"}


def run(spec: str) -> str:
    plan = client.messages.create(model="claude-opus-4-8", messages=[{"role": "user", "content": spec}])
    # execute-code with nothing in the way: the model chooses the command.
    return subprocess.run(plan.content[0].text, shell=True, capture_output=True).stdout.decode()
