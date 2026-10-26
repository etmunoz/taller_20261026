"""Grafo ReAct del agente Colombia."""

import os

from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

from src.colombia.prompts import SYSTEM_PROMPT
from src.colombia.tools import get_tools

load_dotenv()
memory = MemorySaver()


async def get_graph():
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("Falta GOOGLE_API_KEY en .env; configura tu clave para ejecutar el agente.")
    model = ChatGoogleGenerativeAI(
        model=os.getenv("MODELO", "gemini-3.5-flash-lite"),
        google_api_key=api_key,
    )
    tools = await get_tools()
    return create_react_agent(
        model=model,
        tools=tools,
        prompt=SYSTEM_PROMPT,
        checkpointer=memory,
    )
