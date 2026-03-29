
import sys

with open("cogs/graph_agent.py", "r", encoding="utf-8") as f:
    content = f.read()

# 1. gatekeeper_node
old_gatekeeper = """    sys_msg = SystemMessage(content=f"""
new_gatekeeper = """    sys_msg = HumanMessage(content=f"""
content = content.replace("sys_msg = SystemMessage(content=", "sys_msg = HumanMessage(content=")

old_ainvoke_1 = "response = await llm.ainvoke([sys_msg, HumanMessage(content=latest_msg or \" \")])"
new_ainvoke_1 = "response = await llm.ainvoke([HumanMessage(content=sys_msg.content + \"\\n\\n[USER INPUT]:\\n\" + (latest_msg or \" \"))])"
content = content.replace(old_ainvoke_1, new_ainvoke_1)

# Note: The original gatekeeper earlier had ainvoke([sys_msg]). Let's make sure we find whatever it is.
import re
content = re.sub(
    r"response = await llm\.ainvoke\(\[sys_msg\]\)",
    "response = await llm.ainvoke([HumanMessage(content=sys_msg.content + \"\\n\\n[USER INPUT]:\\n\" + (latest_msg or \" \"))])",
    content
)

with open("cogs/graph_agent.py", "w", encoding="utf-8") as f:
    f.write(content)

print("SystemMessages replaced with HumanMessages")

