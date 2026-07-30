from langchain_anthropic import ChatAnthropic
from langgraph.graph import StateGraph

AI_PROCESSES = {"triage": {"class": "read"}}
PROCESS: str = "triage"


def build():
    llm = ChatAnthropic(model="claude-sonnet-4-6")
    g = StateGraph(dict)
    g.add_node("Triage Analyst", lambda s: llm.invoke(s))
    g.add_node("Escalation Writer", lambda s: llm.invoke(s))
    return g
