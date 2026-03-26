這是一份為開發者量身打造的 `README.md` 文件。內容融合了我們剛剛討論的 LangGraph 架構、Gemini API 串接，以及底層的機制設計數學邏輯。

你可以直接將以下內容複製並存為專案根目錄下的 `README.md`，這會讓接手開發的工程師（或是 AI Coding Agent）一目瞭然地掌握專案的全貌。

---

```markdown
# 揪霸 (Jio Ba) 🤖 - 智慧揪團與需求搓合 Discord Bot

> 「告別群組裡的七嘴八舌，讓 AI 幫你找出所有人最滿意的最佳解。」

揪霸 (Jio Ba) 是一個結合 **LLM 非同步訪談**與**機制設計 (Mechanism Design)** 的 Discord 機器人。透過 LangGraph 打造的狀態機，Jio Ba 能在一對一私訊中溫和地收集參與者的偏好與隱私限制，並利用 Nash Social Welfare (NSW) 演算法計算出 Pareto Optimal 的活動方案，大幅降低主揪的協調成本。

## ✨ 核心特色 (Features)

* **非同步深度訪談：** 利用 Google Gemini API 在背景與參與者私訊，消除群組表態的「從眾效應」。
* **精準意圖萃取：** 透過 LangGraph 將口語化的自然語言（如：「想找高 CP 值的店，但我絕對不吃羊肉爐」）精準轉換為結構化的 JSON 偏好參數。
* **防呆與防護機制：** 內建 Gatekeeper 節點阻斷 Prompt Injection，並具備偏題自動拉回 (Reprompt) 功能。
* **Nash Social Welfare 搓合引擎：** 底層採用公平分配演算法，確保最終方案兼顧整體滿意度與少數人的基本權益。
* **Discord 原生整合：** 支援 Slash Commands (`/jio`)、互動按鈕、Modals，並能自動建立 Discord Scheduled Events。

## 🧠 系統架構 (Architecture)

本專案核心對話流程由 **LangGraph** 驅動，定義了嚴謹的 `InterviewState` 傳遞於各節點：

1.  `GatekeeperNode`: 惡意指令攔截與安全過濾。
2.  `ExtractorNode`: 語意解析與 JSON 偏好提取。
3.  `ToolNode`: (規劃中) 串接 Google Places API 進行實體地點檢索。
4.  `RepromptNode`: 處理偏題與安撫使用者情緒。
5.  `NSWCalculatorNode`: Deadline 觸發，執行數學最佳化搓合。

### 數學模型：Nash Social Welfare (NSW)

有別於容易犧牲少數的多數決，或適用於不可分割物品的 EF1 演算法，Jio Ba 將公共活動視為聯合決策，透過最大化所有參與者效用 $u_i(x)$ 的乘積來尋找最佳方案 $x^*$：

$$x^* = \arg\max_{x \in X} \prod_{i=1}^{n} u_i(x)$$

*效用計算範例：* 若方案踩中參與者的絕對雷區 (Dealbreakers)，演算法將賦予極小值 $\epsilon$ 以產生嚴厲懲罰，迫使系統尋找 Pareto Optimal 的替代方案。

## 🛠️ 開發與安裝指南 (Getting Started)

### 先決條件 (Prerequisites)
* Python 3.10+
* Discord Developer Portal Bot Token
* Google Gemini API Key

### 環境建置 (Setup)

1. **複製專案 (Clone the repository)**
   ```bash
   git clone [https://github.com/yourusername/jio-ba-bot.git](https://github.com/yourusername/jio-ba-bot.git)
   cd jio-ba-bot
   ```

2. **建立虛擬環境與安裝依賴 (Install dependencies)**
   ```bash
   python -m venv venv
   source venv/bin/activate  # Windows: venv\Scripts\activate
   pip install -r requirements.txt
   ```
   *(主要套件包含：`discord.py`, `langgraph`, `langchain-google-genai`, `pydantic`, `python-dotenv`)*

3. **設定環境變數 (Environment Variables)**
   複製 `.env.example` 並重新命名為 `.env`，填入你的金鑰：
   ```env
   DISCORD_TOKEN=your_discord_bot_token_here
   GEMINI_API_KEY=your_gemini_api_key_here
   ```

4. **啟動機器人 (Run the bot)**
   ```bash
   python main.py
   ```

## 💡 使用情境範例 (Use Case Scenario)

**情境：舉辦 15 人的台北泡泡足球與會後聚餐**

1.  **發起：** 主揪在頻道輸入 `/jio 目的: 15人泡泡足球與期末聚餐 期限: 明晚8點`。
2.  **上車：** 15 位成員點擊頻道中的 [我要參加] 按鈕。
3.  **訪談：** Jio Ba 分別私訊 15 人。
    * *成員 A 說：* 「運動完想吃點高 CP 值的東西補充體力。」 ➡️ 記錄預算與風格偏好。
    * *成員 B 說：* 「我對羊肉嚴重過敏，絕對不能吃羊肉爐。」 ➡️ 記錄至 `dealbreakers`。
4.  **搓合：** 期限到達，Jio Ba 計算出 NSW 最高的分數，並向主揪提交「幕僚報告」。
5.  **定案：** 主揪點擊確認，Jio Ba 自動在頻道發佈公告並建立 Discord 官方活動。


---
*Built with ❤️ for better gathering experiences.*
```

