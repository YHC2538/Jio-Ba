import os

with open('cogs/graph_agent.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Update analyze_node malicious check to properly pass malicious_reason into route so we can use it
old_malicious = '''        if data.get("is_malicious"):
            return {
                "is_malicious": True,
                "malicious_reason": data.get("malicious_reason", ""),
                "route": "malicious"
            }'''
new_malicious = '''        if data.get("is_malicious"):
            return {
                "is_malicious": True,
                "malicious_reason": data.get("malicious_reason", ""),
                "route": "malicious",
                "extracted": data
            }'''
content = content.replace(old_malicious, new_malicious)


# 2. Update next_question_node formatting to include the interview form
old_next_question = '''async def next_question_node(state: InterviewState) -> InterviewState:  
    """判斷進入下一個問題或是結束"""
    next_id = next_question_id(state["current_question_id"], state["questions"], state["answers"])

    if next_id == "completed":
        return {"current_question_id": "completed", "route": "finalize"}

    # 查出下一個問題，作為 AI 的發言並存入 (模擬由系統發問)
    questions = state.get("questions", [])
    next_text = ""
    for q in questions:
        if q.get("id") == next_id:
            next_text = q.get("text", "")

    return {
        "current_question_id": next_id,
        "messages": [AIMessage(content=next_text)],
        "route": "standby"
    }'''

new_next_question = '''async def next_question_node(state: InterviewState) -> InterviewState:
    """進入下一題，並呈現填寫進度表單"""
    next_id = next_question_id(state["current_question_id"], state["questions"], state["answers"])
    is_sufficient = state.get("extracted", {}).get("is_sufficient", False)

    if next_id == "completed":
        # 結束報名或完成問卷
        return {"current_question_id": "completed", "route": "finalize"}

    questions = state.get("questions", [])
    answers = state.get("answers", {})

    lines = []
    if is_sufficient:
        lines.append("✅ **答案已記錄！**\\n")
    
    lines.append("📝 **【目前問卷進度】**")
    next_text = ""
    
    for q in questions:
        q_id = str(q.get("id"))
        text = q.get("text", "")
        if str(next_id) == q_id:
            lines.append(f"👉 **{text}** *(等待回答...)*")
            next_text = text
        elif q_id in answers:
            lines.append(f"✅ ~~{text}~~")
            lines.append(f"   **回答:** {answers[q_id]}")
        else:
            lines.append(f"⬜ {text}")

    lines.append("")
    lines.append(f"🤖 **Bot發問：** {next_text}")
    
    full_text = "\\n".join(lines)

    return {
        "current_question_id": next_id, # 更新狀態的指標到下一題
        "messages": [AIMessage(content=full_text)],
        "route": "standby"
    }'''
content = content.replace(old_next_question, new_next_question)

# 3. Update malicious_node to show exactly why they are warned and reprompt
old_malicious_node = '''async def malicious_node(state: InterviewState) -> InterviewState:      
    msg = AIMessage(content="系統警告：我們偵測到您的輸入包含不適當或違反聚餐原則的訊息，請注意您的用語。")
    return {
        "messages": [msg],
        "warning_count": state.get("warning_count", 0) + 1
    }'''

new_malicious_node = '''async def malicious_node(state: InterviewState) -> InterviewState:
    reason = state.get("malicious_reason", "言論不當")
    warning_count = state.get("warning_count", 0) + 1
    current_q_text = _get_current_question_text(state)
    
    content = f"⚠️ **系統警告 (第 {warning_count} 次)：** 我們偵測到您的回覆包含不適當的內容。\\n**判定理由：** {reason}\\n\\n請針對問題重新回答：\\n🤖 **Bot發問：** {current_q_text}"
    
    msg = AIMessage(content=content)
    return {
        "messages": [msg],
        "warning_count": warning_count
    }'''
content = content.replace(old_malicious_node, new_malicious_node)

with open('cogs/graph_agent.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("graph_agent patched")
