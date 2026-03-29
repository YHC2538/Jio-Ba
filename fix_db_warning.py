import os

with open('cogs/ai_brain.py', 'r', encoding='utf-8') as f:
    content = f.read()

old_warning_snippet = '''                         # Check warnings
                         old_warnings = initial_state.get("warning_count", 0)
                         new_warnings = final_state.get("warning_count", 0)
                         if new_warnings > old_warnings:
                             diff = new_warnings - old_warnings
                             for _ in range(diff):
                                 await db_cog.increment_participant_warning(_event_id_obj, _uid, "AI Warning")'''

new_warning_snippet = '''                         # Check warnings
                         old_warnings = initial_state.get("warning_count", 0)
                         new_warnings = final_state.get("warning_count", 0)
                         if new_warnings > old_warnings:
                             reason = final_state.get("malicious_reason") or final_state.get("extracted", {}).get("malicious_reason", "AI 判定異常回覆")
                             
                             # Identify the exact question string and user reply
                             questions = initial_state.get("questions", [])
                             c_qid = initial_state.get("current_question_id", "")
                             q_text = ""
                             for q in questions:
                                 if str(q.get("id")) == str(c_qid):
                                     q_text = str(q.get("text", ""))
                             
                             reply_text = user_inputs.get(target_uid, "")
                             
                             context_dict = {
                                 "question": q_text,
                                 "reply": reply_text,
                                 "participant_reason": reason
                             }
                             
                             diff = new_warnings - old_warnings
                             for _ in range(diff):
                                 await db_cog.increment_participant_warning(_event_id_obj, _uid, reason, context=context_dict)'''

content = content.replace(old_warning_snippet, new_warning_snippet)

with open('cogs/ai_brain.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("warning snippet patched")
