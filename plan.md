# 專案計畫書：未甲霸 (Wei Jia Ba) - AI 聚餐協調機器人

## 1. 專案概述 (Project Overview)
「未甲霸」是一個結合 **AI Agent (Gemini)** 的 Discord 機器人，旨在解決「多人聚餐協調」的痛點。
與傳統投票機器人不同，未甲霸扮演「面試官」的角色，主動私訊每一位參與者，透過自然對話釐清需求，並具備「自動踢出奧客」與「隨便條款」等機制，讓揪團吃飯變得高效且有趣。

---

## 2. 功能需求 (Functional Requirements)

### 2.1 發起聚餐 (Initiation)
*   **指令**: `/jio`
*   **發起流程**:
    1.  **選擇模式**:
        *   `手動邀請`: 發起人從使用者選單中勾選好友。
        *   `開放邀請`: 機器人在頻道發送邀請卡，任何人點擊「✋ 我要參加」按鈕即可加入。
    2.  **設定條件**:
        *   **時間 (Time)**:
            *   `獨裁時間`: 發起人直接指定 (e.g. "19:00")。
            *   `協調時間`: 設定為 "待定"，由 AI 詢問大家方便的時間。
        *   **地點/類型 (Food)**:
            *   發起人可輸入初始想法 (e.g. "台北", "日式", "都可以")。
    3.  **決定權 (Decision Mode)**:
        *   `獨裁`: 發起人看完大家需求後自己決定。
        *   `投票`: 最後產生選項讓大家投。
        *   `AI 隨機`: 機器人根據需求池直接選出一個餐廳。

### 2.2 AI 面試流程 (AI Interview Flow)
*   **觸發機制**: 當使用者被加入名單 (或點擊參加按鈕) 後，機器人主動發起 DM (私訊)。
*   **對話邏輯**:
    *   機器人具有 **Context Awareness (上下文意識)**，根據使用者的回答動態決定下一個問題。
    *   **詢問內容**:
        *   確認時間 (若為協調模式)。
        *   確認飲食偏好 (想吃什麼、不吃什麼、過敏原)。
*   **隨便條款 (The "Whatever" Clause)**:
    *   若使用者回答「隨便」、「都可以」、「看你們」等模糊詞彙。
    *   **AI 判定**: 標記該使用者 `is_whatever = True`。
    *   **後果**: 機器人會警告：「已紀錄為隨便，你將喪失最後決策的**否決權** (No Veto Power)。」
*   **難搞偵測與踢出 (Kick Logic)**:
    *   **AI 判斷標準**:
        *   設下過多限制，導致無法選擇餐廳 (e.g. "不吃肉、不吃菜、不吃澱粉")。
        *   態度消極或故意搗亂。
    *   **執行**: AI 判定 `KICK`，機器人自動將其從參與者名單移除，並通知發起人 "XXX 因為太難搞已被踢出"。

### 2.3 決策與結果 (Decision & Result)
*   **即時狀態板 (Live Dashboard)**:
    *   在公開頻道顯示 Embed，即時更新：
        *   誰加入了 (已發送邀請)。
        *   誰正在面試中。
        *   誰已經完成面試 (Ready)。
        *   誰被踢出了 (Kicked)。
*   **匯總與產出**:
    *   當所有存活的參與者都狀態為 `Ready`。
    *   **AI 匯總**: 分析所有人的對話，產出結構化的「需求限制列表」 (Constraints List)。
    *   **執行決定權**:
        *   若為 `AI 隨機`: 從符合限制的選項中隨機挑選。
        *   若為 `投票`: 生成投票選單。 (隨便黨的投票權重可能被降級或無法投反對票)。

---

## 3. 技術架構 (Technical Architecture)

### 3.1 核心組件
*   **語言**: Python 3.12
*   **框架**: `py-cord` (Discord Bot Framework)
*   **資料庫**: MongoDB (使用 `motor` 非同步驅動)
    *   選用原因: 需要儲存非結構化的對話紀錄 (Conversation History) 和動態的限制條件。
*   **AI 模型**: Google Gemini 1.5 Flash (透過 `google-generativeai`)
    *   選用原因: 速度快、成本低、具備良好的 Instruction Following 能力，適合處理即時對話與 Function Calling 決策。

### 3.2 資料庫 Schema (MongoDB)
#### `events` Collection
```json
{
  "_id": ObjectId,
  "initiator_id": 123456,
  "channel_id": 987654,
  "message_id": 112233,
  "mode": "OPEN",  // OPEN or MANUAL
  "decision_mode": "RANDOM", // DICTATOR, VOTE, RANDOM
  "description": "Dinner in Taipei",
  "active": true,
  "global_constraints": ["No Spicy"],
  "participants": [
    {
      "user_id": 999,
      "status": "INTERVIEWING", // PENDING, INTERVIEWING, READY, KICKED, DECLINED
      "constraints": ["No Beef", "After 7PM"],
      "conversation_history": [
        {"role": "model", "parts": ["Hi, what do you want to eat?"]},
        {"role": "user", "parts": ["I don't eat beef."]}
      ],
      "is_whatever": false
    }
  ]
}
```

#### `users` Collection
*   基本使用者資料、違規次數 (Strikes) 統計。

### 3.3 模組規劃 (Cogs)
1.  **`cogs/ai_brain.py`**:
    *   **核心邏輯**: 封裝 Gemini API 呼叫。
    *   **Function**: `process_reply(history, constraints)` -> 回傳 JSON Action (`ASK`, `UPDATE`, `KICK`, `FINISH`)。
2.  **`cogs/jio.py`**:
    *   **互動層**: 處理 `/jio` 指令、按鈕 (Join/Reject)、Modal (填寫資訊)。
    *   **訊息路由**: 監聽 DM 訊息，轉發給 `ai_brain` 處理，並將回應回傳給使用者。
    *   **狀態更新**: 負責編輯公開頻道的 Dashboard Embed。
3.  **`cogs/db.py`**:
    *   **資料層**: 負責所有的 MongoDB CRUD 操作。

---

## 4. 驗證與測試計畫 (Verification Plan)
1.  **環境測試**: 
    *   驗證 MongoDB 連線。
    *   驗證 Gemini API Key 有效性與回應速度。
2.  **流程測試**:
    *   **Happy Path**: 發起 -> 加入 -> 回答問題 -> 結束面試 -> 產出結果。
    *   **Edge Case**: 
        *   測試 "隨便" 關鍵字觸發。
        *   測試 "奧客" 行為觸發踢人。
        *   測試 使用者中途封鎖機器人私訊 (Error Handling)。
3.  **壓力測試**: 多人同時進行面試，驗證資料庫與 API 的併發處理能力。
